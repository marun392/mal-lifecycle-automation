from __future__ import annotations

import pytest
import responses

from mal_lifecycle.transport import (
    APIError,
    BaseClient,
    LiveTransport,
    MockTransport,
    json_response,
    redact_url,
)


def test_mock_transport_routes_and_records_calls():
    transport = MockTransport()
    transport.add(
        "GET", r"/things/(?P<id>\d+)$", lambda m, call: json_response(200, {"id": int(m.group("id"))})
    )
    resp = transport.request("get", "https://api.example.com/things/7", headers={"X": "1"})
    assert resp.status_code == 200 and resp.json() == {"id": 7}
    assert transport.calls[0].method == "GET" and transport.calls[0].headers == {"X": "1"}


def test_mock_transport_unknown_route_raises():
    with pytest.raises(APIError, match="no mock route"):
        MockTransport().request("POST", "https://api.example.com/nothing")


@responses.activate
def test_live_transport_sends_real_http():
    responses.add(responses.POST, "https://api.example.com/v1/users", json={"id": "abc"}, status=201)
    resp = LiveTransport().request("POST", "https://api.example.com/v1/users", json={"login": "x"})
    assert resp.status_code == 201 and resp.json()["id"] == "abc"
    assert responses.calls[0].request.body == b'{"login": "x"}'


def test_base_client_raises_api_error_with_body():
    transport = MockTransport()
    transport.add("GET", r"/boom$", lambda m, c: json_response(500, {"errorSummary": "kaboom"}))
    client = BaseClient("https://api.example.com", transport)
    with pytest.raises(APIError) as exc:
        client._request("GET", "/boom")
    assert exc.value.status_code == 500 and exc.value.body == {"errorSummary": "kaboom"}
    assert "kaboom" in str(exc.value)


def test_redact_url_hides_secrets():
    assert redact_url("https://x/y?token=abc&foo=bar") == "https://x/y?token=***&foo=bar"
