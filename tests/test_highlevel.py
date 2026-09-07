import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient

import main
import services.highlevel as highlevel

COS_API_KEY = os.environ["COS_API_KEY"]
AUTH_HEADERS = {"Authorization": "Bearer {}".format(COS_API_KEY)}


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}

    def json(self):
        return self._json_data

    @property
    def text(self):
        return json.dumps(self._json_data)


class _FakeAsyncClient:
    """Routes GET/POST calls to a canned response by matching a path substring."""

    responses = {}
    requests = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None, params=None):
        _FakeAsyncClient.requests.append(
            {"method": "GET", "url": url, "params": params, "json": None}
        )
        return self._resolve(url)

    async def post(self, url, headers=None, json=None):
        _FakeAsyncClient.requests.append(
            {"method": "POST", "url": url, "params": None, "json": json}
        )
        return self._resolve(url)

    def _resolve(self, url):
        for path_key, response in _FakeAsyncClient.responses.items():
            if path_key in url:
                return response
        return _FakeResponse(200, {})


def _set_response(path_key, status_code=200, json_data=None):
    _FakeAsyncClient.responses[path_key] = _FakeResponse(status_code, json_data)


def _configure_full_account_success():
    _set_response(
        "/locations/",
        200,
        {
            "location": {
                "id": "loc_1",
                "name": "WadadliTech",
                "timezone": "America/Antigua",
            }
        },
    )
    _set_response(
        "/opportunities/pipelines",
        200,
        {"pipelines": [{"id": "p1", "name": "Pipeline 1"}]},
    )
    _set_response(
        "/workflows/",
        200,
        {"workflows": [{"id": "w1", "name": "Workflow 1", "status": "published"}]},
    )
    _set_response(
        "/opportunities/search",
        200,
        {"opportunities": [{"id": "o1", "status": "open", "monetaryValue": 50}]},
    )
    _set_response(
        "/agent-studio/agent",
        200,
        {"agents": [{"id": "a1", "name": "Agent 1", "status": "active"}]},
    )


@pytest.fixture(autouse=True)
def _reset_fake_client(monkeypatch):
    _FakeAsyncClient.responses = {}
    _FakeAsyncClient.requests = []
    monkeypatch.setattr(highlevel.httpx, "AsyncClient", _FakeAsyncClient)
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# Contact parameter translation
# ---------------------------------------------------------------------------


def test_search_contacts_translates_limit_to_page_limit():
    _set_response("/contacts/search", 200, {"contacts": [], "total": 0})

    asyncio.run(highlevel.search_contacts("wadadlitech", limit=20))

    post_requests = [r for r in _FakeAsyncClient.requests if r["method"] == "POST"]
    assert len(post_requests) == 1

    payload = post_requests[0]["json"]
    assert payload["pageLimit"] == 20
    assert "limit" not in payload


def test_search_contacts_omits_none_optional_params():
    _set_response("/contacts/search", 200, {"contacts": []})

    asyncio.run(
        highlevel.search_contacts("wadadlitech", limit=10, query=None, cursor=None)
    )

    payload = _FakeAsyncClient.requests[-1]["json"]
    assert "query" not in payload
    assert "nextCursor" not in payload
    assert payload["pageLimit"] == 10


def test_search_contacts_includes_query_and_cursor_when_provided():
    _set_response("/contacts/search", 200, {"contacts": []})

    asyncio.run(
        highlevel.search_contacts(
            "wadadlitech", limit=10, query="jane", cursor="cursor-abc"
        )
    )

    payload = _FakeAsyncClient.requests[-1]["json"]
    assert payload["query"] == "jane"
    assert payload["nextCursor"] == "cursor-abc"


# ---------------------------------------------------------------------------
# Numeric bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_limit", [0, -5, 101, 1000])
def test_search_contacts_rejects_out_of_range_limit(bad_limit):
    with pytest.raises(highlevel.HighLevelError):
        asyncio.run(highlevel.search_contacts("wadadlitech", limit=bad_limit))


def test_search_contacts_rejects_non_integer_limit():
    with pytest.raises(highlevel.HighLevelError):
        asyncio.run(highlevel.search_contacts("wadadlitech", limit="20"))


def test_search_contacts_accepts_valid_limit():
    _set_response("/contacts/search", 200, {"contacts": [{"id": "c1"}], "total": 1})

    result = asyncio.run(highlevel.search_contacts("wadadlitech", limit=100))

    assert result["contacts"][0]["id"] == "c1"

    payload = _FakeAsyncClient.requests[-1]["json"]
    assert payload["pageLimit"] == 100


# ---------------------------------------------------------------------------
# Account summary: partial failure, complete failure, full success
# ---------------------------------------------------------------------------


def test_account_summary_partial_contacts_failure_is_degraded():
    _configure_full_account_success()
    _set_response(
        "/contacts/search",
        422,
        {
            "message": [
                "property limit should not exist",
                "pageLimit must be a number conforming to the specified constraints",
            ],
            "error": "Unprocessable Entity",
            "statusCode": 422,
        },
    )

    result = asyncio.run(highlevel.get_account_summary("wadadlitech"))

    assert result["status"] == "DEGRADED"
    assert result["resources"] == {
        "pipelines": "HEALTHY",
        "workflows": "HEALTHY",
        "contacts": "ERROR",
        "opportunities": "HEALTHY",
        "agents": "HEALTHY",
    }

    # Successful resources remain visible during a partial failure.
    assert result["pipelines"]["count"] == 1
    assert result["workflows"]["count"] == 1
    assert result["opportunities"]["returned"] == 1
    assert result["agents"]["count"] == 1
    assert result["contacts"]["returned"] == 0

    contacts_error = result["errors"]["contacts"]
    assert contacts_error["provider"] == "highlevel"
    assert contacts_error["account_key"] == "wadadlitech"
    assert contacts_error["resource"] == "contacts"
    assert contacts_error["http_status"] == 422
    assert contacts_error["error_type"] == "UPSTREAM_VALIDATION_ERROR"
    assert contacts_error["retryable"] is False
    assert contacts_error["trace_id"]
    # Never leak the raw upstream body/PII, only a short safe message.
    assert "trace_id" not in contacts_error["message"]


def test_account_summary_location_failure_is_unavailable():
    _set_response("/locations/", 401, {"message": "Invalid token"})

    with pytest.raises(highlevel.HighLevelError):
        asyncio.run(highlevel.get_account_summary("wadadlitech"))


def test_account_summary_full_success_is_healthy():
    _configure_full_account_success()
    _set_response("/contacts/search", 200, {"contacts": [{"id": "c1"}], "total": 1})

    result = asyncio.run(highlevel.get_account_summary("wadadlitech"))

    assert result["status"] == "HEALTHY"
    assert result["errors"] is None
    assert all(status == "HEALTHY" for status in result["resources"].values())


@pytest.mark.parametrize(
    "account_key", ["wadadlitech", "paradigm", "jermaingordon"]
)
def test_account_summary_works_for_every_configured_account(account_key):
    _configure_full_account_success()
    _set_response("/contacts/search", 200, {"contacts": [{"id": "c1"}], "total": 1})

    result = asyncio.run(highlevel.get_account_summary(account_key))

    assert result["account_key"] == account_key
    assert result["status"] == "HEALTHY"


# ---------------------------------------------------------------------------
# Executive summary: partial failure must not look like total disconnection
# ---------------------------------------------------------------------------


def test_executive_summary_shows_degraded_not_unavailable(monkeypatch):

    async def fake_get_account_summary(account_key):
        if account_key == "wadadlitech":
            return {
                "account_key": account_key,
                "name": "WadadliTech",
                "relationship": "internal",
                "status": "DEGRADED",
                "resources": {
                    "pipelines": "HEALTHY",
                    "workflows": "HEALTHY",
                    "contacts": "ERROR",
                    "opportunities": "HEALTHY",
                    "agents": "HEALTHY",
                },
                "errors": {
                    "contacts": {
                        "provider": "highlevel",
                        "account_key": account_key,
                        "resource": "contacts",
                        "http_status": 422,
                        "error_type": "UPSTREAM_VALIDATION_ERROR",
                        "message": "HighLevel rejected the contact pagination parameters.",
                        "trace_id": "abc123",
                        "retryable": False,
                    }
                },
                "location": {"id": "loc_1", "name": "WadadliTech", "timezone": "America/Antigua"},
                "contacts": {"returned": 0, "reported_total": 0},
                "pipelines": {"count": 1, "items": []},
                "workflows": {"count": 1, "items": []},
                "opportunities": {
                    "returned": 1,
                    "open_count": 1,
                    "won_count": 0,
                    "lost_count": 0,
                    "open_value": 50.0,
                },
                "agents": {"count": 1, "items": []},
            }

        return {
            "account_key": account_key,
            "name": account_key,
            "relationship": "client",
            "status": "HEALTHY",
            "resources": {},
            "errors": None,
            "location": {"id": None, "name": None, "timezone": None},
            "contacts": {"returned": 0, "reported_total": 0},
            "pipelines": {"count": 0, "items": []},
            "workflows": {"count": 0, "items": []},
            "opportunities": {
                "returned": 0,
                "open_count": 0,
                "won_count": 0,
                "lost_count": 0,
                "open_value": 0.0,
            },
            "agents": {"count": 0, "items": []},
        }

    monkeypatch.setattr(main, "get_account_summary", fake_get_account_summary)

    test_client = TestClient(main.app)
    response = test_client.get("/executive-summary", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()

    wadadlitech = body["crm"]["internal"]["wadadlitech"]
    assert wadadlitech["available"] is True
    assert wadadlitech["status"] == "DEGRADED"

    assert body["source_health"]["wadadlitech"] is True
    assert body["source_health_status"]["wadadlitech"] == "DEGRADED"

    degraded_alerts = [
        alert
        for alert in body["alerts"]
        if alert.get("account") == "wadadlitech" and "degraded" in alert["message"].lower()
    ]
    assert len(degraded_alerts) == 1


# ---------------------------------------------------------------------------
# Regression: existing HighLevel endpoints still work
# ---------------------------------------------------------------------------


def test_pipelines_endpoint_still_works(client):
    _set_response(
        "/opportunities/pipelines", 200, {"pipelines": [{"id": "p1", "name": "Pipeline"}]}
    )

    response = client.get(
        "/highlevel/accounts/wadadlitech/pipelines", headers=AUTH_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["pipelines"][0]["id"] == "p1"


def test_workflows_endpoint_still_works(client):
    _set_response("/workflows/", 200, {"workflows": [{"id": "w1"}]})

    response = client.get(
        "/highlevel/accounts/wadadlitech/workflows", headers=AUTH_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["workflows"][0]["id"] == "w1"


def test_opportunities_endpoint_still_works(client):
    _set_response("/opportunities/search", 200, {"opportunities": []})

    response = client.get(
        "/highlevel/accounts/wadadlitech/opportunities", headers=AUTH_HEADERS
    )

    assert response.status_code == 200


def test_contacts_endpoint_now_works_with_valid_pagination(client):
    _set_response(
        "/contacts/search", 200, {"contacts": [{"id": "c1"}], "total": 1}
    )

    response = client.get(
        "/highlevel/accounts/wadadlitech/contacts",
        params={"limit": 20},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["contacts"][0]["id"] == "c1"

    payload = _FakeAsyncClient.requests[-1]["json"]
    assert payload["pageLimit"] == 20
    assert "limit" not in payload
