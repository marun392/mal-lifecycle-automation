from __future__ import annotations

from mal_lifecycle.alerts import EmailAlerter, MockSMTP, build_email, build_slack_payload
from mal_lifecycle.models import StepResult, StepStatus, WorkflowResult, utcnow


def _result(with_failure: bool) -> WorkflowResult:
    result = WorkflowResult("offboarding", "bob@example.com", modes={"okta": "mock"})
    result.steps.append(StepResult("okta", "suspend", StepStatus.SUCCESS, "user SUSPENDED"))
    if with_failure:
        result.steps.append(
            StepResult(
                "google_workspace", "revoke_oauth_tokens", StepStatus.FAILED, error="APIError: HTTP 403"
            )
        )
    result.steps.append(StepResult("1password", "suspend", StepStatus.SKIPPED, "skipped: lookup failed"))
    result.finished_at = utcnow()
    return result


def test_slack_payload_structure_success():
    payload = build_slack_payload(_result(with_failure=False))
    assert payload["blocks"][0]["type"] == "header"
    assert "Offboarding completed" in payload["blocks"][0]["text"]["text"]
    fields = {f["text"].split("\n")[0] for f in payload["blocks"][1]["fields"]}
    assert fields == {"*User:*", "*Workflow:*", "*Started (UTC):*", "*Duration:*"}
    assert payload["attachments"][0]["color"] == "#2EB67D"
    assert not any("*Failures:*" in b.get("text", {}).get("text", "") for b in payload["blocks"])


def test_slack_payload_lists_failures():
    payload = build_slack_payload(_result(with_failure=True))
    text = "\n".join(b["text"]["text"] for b in payload["blocks"] if b["type"] == "section" and "text" in b)
    assert ":x: `google_workspace.revoke_oauth_tokens`" in text
    assert "*Failures:*" in text and "HTTP 403" in text
    assert ":fast_forward: `1password.suspend`" in text
    assert payload["attachments"][0]["color"] == "#E01E5A"


def test_slack_alerter_posts_webhook(slack, slack_server):
    out = slack.send(_result(with_failure=True))
    assert out == {"channel": "slack", "response": "ok", "blocks": 7}
    assert slack_server.received[0]["text"].startswith("Offboarding completed with failures")


def test_email_alert_contents_and_mock_smtp():
    smtp = MockSMTP()
    alerter = EmailAlerter("it@example.com", ["sec@example.com"], lambda: smtp, username="u", password="p")
    out = alerter.send(_result(with_failure=True))
    assert out["channel"] == "email" and out["subject"].startswith("[1 FAILURE(S)] offboarding")
    msg = smtp.sent[0]
    body = msg.get_body().get_content()
    assert "okta.suspend" in body and "HTTP 403" in body
    assert msg["To"] == "sec@example.com"
    attachments = list(msg.iter_attachments())
    assert attachments[0].get_filename() == "offboarding-bob@example.com.json"


def test_build_email_success_subject():
    assert build_email(_result(False), "a@x", ["b@x"])["Subject"] == "[OK] offboarding for bob@example.com"
