from __future__ import annotations

import pytest

from mal_lifecycle.config import ConfigError, Secret, Settings


def test_defaults_to_mock_everywhere():
    settings = Settings.from_env({})
    assert set(settings.modes().values()) == {"mock"}


def test_live_mode_requires_credentials():
    with pytest.raises(ConfigError, match="OKTA_API_TOKEN"):
        Settings.from_env({"OKTA_MODE": "live", "OKTA_DOMAIN": "dev-1.okta.com"})


def test_live_mode_reads_secrets_from_env_and_hides_them():
    env = {"OKTA_MODE": "live", "OKTA_DOMAIN": "dev-1.okta.com", "OKTA_API_TOKEN": "00abc"}
    settings = Settings.from_env(env)
    assert settings.okta_mode == "live"
    assert isinstance(settings.okta_api_token, Secret) and settings.okta_api_token == "00abc"
    assert "00abc" not in repr(settings)


def test_force_mock_overrides_live():
    env = {"OKTA_MODE": "live"}  # no credentials, would normally fail
    assert Settings.from_env(env, force_mock=True).okta_mode == "mock"


def test_invalid_mode_rejected():
    with pytest.raises(ConfigError):
        Settings.from_env({"GWS_MODE": "sometimes"})


def test_csv_lists():
    settings = Settings.from_env(
        {"OP_SHARED_VAULTS": "Engineering, Shared Infra", "ALERT_EMAIL_TO": "a@x.com,b@x.com"}
    )
    assert settings.op_shared_vaults == ["Engineering", "Shared Infra"]
    assert settings.alert_email_to == ["a@x.com", "b@x.com"]
