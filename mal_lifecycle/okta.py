"""Okta user lifecycle via the Okta Users / Groups management API.

Live mode uses an SSWS API token against ``https://<OKTA_DOMAIN>/api/v1``.
Mock mode is a small stateful in-memory Okta tenant with realistic payloads.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .mockdata import OKTA_MOCK_DOMAIN, SEED_EMPLOYEES, SEED_GROUPS, fake_id, iso_now
from .models import Employee
from .transport import APIError, BaseClient, Call, MockTransport, Response, Transport, json_response


class OktaClient(BaseClient):
    service = "okta"

    def __init__(self, domain: str, api_token: str, transport: Transport):
        super().__init__(
            f"https://{domain}/api/v1",
            transport,
            {
                "Authorization": f"SSWS {api_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    # -- users -----------------------------------------------------------------

    def create_user(self, employee: Employee, *, activate: bool = True) -> dict[str, Any]:
        """Create (and by default activate) a user.  Idempotent on existing login."""
        profile = {
            "firstName": employee.first_name,
            "lastName": employee.last_name,
            "email": employee.email,
            "login": employee.login,
        }
        if employee.title:
            profile["title"] = employee.title
        if employee.department:
            profile["department"] = employee.department
        try:
            resp = self._request(
                "POST",
                "/users",
                params={"activate": "true" if activate else "false"},
                json={"profile": profile},
                expect=(200,),
            )
        except APIError as exc:
            if exc.status_code == 400 and _is_duplicate_login(exc.body):
                return self.get_user(employee.login)
            raise
        return resp.json()

    def get_user(self, login_or_id: str) -> dict[str, Any]:
        return self._request("GET", f"/users/{login_or_id}", expect=(200,)).json()

    def clear_sessions(self, user_id: str) -> None:
        """Revoke all of the user's Okta sessions (forces re-auth everywhere)."""
        self._request("DELETE", f"/users/{user_id}/sessions", expect=(204,))

    def suspend_user(self, user_id: str) -> dict[str, Any]:
        resp = self._request("POST", f"/users/{user_id}/lifecycle/suspend", expect=(200,))
        return resp.json() or {}

    def deactivate_user(self, user_id: str, *, send_email: bool = False) -> dict[str, Any]:
        resp = self._request(
            "POST",
            f"/users/{user_id}/lifecycle/deactivate",
            params={"sendEmail": "true" if send_email else "false"},
            expect=(200,),
        )
        return resp.json() or {}

    # -- groups ----------------------------------------------------------------

    def find_group(self, name: str) -> dict[str, Any]:
        groups = self._request("GET", "/groups", params={"q": name, "limit": 20}).json() or []
        for group in groups:
            if group.get("profile", {}).get("name", "").lower() == name.lower():
                return group
        raise APIError(f"okta: group {name!r} not found", status_code=404)

    def add_user_to_group(self, user_id: str, group_id: str) -> None:
        self._request("PUT", f"/groups/{group_id}/users/{user_id}", expect=(204,))

    def remove_user_from_group(self, user_id: str, group_id: str) -> None:
        self._request("DELETE", f"/groups/{group_id}/users/{user_id}", expect=(204,))


def _is_duplicate_login(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    causes = " ".join(c.get("errorSummary", "") for c in body.get("errorCauses", []))
    return "already exists" in (body.get("errorSummary", "") + causes).lower()


# -- mock tenant ---------------------------------------------------------------


class OktaMockTenant:
    """In-memory Okta org used by the mock transport."""

    def __init__(self, domain: str = OKTA_MOCK_DOMAIN, seed: Iterable[Employee] = SEED_EMPLOYEES):
        self.domain = domain
        self.users: dict[str, dict[str, Any]] = {}  # keyed by id
        self.groups: dict[str, dict[str, Any]] = {}  # keyed by id
        self.memberships: dict[str, set[str]] = {}  # group id -> user ids
        for name in SEED_GROUPS:
            self._add_group(name)
        for emp in seed:
            user = self._add_user(emp, status="ACTIVE")
            self._join(self._group_by_name("everyone")["id"], user["id"])
            group = self._group_by_name(emp.group)
            if group:
                self._join(group["id"], user["id"])

    # -- helpers
    def _add_group(self, name: str) -> dict[str, Any]:
        gid = fake_id("00g", f"group:{name}")
        self.groups[gid] = {
            "id": gid,
            "created": "2024-01-15T10:00:00.000Z",
            "lastUpdated": "2024-01-15T10:00:00.000Z",
            "lastMembershipUpdated": iso_now(),
            "objectClass": ["okta:user_group"],
            "type": "OKTA_GROUP",
            "profile": {"name": name, "description": f"{name.title()} team"},
            "_links": {"self": {"href": f"https://{self.domain}/api/v1/groups/{gid}"}},
        }
        self.memberships[gid] = set()
        return self.groups[gid]

    def _group_by_name(self, name: str) -> dict[str, Any] | None:
        for g in self.groups.values():
            if g["profile"]["name"].lower() == name.lower():
                return g
        return None

    def _add_user(self, emp: Employee, status: str) -> dict[str, Any]:
        uid = fake_id("00u", emp.login)
        now = iso_now()
        user = {
            "id": uid,
            "status": status,
            "created": now,
            "activated": now if status != "STAGED" else None,
            "statusChanged": now,
            "lastLogin": None,
            "lastUpdated": now,
            "passwordChanged": None,
            "type": {"id": "otyfnjfba4ye7pgjB0g4"},
            "profile": {
                "firstName": emp.first_name,
                "lastName": emp.last_name,
                "mobilePhone": None,
                "secondEmail": None,
                "login": emp.login,
                "email": emp.email,
                "title": emp.title,
                "department": emp.department,
            },
            "credentials": {"provider": {"type": "OKTA", "name": "OKTA"}},
            "_links": {"self": {"href": f"https://{self.domain}/api/v1/users/{uid}"}},
        }
        self.users[uid] = user
        return user

    def _join(self, gid: str, uid: str) -> None:
        self.memberships[gid].add(uid)

    def _find_user(self, key: str) -> dict[str, Any] | None:
        if key in self.users:
            return self.users[key]
        for u in self.users.values():
            if u["profile"]["login"].lower() == key.lower():
                return u
        return None

    # -- handlers
    def create_user(self, _m: re.Match[str], call: Call) -> Response:
        profile = (call.json or {}).get("profile", {})
        login = profile.get("login", "").lower()
        if self._find_user(login):
            return json_response(
                400,
                {
                    "errorCode": "E0000001",
                    "errorSummary": "Api validation failed: login",
                    "errorLink": "E0000001",
                    "errorId": "oaeX1RR_b8pQzKb2hYc7TdMlw",
                    "errorCauses": [
                        {
                            "errorSummary": "login: An object with this field already exists "
                            "in the current organization"
                        }
                    ],
                },
            )
        activate = (call.params or {}).get("activate") == "true"
        emp = Employee(
            profile.get("firstName", ""),
            profile.get("lastName", ""),
            profile.get("email", login),
            title=profile.get("title"),
            department=profile.get("department"),
        )
        user = self._add_user(emp, status="PROVISIONED" if activate else "STAGED")
        self._join(self._group_by_name("everyone")["id"], user["id"])
        return json_response(200, user)

    def get_user(self, m: re.Match[str], _call: Call) -> Response:
        user = self._find_user(m.group("key"))
        if not user:
            return json_response(404, _not_found(m.group("key")))
        return json_response(200, user)

    def clear_sessions(self, m: re.Match[str], _call: Call) -> Response:
        if not self._find_user(m.group("key")):
            return json_response(404, _not_found(m.group("key")))
        return Response(204)

    def lifecycle(self, m: re.Match[str], _call: Call) -> Response:
        user = self._find_user(m.group("key"))
        if not user:
            return json_response(404, _not_found(m.group("key")))
        op = m.group("op")
        if op == "suspend":
            if user["status"] != "ACTIVE":
                return json_response(
                    400,
                    {
                        "errorCode": "E0000001",
                        "errorSummary": "Api validation failed: suspendUser",
                        "errorCauses": [{"errorSummary": "Cannot suspend a user that is not active"}],
                    },
                )
            user["status"] = "SUSPENDED"
        elif op == "deactivate":
            user["status"] = "DEPROVISIONED"
        elif op == "unsuspend":
            user["status"] = "ACTIVE"
        else:
            return json_response(404, {"errorCode": "E0000007", "errorSummary": "Not found"})
        user["statusChanged"] = iso_now()
        return json_response(200, {})

    def list_groups(self, _m: re.Match[str], call: Call) -> Response:
        q = (call.params or {}).get("q", "").lower()
        groups = [g for g in self.groups.values() if q in g["profile"]["name"].lower()]
        return json_response(200, groups)

    def group_membership(self, m: re.Match[str], call: Call) -> Response:
        gid, uid = m.group("gid"), m.group("uid")
        if gid not in self.groups or uid not in self.users:
            return json_response(404, _not_found(uid))
        if call.method == "PUT":
            self.memberships[gid].add(uid)
        else:
            self.memberships[gid].discard(uid)
        return Response(204)

    def install(self, transport: MockTransport) -> MockTransport:
        base = rf"https://{re.escape(self.domain)}/api/v1"
        transport.add("POST", rf"^{base}/users$", self.create_user)
        transport.add("GET", rf"^{base}/users/(?P<key>[^/]+)$", self.get_user)
        transport.add("DELETE", rf"^{base}/users/(?P<key>[^/]+)/sessions$", self.clear_sessions)
        transport.add("POST", rf"^{base}/users/(?P<key>[^/]+)/lifecycle/(?P<op>\w+)$", self.lifecycle)
        transport.add("GET", rf"^{base}/groups$", self.list_groups)
        transport.add("PUT", rf"^{base}/groups/(?P<gid>[^/]+)/users/(?P<uid>[^/]+)$", self.group_membership)
        transport.add(
            "DELETE", rf"^{base}/groups/(?P<gid>[^/]+)/users/(?P<uid>[^/]+)$", self.group_membership
        )
        return transport


def _not_found(key: str) -> dict[str, Any]:
    return {
        "errorCode": "E0000007",
        "errorSummary": f"Not found: Resource not found: {key} (User)",
        "errorLink": "E0000007",
        "errorId": "oaeHhU0bHc9SbCv4VJ4D3Yh4w",
        "errorCauses": [],
    }


def mock_okta_client(tenant: OktaMockTenant | None = None) -> OktaClient:
    tenant = tenant or OktaMockTenant()
    transport = tenant.install(MockTransport(label="okta-mock"))
    return OktaClient(tenant.domain, "mock-ssws-token", transport)
