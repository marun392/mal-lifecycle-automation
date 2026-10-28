"""MDM enrollment trigger (Jamf Pro).

Sends a *computer invitation* email through the Jamf Pro Classic API, after
obtaining a bearer token from the Jamf Pro API.  The HTTP calls are mocked by
default (``MDM_MODE=mock``) with the real request/response shapes so the log
shows exactly what would be sent to a live tenant.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import Employee
from .transport import BaseClient, Call, MockTransport, Response, Transport, json_response, text_response

log = logging.getLogger(__name__)

MOCK_JAMF_URL = "https://mal-corp.jamfcloud.com"


class JamfClient(BaseClient):
    service = "jamf"

    def __init__(self, url: str, username: str, password: str, transport: Transport):
        super().__init__(url, transport)
        self._username = username
        self._password = password
        self._token: str | None = None

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def authenticate(self) -> str:
        """Exchange basic credentials for a short-lived bearer token (Jamf Pro API)."""
        resp = self._request(
            "POST",
            "/api/v1/auth/token",
            auth=(self._username, self._password),
            headers={"Accept": "application/json"},
            expect=(200,),
        )
        self._token = resp.json()["token"]
        return self._token

    def send_enrollment_invitation(
        self,
        employee: Employee,
        *,
        expires_in_days: int = 30,
        site_id: int = -1,
        ssh_username: str = "jamfmanagement",
    ) -> dict[str, Any]:
        """Email a user-initiated enrollment invitation to the new hire."""
        if not self._token:
            self.authenticate()
        expires = (datetime.now(tz=UTC) + timedelta(days=expires_in_days)).strftime("%Y-%m-%d %H:%M:%S")
        payload = build_invitation_xml(employee.email, expires, site_id=site_id, ssh_username=ssh_username)
        log.info("[jamf] POST /JSSResource/computerinvitations/id/0 payload:\n%s", payload)
        resp = self._request(
            "POST",
            "/JSSResource/computerinvitations/id/0",
            data=payload,
            headers={"Content-Type": "application/xml", "Accept": "application/xml"},
            expect=(201,),
        )
        log.info("[jamf] response HTTP %s: %s", resp.status_code, resp.text)
        invitation_id = int(ET.fromstring(resp.text).findtext("id") or 0)
        detail = self._request(
            "GET",
            f"/JSSResource/computerinvitations/id/{invitation_id}",
            headers={"Accept": "application/json"},
            expect=(200,),
        ).json()
        log.info("[jamf] invitation %s:\n%s", invitation_id, json.dumps(detail, indent=2))
        return detail["computer_invitation"]


def build_invitation_xml(email: str, expiration: str, *, site_id: int = -1, ssh_username: str) -> str:
    root = ET.Element("computer_invitation")
    fields = {
        "invitation_type": "EMAIL",
        "email_addresses": email,
        "expiration_date": expiration,
        "ssh_username": ssh_username,
        "multiple_uses_allowed": "false",
        "create_account_if_does_not_exist": "true",
        "hide_account": "true",
        "lock_down_ssh": "true",
        "keep_existing_site_membership": "false",
    }
    for tag, value in fields.items():
        ET.SubElement(root, tag).text = value
    site = ET.SubElement(root, "enrolled_into_site")
    ET.SubElement(site, "id").text = str(site_id)
    return ET.tostring(root, encoding="unicode")


# -- mock Jamf Pro -------------------------------------------------------------


class JamfMockServer:
    def __init__(self, url: str = MOCK_JAMF_URL):
        self.url = url
        self.invitations: dict[int, dict[str, Any]] = {}
        self._next_id = 42

    def auth_token(self, _m: re.Match[str], call: Call) -> Response:
        if not call.auth or not all(call.auth):
            return json_response(401, {"httpStatus": 401, "errors": [{"code": "INVALID_CREDENTIALS"}]})
        expires = (datetime.now(tz=UTC) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return json_response(200, {"token": "eyJhbGciOiJIUzI1NiJ9.mock-jamf-token", "expires": expires})

    def create_invitation(self, _m: re.Match[str], call: Call) -> Response:
        if not call.headers.get("Authorization", "").startswith("Bearer "):
            return text_response(401, "Unauthorized")
        xml = ET.fromstring(call.data or "")
        inv_id = self._next_id
        self._next_id += 1
        email = xml.findtext("email_addresses") or ""
        self.invitations[inv_id] = {
            "id": inv_id,
            "invitation": str(5981054734013511631 + inv_id),
            "invitation_type": (xml.findtext("invitation_type") or "EMAIL").title(),
            "expiration_date": xml.findtext("expiration_date"),
            "expiration_date_epoch": int(datetime.now(tz=UTC).timestamp() * 1000),
            "ssh_username": xml.findtext("ssh_username"),
            "hide_account": xml.findtext("hide_account") == "true",
            "lock_down_ssh": xml.findtext("lock_down_ssh") == "true",
            "multiple_uses_allowed": xml.findtext("multiple_uses_allowed") == "true",
            "create_account_if_does_not_exist": xml.findtext("create_account_if_does_not_exist") == "true",
            "invited_by": "api-automation",
            "email_addresses": email,
            "enrolled_into_site": {"id": int(xml.findtext("enrolled_into_site/id") or -1), "name": "None"},
            "keep_existing_site_membership": False,
            "times_used": 0,
            "enrollment_status": "Pending",
        }
        return text_response(
            201,
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<computer_invitation><id>{inv_id}</id></computer_invitation>",
            "application/xml",
        )

    def get_invitation(self, m: re.Match[str], _call: Call) -> Response:
        inv = self.invitations.get(int(m.group("id")))
        if not inv:
            return text_response(404, "<html>Not Found</html>", "text/html")
        return json_response(200, {"computer_invitation": inv})

    def install(self, transport: MockTransport) -> MockTransport:
        base = re.escape(self.url)
        transport.add("POST", rf"^{base}/api/v1/auth/token$", self.auth_token)
        transport.add("POST", rf"^{base}/JSSResource/computerinvitations/id/0$", self.create_invitation)
        transport.add("GET", rf"^{base}/JSSResource/computerinvitations/id/(?P<id>\d+)$", self.get_invitation)
        return transport


def mock_jamf_client(server: JamfMockServer | None = None) -> JamfClient:
    server = server or JamfMockServer()
    transport = server.install(MockTransport(label="jamf-mock"))
    return JamfClient(server.url, "mock-api-user", "mock-api-password", transport)
