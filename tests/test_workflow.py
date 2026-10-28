from __future__ import annotations

from mal_lifecycle.models import StepStatus
from mal_lifecycle.transport import json_response


def _names(result, status=None):
    return [s.name for s in result.steps if status is None or s.status is status]


def test_onboarding_runs_every_step(workflow, okta_tenant, op_tenant, jamf_server, slack_server, new_hire):
    result = workflow.onboard(new_hire)
    assert result.ok
    assert _names(result) == [
        "okta.create_user",
        "okta.assign_group",
        "google_workspace.create_user",
        "1password.create_user",
        "1password.assign_vault_group",
        "1password.verify_shared_vaults",
        "mdm.send_enrollment_invitation",
        "alert.send_summary",
    ]
    okta_user = next(u for u in okta_tenant.users.values() if u["profile"]["login"] == new_hire.login)
    eng = next(g for g in okta_tenant.groups.values() if g["profile"]["name"] == "engineering")
    assert okta_user["id"] in okta_tenant.memberships[eng["id"]]
    op_eng = next(g for g in op_tenant.groups.values() if g["displayName"] == "Engineering")
    assert any(m["display"] == new_hire.display_name for m in op_eng["members"])
    assert jamf_server.invitations[42]["email_addresses"] == new_hire.email
    assert slack_server.received[0]["text"] == f"Onboarding completed: {new_hire.login}"
    assert result.to_dict()["ok"] is True


def test_offboarding_revokes_everything(
    workflow, okta_tenant, gws_directory, op_tenant, slack_server, departing
):
    result = workflow.offboard(departing.login)
    assert result.ok
    assert _names(result) == [
        "okta.lookup_user",
        "okta.clear_sessions",
        "okta.suspend",
        "google_workspace.revoke_oauth_tokens",
        "google_workspace.sign_out",
        "google_workspace.suspend",
        "1password.lookup_user",
        "1password.remove_from_shared_vaults",
        "1password.suspend",
        "1password.audit_events",
        "alert.send_summary",
    ]
    okta_user = next(u for u in okta_tenant.users.values() if u["profile"]["login"] == departing.login)
    assert okta_user["status"] == "SUSPENDED"
    assert gws_directory.tokens[departing.login] == [] and gws_directory.users[departing.login]["suspended"]
    op_user = next(u for u in op_tenant.users.values() if u["userName"] == departing.login)
    assert op_user["active"] is False
    assert all(op_user["id"] not in [m["value"] for m in g["members"]] for g in op_tenant.groups.values())
    assert "Offboarding completed:" in slack_server.received[0]["text"]


def test_offboarding_continues_and_alerts_on_failure(workflow, slack_server, departing):
    # Simulate Google returning 403 on the token listing - everything else must still run.
    workflow.gws.transport.add(
        "GET",
        r"/tokens$",
        lambda m, c: json_response(403, {"error": {"code": 403, "message": "Not Authorized"}}),
        first=True,
    )
    result = workflow.offboard(departing.login)
    assert not result.ok
    assert _names(result, StepStatus.FAILED) == ["google_workspace.revoke_oauth_tokens"]
    assert "HTTP 403" in result.failures[0].error
    assert "okta.suspend" in _names(result, StepStatus.SUCCESS)
    assert "1password.suspend" in _names(result, StepStatus.SUCCESS)
    payload = slack_server.received[0]
    assert payload["text"].startswith("Offboarding completed with failures")
    assert "google_workspace.revoke_oauth_tokens" in str(payload["blocks"])


def test_dependent_steps_are_skipped_when_lookup_fails(workflow, slack_server):
    result = workflow.offboard("ghost@example.com")
    assert _names(result, StepStatus.SKIPPED) == [
        "okta.clear_sessions",
        "okta.suspend",
        "1password.remove_from_shared_vaults",
        "1password.suspend",
    ]
    assert "okta.lookup_user" in _names(result, StepStatus.FAILED)
    assert "alert.send_summary" in _names(result, StepStatus.SUCCESS)  # alert always goes out


def test_offboard_deactivate_action(workflow, okta_tenant, departing):
    workflow.okta_offboard_action = "deactivate"
    result = workflow.offboard(departing.login)
    assert "okta.deactivate" in _names(result, StepStatus.SUCCESS)
    user = next(u for u in okta_tenant.users.values() if u["profile"]["login"] == departing.login)
    assert user["status"] == "DEPROVISIONED"
