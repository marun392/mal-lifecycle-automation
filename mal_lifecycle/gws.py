"""Google Workspace provisioning via the Admin SDK Directory API (REST).

Live mode authenticates with a service account that has domain-wide delegation
and impersonates a Workspace admin (``GWS_ADMIN_EMAIL``), or with a short-lived
``GWS_ACCESS_TOKEN``.  Mock mode is a stateful in-memory directory.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Iterable
from typing import Any

from .mockdata import MOCK_DOMAIN, SEED_EMPLOYEES, fake_id, iso_now
from .models import Employee
from .transport import APIError, BaseClient, Call, MockTransport, Response, Transport, json_response

DIRECTORY_BASE = "https://admin.googleapis.com/admin/directory/v1"
SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.user",
    "https://www.googleapis.com/auth/admin.directory.user.security",
]

TokenProvider = Callable[[], str]


def generate_temp_password(length: int = 20) -> str:
    return secrets.token_urlsafe(length)[:length]


def service_account_token_provider(service_account_file: str, admin_email: str) -> TokenProvider:
    """Build a token provider from a domain-wide-delegated service account."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        service_account_file, scopes=SCOPES, subject=admin_email
    )

    def provider() -> str:
        if not creds.valid:
            creds.refresh(Request())
        return creds.token

    return provider


class GoogleWorkspaceClient(BaseClient):
    service = "google_workspace"

    def __init__(self, token_provider: TokenProvider, transport: Transport, base_url: str = DIRECTORY_BASE):
        super().__init__(base_url, transport, {"Accept": "application/json"})
        self._token_provider = token_provider

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider()}"}

    def create_user(
        self,
        employee: Employee,
        *,
        password: str | None = None,
        org_unit_path: str = "/",
    ) -> dict[str, Any]:
        """Create a user with a temporary password they must change at first login."""
        body = {
            "primaryEmail": employee.login,
            "name": {"givenName": employee.first_name, "familyName": employee.last_name},
            "password": password or generate_temp_password(),
            "changePasswordAtNextLogin": True,
            "orgUnitPath": org_unit_path,
        }
        if employee.title or employee.department:
            body["organizations"] = [
                {"title": employee.title, "department": employee.department, "primary": True}
            ]
        try:
            return self._request("POST", "/users", json=body, expect=(200, 201)).json()
        except APIError as exc:
            if exc.status_code == 409:
                return self.get_user(employee.login)
            raise

    def get_user(self, user_key: str) -> dict[str, Any]:
        return self._request("GET", f"/users/{user_key}", expect=(200,)).json()

    def list_tokens(self, user_key: str) -> list[dict[str, Any]]:
        body = self._request("GET", f"/users/{user_key}/tokens", expect=(200,)).json() or {}
        return body.get("items", [])

    def revoke_token(self, user_key: str, client_id: str) -> None:
        self._request("DELETE", f"/users/{user_key}/tokens/{client_id}", expect=(204,))

    def revoke_all_tokens(self, user_key: str) -> list[dict[str, str]]:
        """Revoke every OAuth token the user has granted to third-party apps."""
        revoked = []
        for token in self.list_tokens(user_key):
            self.revoke_token(user_key, token["clientId"])
            revoked.append({"clientId": token["clientId"], "app": token.get("displayText", "")})
        return revoked

    def sign_out(self, user_key: str) -> None:
        """Invalidate all web and device sessions."""
        self._request("POST", f"/users/{user_key}/signOut", expect=(204,))

    def suspend_user(self, user_key: str) -> dict[str, Any]:
        return self._request("PUT", f"/users/{user_key}", json={"suspended": True}, expect=(200,)).json()


# -- mock directory -------------------------------------------------------------

_SEED_TOKENS: list[dict[str, Any]] = [
    {
        "kind": "admin#directory#token",
        "clientId": "77185425430.apps.googleusercontent.com",
        "displayText": "Slack",
        "anonymous": False,
        "nativeApp": False,
        "scopes": [
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/calendar.readonly",
            "openid",
        ],
    },
    {
        "kind": "admin#directory#token",
        "clientId": "1062185365062-kqc8f6l0m3p4u5r7tgu7v9kg0ndrc3nm.apps.googleusercontent.com",
        "displayText": "Zoom",
        "anonymous": False,
        "nativeApp": False,
        "scopes": [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/userinfo.email",
        ],
    },
    {
        "kind": "admin#directory#token",
        "clientId": "32555940559.apps.googleusercontent.com",
        "displayText": "Google Cloud SDK",
        "anonymous": False,
        "nativeApp": True,
        "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
    },
]


class GoogleWorkspaceMockDirectory:
    def __init__(self, domain: str = MOCK_DOMAIN, seed: Iterable[Employee] = SEED_EMPLOYEES):
        self.domain = domain
        self.users: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, list[dict[str, Any]]] = {}
        for emp in seed:
            self._add_user(emp)
            self.tokens[emp.login] = [
                {**t, "etag": '"mock-etag"', "userKey": self.users[emp.login]["id"]} for t in _SEED_TOKENS
            ]

    def _add_user(self, emp: Employee, org_unit_path: str = "/") -> dict[str, Any]:
        user = {
            "kind": "admin#directory#user",
            "id": fake_id("1", emp.login, 20),
            "etag": '"mock-etag"',
            "primaryEmail": emp.login,
            "name": {
                "givenName": emp.first_name,
                "familyName": emp.last_name,
                "fullName": emp.display_name,
            },
            "isAdmin": False,
            "isDelegatedAdmin": False,
            "lastLoginTime": "1970-01-01T00:00:00.000Z",
            "creationTime": iso_now(),
            "agreedToTerms": False,
            "suspended": False,
            "archived": False,
            "changePasswordAtNextLogin": True,
            "ipWhitelisted": False,
            "emails": [{"address": emp.login, "primary": True}],
            "customerId": "C03az79cb",
            "orgUnitPath": org_unit_path,
            "isMailboxSetup": True,
            "includeInGlobalAddressList": True,
        }
        self.users[emp.login] = user
        self.tokens.setdefault(emp.login, [])
        return user

    def _lookup(self, key: str) -> dict[str, Any] | None:
        key = key.lower()
        if key in self.users:
            return self.users[key]
        return next((u for u in self.users.values() if u["id"] == key), None)

    # handlers
    def create_user(self, _m: re.Match[str], call: Call) -> Response:
        body = call.json or {}
        email = body.get("primaryEmail", "").lower()
        if self._lookup(email):
            return json_response(409, _gerror(409, "Entity already exists.", "duplicate"))
        if "password" not in body:
            return json_response(400, _gerror(400, "Invalid Password", "invalid"))
        name = body.get("name", {})
        emp = Employee(name.get("givenName", ""), name.get("familyName", ""), email)
        return json_response(200, self._add_user(emp, body.get("orgUnitPath", "/")))

    def get_user(self, m: re.Match[str], _call: Call) -> Response:
        user = self._lookup(m.group("key"))
        if not user:
            return json_response(404, _gerror(404, "Resource Not Found: userKey", "notFound"))
        return json_response(200, user)

    def update_user(self, m: re.Match[str], call: Call) -> Response:
        user = self._lookup(m.group("key"))
        if not user:
            return json_response(404, _gerror(404, "Resource Not Found: userKey", "notFound"))
        user.update(call.json or {})
        return json_response(200, user)

    def list_tokens(self, m: re.Match[str], _call: Call) -> Response:
        user = self._lookup(m.group("key"))
        if not user:
            return json_response(404, _gerror(404, "Resource Not Found: userKey", "notFound"))
        return json_response(
            200,
            {
                "kind": "admin#directory#tokenList",
                "etag": '"mock-etag"',
                "items": list(self.tokens.get(user["primaryEmail"], [])),
            },
        )

    def delete_token(self, m: re.Match[str], _call: Call) -> Response:
        user = self._lookup(m.group("key"))
        if not user:
            return json_response(404, _gerror(404, "Resource Not Found: userKey", "notFound"))
        tokens = self.tokens.get(user["primaryEmail"], [])
        remaining = [t for t in tokens if t["clientId"] != m.group("client")]
        if len(remaining) == len(tokens):
            return json_response(404, _gerror(404, "Resource Not Found: clientId", "notFound"))
        self.tokens[user["primaryEmail"]] = remaining
        return Response(204)

    def sign_out(self, m: re.Match[str], _call: Call) -> Response:
        if not self._lookup(m.group("key")):
            return json_response(404, _gerror(404, "Resource Not Found: userKey", "notFound"))
        return Response(204)

    def install(self, transport: MockTransport) -> MockTransport:
        base = re.escape(DIRECTORY_BASE)
        transport.add("POST", rf"^{base}/users$", self.create_user)
        transport.add("GET", rf"^{base}/users/(?P<key>[^/]+)$", self.get_user)
        transport.add("PUT", rf"^{base}/users/(?P<key>[^/]+)$", self.update_user)
        transport.add("GET", rf"^{base}/users/(?P<key>[^/]+)/tokens$", self.list_tokens)
        transport.add("DELETE", rf"^{base}/users/(?P<key>[^/]+)/tokens/(?P<client>[^/]+)$", self.delete_token)
        transport.add("POST", rf"^{base}/users/(?P<key>[^/]+)/signOut$", self.sign_out)
        return transport


def _gerror(code: int, message: str, reason: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "errors": [{"message": message, "domain": "global", "reason": reason}],
        }
    }


def mock_gws_client(directory: GoogleWorkspaceMockDirectory | None = None) -> GoogleWorkspaceClient:
    directory = directory or GoogleWorkspaceMockDirectory()
    transport = directory.install(MockTransport(label="gws-mock"))
    return GoogleWorkspaceClient(lambda: "mock-access-token", transport)
