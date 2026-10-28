from __future__ import annotations

import pytest

from mal_lifecycle.alerts import SlackMockServer, mock_slack_alerter
from mal_lifecycle.gws import GoogleWorkspaceMockDirectory, mock_gws_client
from mal_lifecycle.mdm import JamfMockServer, mock_jamf_client
from mal_lifecycle.mockdata import SEED_EMPLOYEES
from mal_lifecycle.models import Employee
from mal_lifecycle.okta import OktaMockTenant, mock_okta_client
from mal_lifecycle.onepassword import OnePasswordMockTenant, mock_onepassword_clients
from mal_lifecycle.workflow import LifecycleWorkflow


@pytest.fixture
def new_hire() -> Employee:
    return Employee("Ada", "Lovelace", "ada.lovelace@example.com", "engineering", "Software Engineer")


@pytest.fixture
def departing() -> Employee:
    return SEED_EMPLOYEES[0]


@pytest.fixture
def okta_tenant() -> OktaMockTenant:
    return OktaMockTenant()


@pytest.fixture
def okta(okta_tenant):
    return mock_okta_client(okta_tenant)


@pytest.fixture
def gws_directory() -> GoogleWorkspaceMockDirectory:
    return GoogleWorkspaceMockDirectory()


@pytest.fixture
def gws(gws_directory):
    return mock_gws_client(gws_directory)


@pytest.fixture
def op_tenant() -> OnePasswordMockTenant:
    return OnePasswordMockTenant()


@pytest.fixture
def onepassword(op_tenant):
    return mock_onepassword_clients(op_tenant)


@pytest.fixture
def jamf_server() -> JamfMockServer:
    return JamfMockServer()


@pytest.fixture
def jamf(jamf_server):
    return mock_jamf_client(jamf_server)


@pytest.fixture
def slack_server() -> SlackMockServer:
    return SlackMockServer()


@pytest.fixture
def slack(slack_server):
    return mock_slack_alerter(slack_server)


@pytest.fixture
def workflow(okta, gws, onepassword, jamf, slack) -> LifecycleWorkflow:
    return LifecycleWorkflow(
        okta=okta,
        gws=gws,
        onepassword=onepassword,
        mdm=jamf,
        alerter=slack,
        modes={
            "okta": "mock",
            "google_workspace": "mock",
            "1password": "mock",
            "mdm": "mock",
            "alerts": "mock",
        },
    )
