from __future__ import annotations

import pytest

from mal_lifecycle.gws import generate_temp_password
from mal_lifecycle.transport import APIError


def test_create_user_sends_temp_password_and_bearer(gws, new_hire):
    user = gws.create_user(new_hire, org_unit_path="/Engineering")
    assert user["primaryEmail"] == new_hire.login and user["orgUnitPath"] == "/Engineering"
    call = gws.transport.calls[-1]
    assert call.headers["Authorization"] == "Bearer mock-access-token"
    assert call.json["changePasswordAtNextLogin"] is True
    assert len(call.json["password"]) >= 16
    assert call.json["organizations"][0]["title"] == "Software Engineer"


def test_create_user_existing_returns_existing(gws, departing):
    user = gws.create_user(departing)
    assert user["primaryEmail"] == departing.login
    assert [c.method for c in gws.transport.calls] == ["POST", "GET"]


def test_revoke_all_tokens_deletes_each_token(gws, gws_directory, departing):
    revoked = gws.revoke_all_tokens(departing.login)
    assert {r["app"] for r in revoked} == {"Slack", "Zoom", "Google Cloud SDK"}
    deletes = [c for c in gws.transport.calls if c.method == "DELETE"]
    assert len(deletes) == 3
    assert all(f"/users/{departing.login}/tokens/" in c.url for c in deletes)
    assert gws.list_tokens(departing.login) == []


def test_sign_out_and_suspend(gws, gws_directory, departing):
    gws.sign_out(departing.login)
    user = gws.suspend_user(departing.login)
    assert user["suspended"] is True
    assert gws_directory.users[departing.login]["suspended"] is True
    assert gws.transport.calls[0].url.endswith("/signOut")
    assert gws.transport.calls[1].method == "PUT" and gws.transport.calls[1].json == {"suspended": True}


def test_unknown_user_raises(gws):
    with pytest.raises(APIError) as exc:
        gws.list_tokens("ghost@example.com")
    assert exc.value.status_code == 404


def test_generate_temp_password_is_random_and_long():
    a, b = generate_temp_password(), generate_temp_password()
    assert a != b and len(a) == 20
