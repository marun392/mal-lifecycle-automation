"""Structured completion alerts: Slack incoming webhook (Block Kit) or SMTP email."""

from __future__ import annotations

import json
import logging
import re
import smtplib
from collections.abc import Callable, Iterable
from email.message import EmailMessage
from typing import Any, Protocol

from .models import StepStatus, WorkflowResult
from .transport import BaseClient, Call, MockTransport, Response, Transport, text_response

log = logging.getLogger(__name__)

STATUS_ICON = {
    StepStatus.SUCCESS: ":white_check_mark:",
    StepStatus.FAILED: ":x:",
    StepStatus.SKIPPED: ":fast_forward:",
}
STATUS_TEXT = {StepStatus.SUCCESS: "OK", StepStatus.FAILED: "FAILED", StepStatus.SKIPPED: "SKIPPED"}


class Alerter(Protocol):
    def send(self, result: WorkflowResult) -> dict[str, Any]: ...


def build_slack_payload(result: WorkflowResult) -> dict[str, Any]:
    """Block Kit message summarising user, timestamp, actions taken and failures."""
    title = f"{result.workflow.title()} {'completed' if result.ok else 'completed with failures'}"
    outcome = (
        ":large_green_circle: all steps succeeded"
        if result.ok
        else f":red_circle: {len(result.failures)} step(s) failed"
    )
    action_lines = [
        f"{STATUS_ICON[s.status]} `{s.name}` - {s.detail or s.error or STATUS_TEXT[s.status]}"
        for s in result.steps
    ]
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*User:*\n{result.subject}"},
                {"type": "mrkdwn", "text": f"*Workflow:*\n{result.workflow}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Started (UTC):*\n{result.started_at.strftime('%Y-%m-%d %H:%M:%S')}",
                },
                {"type": "mrkdwn", "text": f"*Duration:*\n{result.duration_seconds:.1f}s"},
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Outcome:* {outcome}"}},
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Actions taken:*\n" + "\n".join(action_lines)},
        },
    ]
    if result.failures:
        failure_lines = [f"- `{s.name}`: {s.error}" for s in result.failures]
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Failures:*\n" + "\n".join(failure_lines)},
            }
        )
    modes = ", ".join(f"{k}={v}" for k, v in result.modes.items())
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"mal-lifecycle-automation | modes: {modes}"}],
        }
    )
    return {
        "text": f"{title}: {result.subject}",
        "blocks": blocks,
        "attachments": [{"color": "#2EB67D" if result.ok else "#E01E5A", "blocks": []}],
    }


def build_email(result: WorkflowResult, sender: str, recipients: Iterable[str]) -> EmailMessage:
    msg = EmailMessage()
    status = "OK" if result.ok else f"{len(result.failures)} FAILURE(S)"
    msg["Subject"] = f"[{status}] {result.workflow} for {result.subject}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    lines = [
        f"Workflow : {result.workflow}",
        f"User     : {result.subject}",
        f"Started  : {result.started_at.isoformat()}",
        f"Duration : {result.duration_seconds:.1f}s",
        f"Outcome  : {status}",
        "",
        "Actions taken:",
    ]
    lines += [f"  [{STATUS_TEXT[s.status]:>7}] {s.name}: {s.detail or s.error or ''}" for s in result.steps]
    if result.failures:
        lines += ["", "Failures:"] + [f"  - {s.name}: {s.error}" for s in result.failures]
    msg.set_content("\n".join(lines))
    msg.add_attachment(
        json.dumps(result.to_dict(), indent=2).encode(),
        maintype="application",
        subtype="json",
        filename=f"{result.workflow}-{result.subject}.json",
    )
    return msg


class SlackWebhookAlerter(BaseClient):
    service = "slack"

    def __init__(self, webhook_url: str, transport: Transport):
        super().__init__(webhook_url, transport, {"Content-Type": "application/json"})

    def send(self, result: WorkflowResult) -> dict[str, Any]:
        payload = build_slack_payload(result)
        resp = self._request("POST", self.base_url, json=payload, expect=(200,))
        return {"channel": "slack", "response": resp.text, "blocks": len(payload["blocks"])}


SMTPFactory = Callable[[], smtplib.SMTP]


class EmailAlerter:
    service = "email"

    def __init__(
        self,
        sender: str,
        recipients: list[str],
        smtp_factory: SMTPFactory,
        *,
        username: str | None = None,
        password: str | None = None,
    ):
        self.sender = sender
        self.recipients = recipients
        self._smtp_factory = smtp_factory
        self._username = username
        self._password = password

    def send(self, result: WorkflowResult) -> dict[str, Any]:
        msg = build_email(result, self.sender, self.recipients)
        with self._smtp_factory() as smtp:
            if self._username and self._password:
                smtp.login(self._username, self._password)
            smtp.send_message(msg)
        return {"channel": "email", "to": self.recipients, "subject": msg["Subject"]}


def smtp_factory(host: str, port: int, *, starttls: bool = True) -> SMTPFactory:
    def factory() -> smtplib.SMTP:
        smtp = smtplib.SMTP(host, port, timeout=30)
        if starttls:
            smtp.starttls()
        return smtp

    return factory


class MockSMTP:
    """Drop-in for ``smtplib.SMTP`` that logs the message instead of sending it."""

    sent: list[EmailMessage]

    def __init__(self) -> None:
        self.sent = []

    def __enter__(self) -> MockSMTP:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        log.info("[email-mock] login as %s", username)

    def send_message(self, msg: EmailMessage) -> None:
        self.sent.append(msg)
        log.info(
            "[email-mock] would send %r to %s\n%s", msg["Subject"], msg["To"], msg.get_body().get_content()
        )


# -- mock Slack -----------------------------------------------------------------

MOCK_WEBHOOK_URL = "https://hooks.slack.com/mock/TMOCK/BMOCK/not-a-real-webhook"


class SlackMockServer:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []

    def webhook(self, _m: re.Match[str], call: Call) -> Response:
        payload = call.json or {}
        if "blocks" not in payload and "text" not in payload:
            return text_response(400, "invalid_payload")
        self.received.append(payload)
        log.info("[slack-mock] webhook payload:\n%s", json.dumps(payload, indent=2))
        return text_response(200, "ok")

    def install(self, transport: MockTransport) -> MockTransport:
        transport.add("POST", r"^https://hooks\.slack\.com/", self.webhook)
        return transport


def mock_slack_alerter(server: SlackMockServer | None = None) -> SlackWebhookAlerter:
    server = server or SlackMockServer()
    return SlackWebhookAlerter(MOCK_WEBHOOK_URL, server.install(MockTransport(label="slack-mock")))
