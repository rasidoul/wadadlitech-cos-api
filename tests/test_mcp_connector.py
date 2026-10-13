import base64
import hashlib
import os
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import main
from services import mcp_connector

API_KEY = os.environ["COS_API_KEY"]
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("COS_PUBLIC_BASE_URL", "https://cos.example.com")
    mcp_connector._used_token_ids.clear()
    mcp_connector._failed_logins.clear()
    return TestClient(main.app, follow_redirects=False)


def _register(client, redirect_uri=CLAUDE_CALLBACK):
    return client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [redirect_uri]})


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _authorize(client, client_id, challenge, api_key=API_KEY):
    return client.post(
        "/oauth/authorize",
        data={
            "client_id": client_id,
            "redirect_uri": CLAUDE_CALLBACK,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "scope": "cos",
            "resource": "https://cos.example.com/mcp",
            "api_key": api_key,
        },
    )


def _sign_in(client):
    client_id = _register(client).json()["client_id"]
    verifier, challenge = _pkce()
    redirect = _authorize(client, client_id, challenge)
    code = parse_qs(urlparse(redirect.headers["location"]).query)["code"][0]
    tokens = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": CLAUDE_CALLBACK,
            "code_verifier": verifier,
        },
    ).json()
    return client_id, tokens


def _rpc(client, token, method, params=None, msg_id=1):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_unauthenticated_mcp_points_claude_at_metadata(client):
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response.status_code == 401
    assert 'resource_metadata="https://cos.example.com/.well-known/oauth-protected-resource"' in response.headers["www-authenticate"]


def test_discovery_metadata(client):
    resource = client.get("/.well-known/oauth-protected-resource").json()
    assert resource["resource"] == "https://cos.example.com/mcp"
    assert resource["authorization_servers"] == ["https://cos.example.com"]

    server = client.get("/.well-known/oauth-authorization-server").json()
    assert server["code_challenge_methods_supported"] == ["S256"]
    assert server["registration_endpoint"] == "https://cos.example.com/oauth/register"


def test_registration_rejects_unknown_redirect(client):
    assert _register(client, "https://evil.example.com/callback").status_code == 400
    assert _register(client, "http://localhost:51234/callback").status_code == 201


def test_wrong_key_does_not_issue_code(client):
    client_id = _register(client).json()["client_id"]
    _, challenge = _pkce()
    response = _authorize(client, client_id, challenge, api_key="wrong")
    assert response.status_code == 400
    assert "location" not in response.headers


def test_full_oauth_flow_and_tool_calls(client):
    _, tokens = _sign_in(client)
    access = tokens["access_token"]
    assert API_KEY not in access

    init = _rpc(client, access, "initialize", {"protocolVersion": "2025-06-18"}).json()
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"]["tools"] == {"listChanged": False}

    notified = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert notified.status_code == 202

    tools = {t["name"]: t for t in _rpc(client, access, "tools/list").json()["result"]["tools"]}
    assert {"get_executive_summary", "create_task", "today_tasks", "highlevel_all_accounts_summary"} <= set(tools)
    assert not any(name.startswith("legacy") or "github" in name for name in tools)
    assert tools["get_tasks"]["annotations"]["readOnlyHint"] is True
    assert tools["send_gmail_draft"]["annotations"]["destructiveHint"] is True

    created = _rpc(client, access, "tools/call", {
        "name": "create_task",
        "arguments": {"title": "MCP connector test task", "priority": "HIGH"},
    }).json()["result"]
    assert created["isError"] is False
    assert "MCP connector test task" in created["content"][0]["text"]

    missing = _rpc(client, access, "tools/call", {"name": "get_task", "arguments": {"task_id": 999999}}).json()["result"]
    assert missing["isError"] is True


def test_code_cannot_be_replayed(client):
    client_id = _register(client).json()["client_id"]
    verifier, challenge = _pkce()
    code = parse_qs(urlparse(_authorize(client, client_id, challenge).headers["location"]).query)["code"][0]
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": CLAUDE_CALLBACK,
        "code_verifier": verifier,
    }
    assert client.post("/oauth/token", data=form).status_code == 200
    assert client.post("/oauth/token", data=form).json()["error"] == "invalid_grant"


def test_pkce_mismatch_is_rejected(client):
    client_id = _register(client).json()["client_id"]
    _, challenge = _pkce()
    code = parse_qs(urlparse(_authorize(client, client_id, challenge).headers["location"]).query)["code"][0]
    response = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": CLAUDE_CALLBACK,
        "code_verifier": "not-the-verifier",
    })
    assert response.json()["error"] == "invalid_grant"


def test_refresh_rotates(client):
    client_id, tokens = _sign_in(client)
    form = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id}
    refreshed = client.post("/oauth/token", data=form)
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]
    assert client.post("/oauth/token", data=form).json()["error"] == "invalid_grant"


def test_static_api_key_header_still_works(client):
    assert _rpc(client, API_KEY, "ping").json()["result"] == {}


def test_forged_token_is_rejected(client):
    assert _rpc(client, "eyJ0eXAiOiJhY2Nlc3MifQ.AAAA", "ping").status_code == 401


def test_connector_routes_stay_out_of_gpt_schema():
    paths = main.app.openapi()["paths"]
    assert not any(p.startswith(("/mcp", "/oauth", "/.well-known")) for p in paths)
