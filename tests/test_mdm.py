from __future__ import annotations

import xml.etree.ElementTree as ET

from mal_lifecycle.mdm import build_invitation_xml


def test_invitation_xml_payload():
    xml = build_invitation_xml("ada@example.com", "2026-12-01 00:00:00", ssh_username="jamfmanagement")
    root = ET.fromstring(xml)
    assert root.tag == "computer_invitation"
    assert root.findtext("email_addresses") == "ada@example.com"
    assert root.findtext("invitation_type") == "EMAIL"
    assert root.findtext("enrolled_into_site/id") == "-1"
    assert root.findtext("multiple_uses_allowed") == "false"


def test_send_enrollment_invitation_authenticates_then_posts(jamf, jamf_server, new_hire):
    invitation = jamf.send_enrollment_invitation(new_hire, expires_in_days=7)
    assert invitation["id"] == 42
    assert invitation["email_addresses"] == new_hire.email
    assert invitation["enrollment_status"] == "Pending"
    calls = jamf.transport.calls
    assert calls[0].url.endswith("/api/v1/auth/token") and calls[0].auth == (
        "mock-api-user",
        "mock-api-password",
    )
    assert calls[1].url.endswith("/JSSResource/computerinvitations/id/0")
    assert calls[1].headers["Content-Type"] == "application/xml"
    assert calls[1].headers["Authorization"].startswith("Bearer ")
    assert new_hire.email in calls[1].data
    assert calls[2].url.endswith("/JSSResource/computerinvitations/id/42")
    assert jamf_server.invitations[42]["times_used"] == 0


def test_token_is_reused_across_invitations(jamf, new_hire, departing):
    jamf.send_enrollment_invitation(new_hire)
    jamf.send_enrollment_invitation(departing)
    auth_calls = [c for c in jamf.transport.calls if c.url.endswith("/auth/token")]
    assert len(auth_calls) == 1
