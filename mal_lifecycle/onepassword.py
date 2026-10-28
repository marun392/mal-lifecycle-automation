"""1Password access management.

Three 1Password APIs are involved, each with a single responsibility:

* **SCIM bridge** (``OP_SCIM_URL``) - the only API that can create users and
  change group membership.  Vault access in 1Password is granted to *groups*,
  so adding a user to the ``Engineering`` group grants the group's vaults and
  removing them from every group revokes all shared-vault access.
* **Connect API** (``OP_CONNECT_HOST``) - read-only here: resolves the shared
  vault names that back a group and records their IDs for the audit trail.
* **Events API** (``OP_EVENTS_HOST``) - read-only audit log; after offboarding
  we pull the latest audit events to confirm the suspension was recorded.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from .mockdata import SEED_EMPLOYEES, SEED_GROUPS, fake_uuid26, iso_now
from .models import Employee
from .transport import APIError, BaseClient, Call, MockTransport, Response, Transport, json_response

SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"


class OnePasswordSCIMClient(BaseClient):
    service = "1password_scim"

    def __init__(self, scim_url: str, bearer_token: str, transport: Transport):
        super().__init__(
            scim_url,
            transport,
            {
                "Authorization": f"Bearer {bearer_token}",
                "Accept": "application/scim+json",
                "Content-Type": "application/scim+json",
            },
        )

    def create_user(self, employee: Employee) -> dict[str, Any]:
        body = {
            "schemas": [SCIM_USER_SCHEMA],
            "userName": employee.login,
            "name": {"givenName": employee.first_name, "familyName": employee.last_name},
            "displayName": employee.display_name,
            "emails": [{"value": employee.login, "type": "work", "primary": True}],
            "active": True,
        }
        try:
            return self._request("POST", "/Users", json=body, expect=(201,)).json()
        except APIError as exc:
            if exc.status_code == 409:
                existing = self.find_user(employee.login)
                if existing:
                    return existing
            raise

    def find_user(self, email: str) -> dict[str, Any] | None:
        body = self._request("GET", "/Users", params={"filter": f'userName eq "{email}"'}).json()
        resources = (body or {}).get("Resources", [])
        return resources[0] if resources else None

    def find_group(self, name: str) -> dict[str, Any]:
        body = self._request("GET", "/Groups", params={"filter": f'displayName eq "{name}"'}).json()
        resources = (body or {}).get("Resources", [])
        if not resources:
            raise APIError(f"1password: group {name!r} not found", status_code=404)
        return resources[0]

    def list_groups(self) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        start, count = 1, 100
        while True:
            body = self._request("GET", "/Groups", params={"startIndex": start, "count": count}).json()
            page = (body or {}).get("Resources", [])
            groups.extend(page)
            if not page or len(groups) >= (body or {}).get("totalResults", 0):
                return groups
            start += len(page)

    def add_user_to_group(self, group_id: str, user_id: str) -> None:
        self._patch_group(group_id, {"op": "add", "path": "members", "value": [{"value": user_id}]})

    def remove_user_from_group(self, group_id: str, user_id: str) -> None:
        self._patch_group(group_id, {"op": "remove", "path": f'members[value eq "{user_id}"]'})

    def remove_user_from_all_groups(self, user_id: str) -> list[str]:
        """Remove the user from every group (and therefore every shared vault)."""
        removed = []
        for group in self.list_groups():
            if any(m.get("value") == user_id for m in group.get("members", [])):
                self.remove_user_from_group(group["id"], user_id)
                removed.append(group.get("displayName", group["id"]))
        return removed

    def suspend_user(self, user_id: str) -> dict[str, Any]:
        body = {
            "schemas": [SCIM_PATCH_SCHEMA],
            "Operations": [{"op": "replace", "value": {"active": False}}],
        }
        return self._request("PATCH", f"/Users/{user_id}", json=body, expect=(200,)).json()

    def _patch_group(self, group_id: str, operation: dict[str, Any]) -> None:
        body = {"schemas": [SCIM_PATCH_SCHEMA], "Operations": [operation]}
        self._request("PATCH", f"/Groups/{group_id}", json=body, expect=(200, 204))


class OnePasswordConnectClient(BaseClient):
    service = "1password_connect"

    def __init__(self, host: str, token: str, transport: Transport):
        super().__init__(f"{host.rstrip('/')}/v1", transport, {"Authorization": f"Bearer {token}"})

    def list_vaults(self) -> list[dict[str, Any]]:
        return self._request("GET", "/vaults", expect=(200,)).json() or []

    def resolve_vaults(self, names: Iterable[str]) -> list[dict[str, str]]:
        """Return ``{"id","name"}`` for each requested vault; raise if any is missing."""
        by_name = {v["name"].lower(): v for v in self.list_vaults()}
        resolved, missing = [], []
        for name in names:
            vault = by_name.get(name.lower())
            if vault:
                resolved.append({"id": vault["id"], "name": vault["name"]})
            else:
                missing.append(name)
        if missing:
            raise APIError(f"1password: shared vault(s) not found: {', '.join(missing)}", status_code=404)
        return resolved


class OnePasswordEventsClient(BaseClient):
    service = "1password_events"

    def __init__(self, host: str, token: str, transport: Transport):
        super().__init__(
            f"{host.rstrip('/')}/api/v1",
            transport,
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )

    def audit_events(self, *, since_minutes: int = 15, limit: int = 100) -> list[dict[str, Any]]:
        start = (datetime.now(tz=UTC) - timedelta(minutes=since_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = self._request(
            "POST", "/auditevents", json={"limit": limit, "start_time": start}, expect=(200,)
        ).json()
        return (body or {}).get("items", [])

    def events_for_user(self, user_uuid: str, *, since_minutes: int = 15) -> list[dict[str, Any]]:
        return [
            e
            for e in self.audit_events(since_minutes=since_minutes)
            if e.get("object_uuid") == user_uuid or e.get("aux_uuid") == user_uuid
        ]


# -- mock tenant -----------------------------------------------------------------

MOCK_SCIM_URL = "https://op-scim-bridge.example.com"
MOCK_CONNECT_HOST = "http://op-connect.internal:8080"
MOCK_EVENTS_HOST = "https://events.1password.com"


class OnePasswordMockTenant:
    def __init__(self, seed: Iterable[Employee] = SEED_EMPLOYEES):
        self.users: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []
        for name in SEED_GROUPS:
            self._add_group(name.title())
        self.vaults = [
            self._vault("Engineering", "Engineering shared credentials"),
            self._vault("Finance", "Finance shared credentials"),
            self._vault("Shared Infrastructure", "Cloud and infrastructure secrets"),
        ]
        for emp in seed:
            user = self._add_user(emp)
            self._join(self._group_by_name("Everyone"), user)
            group = self._group_by_name(emp.group.title())
            if group:
                self._join(group, user)

    @staticmethod
    def _vault(name: str, description: str) -> dict[str, Any]:
        return {
            "id": fake_uuid26(f"vault:{name}").lower(),
            "name": name,
            "description": description,
            "attributeVersion": 1,
            "contentVersion": 42,
            "items": 17,
            "type": "USER_CREATED",
            "createdAt": "2024-01-15T10:00:00Z",
            "updatedAt": iso_now(),
        }

    def _add_group(self, name: str) -> dict[str, Any]:
        gid = fake_uuid26(f"group:{name}")
        self.groups[gid] = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "id": gid,
            "displayName": name,
            "members": [],
            "meta": {"resourceType": "Group", "location": f"{MOCK_SCIM_URL}/Groups/{gid}"},
        }
        return self.groups[gid]

    def _group_by_name(self, name: str) -> dict[str, Any] | None:
        return next((g for g in self.groups.values() if g["displayName"].lower() == name.lower()), None)

    def _add_user(self, emp: Employee) -> dict[str, Any]:
        uid = fake_uuid26(f"user:{emp.login}")
        self.users[uid] = {
            "schemas": [SCIM_USER_SCHEMA],
            "id": uid,
            "userName": emp.login,
            "name": {"givenName": emp.first_name, "familyName": emp.last_name},
            "displayName": emp.display_name,
            "emails": [{"value": emp.login, "type": "work", "primary": True}],
            "active": True,
            "meta": {
                "resourceType": "User",
                "created": iso_now(),
                "location": f"{MOCK_SCIM_URL}/Users/{uid}",
            },
        }
        return self.users[uid]

    def _join(self, group: dict[str, Any] | None, user: dict[str, Any]) -> None:
        if group and not any(m["value"] == user["id"] for m in group["members"]):
            group["members"].append({"value": user["id"], "display": user["displayName"]})

    def _record(
        self, action: str, object_type: str, obj: dict[str, Any], aux: dict[str, Any] | None = None
    ) -> None:
        self.audit.append(
            {
                "uuid": fake_uuid26(f"audit:{len(self.audit)}:{obj['id']}:{action}"),
                "timestamp": iso_now(),
                "actor_uuid": fake_uuid26("user:scim-bridge"),
                "actor_details": {"name": "SCIM Bridge", "email": "scim@example.com"},
                "action": action,
                "object_type": object_type,
                "object_uuid": obj["id"],
                "object_details": {"name": obj.get("displayName"), "email": obj.get("userName")},
                "aux_uuid": aux["id"] if aux else None,
                "aux_details": {"name": aux.get("displayName")} if aux else None,
                "session": {
                    "uuid": fake_uuid26("session"),
                    "login_time": iso_now(),
                    "device_uuid": None,
                    "ip": "10.0.0.5",
                },
                "location": {"country": "AE", "region": "Dubai", "city": "Dubai"},
            }
        )

    @staticmethod
    def _scim_list(resources: list[dict[str, Any]], start: int = 1) -> dict[str, Any]:
        return {
            "schemas": [SCIM_LIST_SCHEMA],
            "totalResults": len(resources),
            "startIndex": start,
            "itemsPerPage": len(resources),
            "Resources": resources,
        }

    @staticmethod
    def _scim_error(status: int, detail: str, scim_type: str | None = None) -> Response:
        body: dict[str, Any] = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": str(status),
            "detail": detail,
        }
        if scim_type:
            body["scimType"] = scim_type
        return json_response(status, body)

    # -- SCIM handlers
    def scim_create_user(self, _m: re.Match[str], call: Call) -> Response:
        body = call.json or {}
        login = body.get("userName", "").lower()
        if any(u["userName"] == login for u in self.users.values()):
            return self._scim_error(409, "User already exists", "uniqueness")
        name = body.get("name", {})
        user = self._add_user(Employee(name.get("givenName", ""), name.get("familyName", ""), login))
        self._join(self._group_by_name("Everyone"), user)
        self._record("join", "user", user)
        return json_response(201, user)

    def scim_list_users(self, _m: re.Match[str], call: Call) -> Response:
        flt = (call.params or {}).get("filter", "")
        match = re.match(r'userName eq "(?P<v>[^"]+)"', flt)
        if match:
            users = [u for u in self.users.values() if u["userName"] == match.group("v").lower()]
        else:
            users = list(self.users.values())
        return json_response(200, self._scim_list(users))

    def scim_patch_user(self, m: re.Match[str], call: Call) -> Response:
        user = self.users.get(m.group("id"))
        if not user:
            return self._scim_error(404, "User not found")
        for op in (call.json or {}).get("Operations", []):
            value = op.get("value", {})
            if isinstance(value, dict) and "active" in value:
                user["active"] = bool(value["active"])
                self._record("suspend" if not user["active"] else "reactivate", "user", user)
        return json_response(200, user)

    def scim_list_groups(self, _m: re.Match[str], call: Call) -> Response:
        params = call.params or {}
        match = re.match(r'displayName eq "(?P<v>[^"]+)"', params.get("filter", ""))
        groups = list(self.groups.values())
        if match:
            groups = [g for g in groups if g["displayName"].lower() == match.group("v").lower()]
        start = int(params.get("startIndex", 1))
        count = int(params.get("count", 100))
        page = groups[start - 1 : start - 1 + count]
        body = self._scim_list(page, start)
        body["totalResults"] = len(groups)
        return json_response(200, body)

    def scim_patch_group(self, m: re.Match[str], call: Call) -> Response:
        group = self.groups.get(m.group("id"))
        if not group:
            return self._scim_error(404, "Group not found")
        for op in (call.json or {}).get("Operations", []):
            if op.get("op") == "add":
                for member in op.get("value", []):
                    user = self.users.get(member.get("value"))
                    if not user:
                        return self._scim_error(400, "Member not found", "invalidValue")
                    self._join(group, user)
                    self._record("join", "group", group, user)
            elif op.get("op") == "remove":
                match = re.search(r'members\[value eq "(?P<uid>[^"]+)"\]', op.get("path", ""))
                if not match:
                    return self._scim_error(400, "Unsupported remove path", "invalidPath")
                uid = match.group("uid")
                group["members"] = [mm for mm in group["members"] if mm["value"] != uid]
                if uid in self.users:
                    self._record("leave", "group", group, self.users[uid])
        return json_response(200, group)

    # -- Connect handlers
    def connect_list_vaults(self, _m: re.Match[str], _call: Call) -> Response:
        return json_response(200, self.vaults)

    # -- Events handlers
    def events_audit(self, _m: re.Match[str], call: Call) -> Response:
        limit = int((call.json or {}).get("limit", 100))
        items = self.audit[-limit:]
        return json_response(200, {"items": items, "cursor": "mock-cursor", "has_more": False})

    def install(self, transport: MockTransport) -> MockTransport:
        scim = re.escape(MOCK_SCIM_URL)
        transport.add("POST", rf"^{scim}/Users$", self.scim_create_user)
        transport.add("GET", rf"^{scim}/Users$", self.scim_list_users)
        transport.add("PATCH", rf"^{scim}/Users/(?P<id>[^/]+)$", self.scim_patch_user)
        transport.add("GET", rf"^{scim}/Groups$", self.scim_list_groups)
        transport.add("PATCH", rf"^{scim}/Groups/(?P<id>[^/]+)$", self.scim_patch_group)
        transport.add("GET", rf"^{re.escape(MOCK_CONNECT_HOST)}/v1/vaults$", self.connect_list_vaults)
        transport.add("POST", rf"^{re.escape(MOCK_EVENTS_HOST)}/api/v1/auditevents$", self.events_audit)
        return transport


class OnePasswordClients:
    """Bundle of the 1Password API clients the workflow needs (Connect/Events optional)."""

    def __init__(
        self,
        scim: OnePasswordSCIMClient,
        connect: OnePasswordConnectClient | None = None,
        events: OnePasswordEventsClient | None = None,
    ):
        self.scim = scim
        self.connect = connect
        self.events = events


def mock_onepassword_clients(tenant: OnePasswordMockTenant | None = None) -> OnePasswordClients:
    tenant = tenant or OnePasswordMockTenant()
    transport = tenant.install(MockTransport(label="1password-mock"))
    return OnePasswordClients(
        OnePasswordSCIMClient(MOCK_SCIM_URL, "mock-scim-token", transport),
        OnePasswordConnectClient(MOCK_CONNECT_HOST, "mock-connect-token", transport),
        OnePasswordEventsClient(MOCK_EVENTS_HOST, "mock-events-token", transport),
    )
