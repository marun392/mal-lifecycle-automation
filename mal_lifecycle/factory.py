"""Build API clients (live or mock) from :class:`Settings`."""

from __future__ import annotations

from .alerts import (
    Alerter,
    EmailAlerter,
    MockSMTP,
    SlackWebhookAlerter,
    mock_slack_alerter,
    smtp_factory,
)
from .config import Settings
from .gws import GoogleWorkspaceClient, mock_gws_client, service_account_token_provider
from .mdm import JamfClient, mock_jamf_client
from .okta import OktaClient, mock_okta_client
from .onepassword import (
    OnePasswordClients,
    OnePasswordConnectClient,
    OnePasswordEventsClient,
    OnePasswordSCIMClient,
    mock_onepassword_clients,
)
from .transport import LiveTransport
from .workflow import LifecycleWorkflow


def build_okta(settings: Settings, live: LiveTransport) -> OktaClient:
    if settings.okta_mode == "mock":
        return mock_okta_client()
    assert settings.okta_domain and settings.okta_api_token
    return OktaClient(settings.okta_domain, settings.okta_api_token, live)


def build_gws(settings: Settings, live: LiveTransport) -> GoogleWorkspaceClient:
    if settings.gws_mode == "mock":
        return mock_gws_client()
    if settings.gws_access_token:
        token = str(settings.gws_access_token)
        return GoogleWorkspaceClient(lambda: token, live)
    assert settings.gws_service_account_file and settings.gws_admin_email
    provider = service_account_token_provider(settings.gws_service_account_file, settings.gws_admin_email)
    return GoogleWorkspaceClient(provider, live)


def build_onepassword(settings: Settings, live: LiveTransport) -> OnePasswordClients:
    if settings.op_mode == "mock":
        return mock_onepassword_clients()
    assert settings.op_scim_url and settings.op_scim_token
    scim = OnePasswordSCIMClient(settings.op_scim_url, settings.op_scim_token, live)
    connect = (
        OnePasswordConnectClient(settings.op_connect_host, settings.op_connect_token, live)
        if settings.op_connect_host and settings.op_connect_token
        else None
    )
    events = (
        OnePasswordEventsClient(settings.op_events_host, settings.op_events_token, live)
        if settings.op_events_token
        else None
    )
    return OnePasswordClients(scim, connect, events)


def build_mdm(settings: Settings, live: LiveTransport) -> JamfClient:
    if settings.mdm_mode == "mock":
        return mock_jamf_client()
    assert settings.jamf_url and settings.jamf_username and settings.jamf_password
    return JamfClient(settings.jamf_url, settings.jamf_username, settings.jamf_password, live)


def build_alerter(settings: Settings, live: LiveTransport) -> Alerter:
    if settings.alert_channel == "email":
        if settings.alert_mode == "mock":
            return EmailAlerter(
                settings.alert_email_from or "it-security@example.com",
                settings.alert_email_to or ["security-alerts@example.com"],
                MockSMTP,
            )
        assert settings.smtp_host and settings.alert_email_from and settings.alert_email_to
        return EmailAlerter(
            settings.alert_email_from,
            settings.alert_email_to,
            smtp_factory(settings.smtp_host, settings.smtp_port),
            username=settings.smtp_username,
            password=settings.smtp_password,
        )
    if settings.alert_mode == "mock":
        return mock_slack_alerter()
    assert settings.slack_webhook_url
    return SlackWebhookAlerter(settings.slack_webhook_url, live)


def build_workflow(settings: Settings) -> LifecycleWorkflow:
    live = LiveTransport()
    return LifecycleWorkflow(
        okta=build_okta(settings, live),
        gws=build_gws(settings, live),
        onepassword=build_onepassword(settings, live),
        mdm=build_mdm(settings, live),
        alerter=build_alerter(settings, live),
        modes=settings.modes(),
        okta_offboard_action=settings.okta_offboard_action,
        gws_org_unit_path=settings.gws_org_unit_path,
        shared_vaults=settings.op_shared_vaults,
        mdm_invitation_days=settings.jamf_invitation_days,
    )
