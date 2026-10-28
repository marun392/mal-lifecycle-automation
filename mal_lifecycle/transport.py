"""HTTP transport layer.

Every API client in this package talks to the outside world through a
``Transport``.  ``LiveTransport`` wraps :mod:`requests`; ``MockTransport`` routes
requests to in-process fixture handlers.  Clients are identical in both modes,
so the same code path is exercised by the mocks, by the unit tests and in
production.
"""

from __future__ import annotations

import json as _json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests

log = logging.getLogger(__name__)

_SENSITIVE_QUERY = re.compile(r"(token|secret|password|key)=[^&]+", re.IGNORECASE)


class APIError(Exception):
    """Raised when a remote (or mocked) API returns an unexpected response."""

    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass
class Response:
    status_code: int
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        if not self.text:
            return None
        return _json.loads(self.text)


@dataclass
class Call:
    """A recorded outbound request (used by ``MockTransport`` and tests)."""

    method: str
    url: str
    headers: dict[str, str]
    params: dict[str, Any] | None
    json: Any
    data: Any
    auth: tuple[str, str] | None = None


class Transport(Protocol):
    name: str

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        auth: tuple[str, str] | None = None,
        timeout: float = 30.0,
    ) -> Response: ...


def redact_url(url: str) -> str:
    return _SENSITIVE_QUERY.sub(r"\1=***", url)


class LiveTransport:
    """Real HTTP via :mod:`requests`."""

    name = "live"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        auth: tuple[str, str] | None = None,
        timeout: float = 30.0,
    ) -> Response:
        log.debug("[live] %s %s", method.upper(), redact_url(url))
        resp = self.session.request(
            method.upper(),
            url,
            headers=dict(headers or {}),
            params=dict(params or {}),
            json=json,
            data=data,
            auth=auth,
            timeout=timeout,
        )
        return Response(resp.status_code, resp.text, dict(resp.headers))


Handler = Callable[[re.Match[str], Call], Response]


@dataclass
class Route:
    method: str
    pattern: re.Pattern[str]
    handler: Handler


def json_response(status: int, body: Any, headers: Mapping[str, str] | None = None) -> Response:
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    return Response(status, _json.dumps(body), hdrs)


def text_response(status: int, text: str = "", content_type: str = "text/plain") -> Response:
    return Response(status, text, {"Content-Type": content_type})


class MockTransport:
    """Routes requests to fixture handlers instead of the network.

    Routes are matched in insertion order against ``METHOD`` + a regular
    expression searched against the full URL (without query string).  Handlers
    receive the regex match and the recorded :class:`Call`.
    """

    name = "mock"

    def __init__(self, routes: Iterable[Route] | None = None, label: str = "mock"):
        self.routes: list[Route] = list(routes or [])
        self.calls: list[Call] = []
        self.label = label

    def add(self, method: str, pattern: str, handler: Handler, *, first: bool = False) -> None:
        route = Route(method.upper(), re.compile(pattern), handler)
        if first:
            self.routes.insert(0, route)
        else:
            self.routes.append(route)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        auth: tuple[str, str] | None = None,
        timeout: float = 30.0,
    ) -> Response:
        call = Call(
            method.upper(),
            url,
            dict(headers or {}),
            dict(params) if params else None,
            json,
            data,
            auth,
        )
        self.calls.append(call)
        for route in self.routes:
            if route.method != call.method:
                continue
            match = route.pattern.search(url)
            if match:
                resp = route.handler(match, call)
                log.info("[%s] %s %s -> %s", self.label, call.method, redact_url(url), resp.status_code)
                return resp
        raise APIError(f"{self.label}: no mock route for {call.method} {redact_url(url)}")


class BaseClient:
    """Shared plumbing for API clients: URL building, auth headers, error handling."""

    service = "base"

    def __init__(
        self,
        base_url: str,
        transport: Transport,
        default_headers: Mapping[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.default_headers = dict(default_headers or {})

    def _auth_headers(self) -> dict[str, str]:
        """Override to compute per-request auth headers (e.g. refreshing OAuth tokens)."""
        return {}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        expect: Iterable[int] = (200, 201, 202, 204),
    ) -> Response:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        hdrs = {**self.default_headers, **self._auth_headers(), **(headers or {})}
        resp = self.transport.request(
            method, url, headers=hdrs, params=params, json=json, data=data, auth=auth
        )
        if resp.status_code not in set(expect):
            body: Any
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise APIError(
                f"{self.service}: {method.upper()} {redact_url(url)} returned "
                f"HTTP {resp.status_code}: {_summarise(body)}",
                status_code=resp.status_code,
                body=body,
            )
        return resp


def _summarise(body: Any, limit: int = 300) -> str:
    text = body if isinstance(body, str) else _json.dumps(body)
    return text if len(text) <= limit else text[:limit] + "..."
