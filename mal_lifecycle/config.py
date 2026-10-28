"""Configuration loaded exclusively from environment variables (or a ``.env`` file)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields

MODES = ("mock", "live")


class ConfigError(ValueError):
    """Raised when the environment is missing something required for live mode."""


class Secret(str):
    """A ``str`` that never prints its value in ``repr``/logs."""

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "Secret('***')"


def _secret(env: Mapping[str, str], key: str) -> Secret | None:
    value = env.get(key)
    return Secret(value) if value else None


def _mode(env: Mapping[str, str], key: str, force_mock: bool) -> str:
    if force_mock:
        return "mock"
    value = (env.get(key) or "mock").strip().lower()
    if value not in MODES:
        raise ConfigError(f"{key} must be one of {MODES}, got {value!r}")
    return value


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # Okta
    okta_mode: str = "mock"
    okta_domain: str | None = None
    okta_api_token: Secret | None = None
    okta_offboard_action: str = "suspend"  # or "deactivate"

    # Google Workspace
    gws_mode: str = "mock"
    gws_service_account_file: str | None = None
    gws_admin_email: str | None = None
    gws_access_token: Secret | None = None
    gws_org_unit_path: str = "/"

    # 1Password
    op_mode: str = "mock"
    op_scim_url: str | None = None
    op_scim_token: Secret | None = None
    op_connect_host: str | None = None
    op_connect_token: Secret | None = None
    op_events_host: str = "https://events.1password.com"
    op_events_token: Secret | None = None
    op_shared_vaults: list[str] = field(default_factory=lambda: ["Engineering"])

    # MDM (Jamf Pro)
    mdm_mode: str = "mock"
    jamf_url: str | None = None
    jamf_username: str | None = None
    jamf_password: Secret | None = None
    jamf_invitation_days: int = 30

    # Alerts
    alert_mode: str = "mock"
    alert_channel: str = "slack"  # or "email"
    slack_webhook_url: Secret | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: Secret | None = None
    alert_email_from: str | None = None
    alert_email_to: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, force_mock: bool = False) -> Settings:
        env = os.environ if env is None else env
        settings = cls(
            okta_mode=_mode(env, "OKTA_MODE", force_mock),
            okta_domain=env.get("OKTA_DOMAIN"),
            okta_api_token=_secret(env, "OKTA_API_TOKEN"),
            okta_offboard_action=(env.get("OKTA_OFFBOARD_ACTION") or "suspend").lower(),
            gws_mode=_mode(env, "GWS_MODE", force_mock),
            gws_service_account_file=env.get("GWS_SERVICE_ACCOUNT_FILE"),
            gws_admin_email=env.get("GWS_ADMIN_EMAIL"),
            gws_access_token=_secret(env, "GWS_ACCESS_TOKEN"),
            gws_org_unit_path=env.get("GWS_ORG_UNIT_PATH") or "/",
            op_mode=_mode(env, "OP_MODE", force_mock),
            op_scim_url=env.get("OP_SCIM_URL"),
            op_scim_token=_secret(env, "OP_SCIM_TOKEN"),
            op_connect_host=env.get("OP_CONNECT_HOST"),
            op_connect_token=_secret(env, "OP_CONNECT_TOKEN"),
            op_events_host=env.get("OP_EVENTS_HOST") or "https://events.1password.com",
            op_events_token=_secret(env, "OP_EVENTS_TOKEN"),
            op_shared_vaults=_csv(env.get("OP_SHARED_VAULTS")) or ["Engineering"],
            mdm_mode=_mode(env, "MDM_MODE", force_mock),
            jamf_url=env.get("JAMF_URL"),
            jamf_username=env.get("JAMF_USERNAME"),
            jamf_password=_secret(env, "JAMF_PASSWORD"),
            jamf_invitation_days=int(env.get("JAMF_INVITATION_DAYS") or 30),
            alert_mode=_mode(env, "ALERT_MODE", force_mock),
            alert_channel=(env.get("ALERT_CHANNEL") or "slack").lower(),
            slack_webhook_url=_secret(env, "SLACK_WEBHOOK_URL"),
            smtp_host=env.get("SMTP_HOST"),
            smtp_port=int(env.get("SMTP_PORT") or 587),
            smtp_username=env.get("SMTP_USERNAME"),
            smtp_password=_secret(env, "SMTP_PASSWORD"),
            alert_email_from=env.get("ALERT_EMAIL_FROM"),
            alert_email_to=_csv(env.get("ALERT_EMAIL_TO")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        missing: list[str] = []
        if self.okta_mode == "live":
            missing += _require(self, okta_domain="OKTA_DOMAIN", okta_api_token="OKTA_API_TOKEN")
        if self.okta_offboard_action not in ("suspend", "deactivate"):
            raise ConfigError("OKTA_OFFBOARD_ACTION must be 'suspend' or 'deactivate'")
        if self.gws_mode == "live" and not self.gws_access_token:
            missing += _require(
                self,
                gws_service_account_file="GWS_SERVICE_ACCOUNT_FILE",
                gws_admin_email="GWS_ADMIN_EMAIL",
            )
        if self.op_mode == "live":
            missing += _require(self, op_scim_url="OP_SCIM_URL", op_scim_token="OP_SCIM_TOKEN")
        if self.mdm_mode == "live":
            missing += _require(
                self, jamf_url="JAMF_URL", jamf_username="JAMF_USERNAME", jamf_password="JAMF_PASSWORD"
            )
        if self.alert_channel not in ("slack", "email"):
            raise ConfigError("ALERT_CHANNEL must be 'slack' or 'email'")
        if self.alert_mode == "live":
            if self.alert_channel == "slack":
                missing += _require(self, slack_webhook_url="SLACK_WEBHOOK_URL")
            else:
                missing += _require(
                    self,
                    smtp_host="SMTP_HOST",
                    alert_email_from="ALERT_EMAIL_FROM",
                    alert_email_to="ALERT_EMAIL_TO",
                )
        if missing:
            raise ConfigError(
                "Live mode enabled but required environment variables are missing: " + ", ".join(missing)
            )

    def modes(self) -> dict[str, str]:
        return {
            "okta": self.okta_mode,
            "google_workspace": self.gws_mode,
            "1password": self.op_mode,
            "mdm": self.mdm_mode,
            "alerts": self.alert_mode,
        }


def _require(settings: Settings, **attr_to_env: str) -> list[str]:
    known = {f.name for f in fields(settings)}
    return [
        env_name for attr, env_name in attr_to_env.items() if attr in known and not getattr(settings, attr)
    ]
