from __future__ import annotations

import pytest

from mal_lifecycle.onepassword import SCIM_PATCH_SCHEMA
from mal_lifecycle.transport import APIError


def test_scim_create_user_and_find(onepassword, new_hire):
    scim = onepassword.scim
    user = scim.create_user(new_hire)
    assert user["userName"] == new_hire.login and user["active"] is True
    call = scim.transport.calls[-1]
    assert call.headers["Content-Type"] == "application/scim+json"
    assert call.json["emails"][0]["primary"] is True
    assert scim.find_user(new_hire.login)["id"] == user["id"]
    assert scim.find_user("ghost@example.com") is None


def test_scim_create_existing_user_returns_existing(onepassword, departing):
    user = onepassword.scim.create_user(departing)
    assert user["userName"] == departing.login
    assert [c.method for c in onepassword.scim.transport.calls] == ["POST", "GET"]


def test_add_user_to_group_sends_scim_patch(onepassword, op_tenant, new_hire):
    scim = onepassword.scim
    user = scim.create_user(new_hire)
    group = scim.find_group("Engineering")
    scim.add_user_to_group(group["id"], user["id"])
    patch = scim.transport.calls[-1]
    assert patch.method == "PATCH" and patch.url.endswith(f"/Groups/{group['id']}")
    assert patch.json["schemas"] == [SCIM_PATCH_SCHEMA]
    assert patch.json["Operations"][0] == {"op": "add", "path": "members", "value": [{"value": user["id"]}]}
    assert any(m["value"] == user["id"] for m in op_tenant.groups[group["id"]]["members"])


def test_find_group_missing_raises(onepassword):
    with pytest.raises(APIError, match="not found"):
        onepassword.scim.find_group("Nope")


def test_remove_user_from_all_groups_and_suspend(onepassword, op_tenant, departing):
    scim = onepassword.scim
    user = scim.find_user(departing.login)
    removed = scim.remove_user_from_all_groups(user["id"])
    assert sorted(removed) == ["Engineering", "Everyone"]
    assert all(user["id"] not in [m["value"] for m in g["members"]] for g in op_tenant.groups.values())
    suspended = scim.suspend_user(user["id"])
    assert suspended["active"] is False
    patch = scim.transport.calls[-1]
    assert patch.json["Operations"][0]["value"] == {"active": False}


def test_connect_resolve_vaults(onepassword):
    vaults = onepassword.connect.resolve_vaults(["engineering", "Shared Infrastructure"])
    assert [v["name"] for v in vaults] == ["Engineering", "Shared Infrastructure"]
    with pytest.raises(APIError, match="Missing Vault"):
        onepassword.connect.resolve_vaults(["Missing Vault"])


def test_events_api_filters_user_audit_events(onepassword, departing):
    scim = onepassword.scim
    user = scim.find_user(departing.login)
    scim.suspend_user(user["id"])
    events = onepassword.events.events_for_user(user["id"])
    assert [e["action"] for e in events] == ["suspend"]
    body = onepassword.events.transport.calls[-1].json
    assert body["limit"] == 100 and body["start_time"].endswith("Z")
