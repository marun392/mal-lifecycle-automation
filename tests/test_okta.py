from __future__ import annotations

import pytest

from mal_lifecycle.transport import APIError


def test_create_user_posts_profile_and_activates(okta, new_hire):
    user = okta.create_user(new_hire)
    assert user["profile"]["login"] == new_hire.login
    assert user["status"] == "PROVISIONED"
    call = okta.transport.calls[-1]
    assert call.method == "POST" and call.url.endswith("/api/v1/users")
    assert call.params == {"activate": "true"}
    assert call.json["profile"]["title"] == "Software Engineer"
    assert call.headers["Authorization"].startswith("SSWS ")


def test_create_user_is_idempotent_on_duplicate_login(okta, departing):
    existing = okta.create_user(departing)
    assert existing["status"] == "ACTIVE"
    methods = [c.method for c in okta.transport.calls]
    assert methods == ["POST", "GET"]  # 400 duplicate -> fetch existing


def test_find_group_and_add_member(okta, okta_tenant, new_hire):
    user = okta.create_user(new_hire)
    group = okta.find_group("Engineering")
    assert group["profile"]["name"] == "engineering"
    okta.add_user_to_group(user["id"], group["id"])
    assert user["id"] in okta_tenant.memberships[group["id"]]
    put = okta.transport.calls[-1]
    assert put.method == "PUT" and put.url.endswith(f"/groups/{group['id']}/users/{user['id']}")


def test_find_group_missing_raises(okta):
    with pytest.raises(APIError, match="not found"):
        okta.find_group("does-not-exist")


def test_suspend_and_deactivate_change_status(okta, okta_tenant, departing):
    user = okta.get_user(departing.login)
    okta.clear_sessions(user["id"])
    okta.suspend_user(user["id"])
    assert okta_tenant.users[user["id"]]["status"] == "SUSPENDED"
    okta.deactivate_user(user["id"])
    assert okta_tenant.users[user["id"]]["status"] == "DEPROVISIONED"
    urls = [c.url for c in okta.transport.calls]
    assert any(u.endswith(f"/users/{user['id']}/sessions") for u in urls)
    assert any(u.endswith("/lifecycle/suspend") for u in urls)


def test_suspend_already_suspended_user_fails(okta, departing):
    user = okta.get_user(departing.login)
    okta.suspend_user(user["id"])
    with pytest.raises(APIError) as exc:
        okta.suspend_user(user["id"])
    assert exc.value.status_code == 400


def test_get_unknown_user_raises_404(okta):
    with pytest.raises(APIError) as exc:
        okta.get_user("nobody@example.com")
    assert exc.value.status_code == 404 and exc.value.body["errorCode"] == "E0000007"
