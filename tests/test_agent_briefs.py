import os

import pytest
from fastapi.testclient import TestClient

import main
import services.agent_briefs as agent_briefs

COS_API_KEY = os.environ["COS_API_KEY"]
AUTH_HEADERS = {"Authorization": "Bearer {}".format(COS_API_KEY)}
MAYA_ID = "MAYA-WD-MKT-001"


@pytest.fixture
def client():
    return TestClient(main.app)


def _sample_payload(title="Weekly Technology Intelligence Brief", external_reference=None):
    payload = {
        "title": title,
        "summary": "Consequential technology developments.",
        "brief_type": "WEEKLY_TECH_INTELLIGENCE",
        "source_agent_id": "WD-AI-001",
        "priority": "NORMAL",
        "reporting_period_start": "2026-08-17",
        "reporting_period_end": "2026-08-23",
        "sections": [
            {
                "category": "AI Automation",
                "development": "Description of the consequential development.",
                "what_changed": "A concise explanation of the change.",
                "why_it_matters": "How it could affect WadadliTech.",
                "small_business_relevance": "Application for a small business.",
                "antigua_barbuda_relevance": "Application for Antigua and Barbuda.",
                "recommended_move": "One practical action.",
                "sources": [
                    {
                        "title": "Source title",
                        "url": "https://example.com/source",
                        "published_at": "2026-08-18",
                    }
                ],
            }
        ],
        "practical_move": "Choose one development for a controlled pilot.",
        "tags": ["AI", "automation"],
        "requires_human_approval": False,
        "metadata": {"recipient_name": "Maya"},
    }

    if external_reference:
        payload["external_reference"] = external_reference

    return payload


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeAsyncClient:
    status_code = 200
    raise_exception = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json=None):
        if _FakeAsyncClient.raise_exception:
            import httpx

            raise httpx.ConnectError("simulated network failure")

        return _FakeResponse(_FakeAsyncClient.status_code)


@pytest.fixture(autouse=True)
def _reset_fake_client():
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.raise_exception = False
    yield


@pytest.fixture
def mock_webhook(monkeypatch):
    def _configure(status_code=None, raise_exception=False, configured=True):
        if configured:
            monkeypatch.setenv(
                "MAYA_WD_WEBHOOK_URL", "https://example.com/maya-webhook"
            )
        else:
            monkeypatch.delenv("MAYA_WD_WEBHOOK_URL", raising=False)

        if status_code is not None:
            _FakeAsyncClient.status_code = status_code

        _FakeAsyncClient.raise_exception = raise_exception

        monkeypatch.setattr(
            agent_briefs.httpx, "AsyncClient", _FakeAsyncClient
        )

    return _configure


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_missing_bearer_token_returns_401(client):
    response = client.post(
        "/agents/{}/briefs".format(MAYA_ID),
        json=_sample_payload(),
    )
    assert response.status_code == 401


def test_invalid_bearer_token_returns_403(client):
    response = client.post(
        "/agents/{}/briefs".format(MAYA_ID),
        json=_sample_payload(),
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------


def test_unknown_agent_returns_404(client, mock_webhook):
    mock_webhook(configured=False)

    response = client.post(
        "/agents/UNKNOWN-AGENT/briefs",
        json=_sample_payload(),
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 404


def test_list_and_get_agent(client):
    response = client.get("/agents", headers=AUTH_HEADERS)
    assert response.status_code == 200
    agent_ids = [a["agent_id"] for a in response.json()["agents"]]
    assert MAYA_ID in agent_ids

    response = client.get("/agents/{}".format(MAYA_ID), headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["name"] == "Maya"

    response = client.get("/agents/UNKNOWN-AGENT", headers=AUTH_HEADERS)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Brief creation, retrieval, and delivery
# ---------------------------------------------------------------------------


def test_create_brief_webhook_not_configured(client, mock_webhook):
    mock_webhook(configured=False)

    response = client.post(
        "/agents/{}/briefs".format(MAYA_ID),
        json=_sample_payload(title="Not Configured Brief"),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["agent_id"] == MAYA_ID
    assert body["delivery_status"] == "PENDING_CONFIGURATION"
    assert body["status"] == "QUEUED"


def test_create_brief_webhook_success(client, mock_webhook):
    mock_webhook(status_code=200)

    response = client.post(
        "/agents/{}/briefs".format(MAYA_ID),
        json=_sample_payload(title="Delivered Brief"),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["delivery_status"] == "DELIVERED"
    assert body["status"] == "DELIVERED"


def test_create_brief_webhook_failure(client, mock_webhook):
    mock_webhook(status_code=500)

    response = client.post(
        "/agents/{}/briefs".format(MAYA_ID),
        json=_sample_payload(title="Failed Delivery Brief"),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["delivery_status"] == "FAILED"
    assert body["status"] == "DELIVERY_FAILED"


def test_retry_failed_delivery_succeeds(client, mock_webhook):
    mock_webhook(status_code=500)

    create_response = client.post(
        "/agents/{}/briefs".format(MAYA_ID),
        json=_sample_payload(title="Retry Brief"),
        headers=AUTH_HEADERS,
    )
    brief_id = create_response.json()["brief_id"]
    assert create_response.json()["delivery_status"] == "FAILED"

    mock_webhook(status_code=200)

    retry_response = client.post(
        "/agents/{}/briefs/{}/retry-delivery".format(MAYA_ID, brief_id),
        headers=AUTH_HEADERS,
    )

    assert retry_response.status_code == 200
    body = retry_response.json()
    assert body["delivery_status"] == "DELIVERED"
    assert body["status"] == "DELIVERED"
    assert body["delivery_attempts"] == 2


def test_idempotent_duplicate_submission_does_not_duplicate(client, mock_webhook):
    mock_webhook(configured=False)

    payload = _sample_payload(
        title="Idempotent Brief",
        external_reference="weekly-tech-brief-2026-08-24-{}".format(MAYA_ID),
    )

    first = client.post(
        "/agents/{}/briefs".format(MAYA_ID),
        json=payload,
        headers=AUTH_HEADERS,
    )
    second = client.post(
        "/agents/{}/briefs".format(MAYA_ID),
        json=payload,
        headers=AUTH_HEADERS,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["brief_id"] == second.json()["brief_id"]

    listing = client.get(
        "/agents/{}/briefs".format(MAYA_ID),
        params={"limit": 200},
        headers=AUTH_HEADERS,
    )
    matching = [
        b
        for b in listing.json()["briefs"]
        if b["title"] == "Idempotent Brief"
    ]
    assert len(matching) == 1


def test_retrieve_brief_queue_and_single_brief(client, mock_webhook):
    mock_webhook(configured=False)

    create_response = client.post(
        "/agents/{}/briefs".format(MAYA_ID),
        json=_sample_payload(title="Queue Brief"),
        headers=AUTH_HEADERS,
    )
    brief_id = create_response.json()["brief_id"]

    queue_response = client.get(
        "/agents/{}/briefs".format(MAYA_ID),
        headers=AUTH_HEADERS,
    )
    assert queue_response.status_code == 200
    assert queue_response.json()["count"] >= 1

    single_response = client.get(
        "/agents/{}/briefs/{}".format(MAYA_ID, brief_id),
        headers=AUTH_HEADERS,
    )
    assert single_response.status_code == 200
    assert single_response.json()["id"] == brief_id


def test_acknowledge_and_complete_brief(client, mock_webhook):
    mock_webhook(configured=False)

    create_response = client.post(
        "/agents/{}/briefs".format(MAYA_ID),
        json=_sample_payload(title="Lifecycle Brief"),
        headers=AUTH_HEADERS,
    )
    brief_id = create_response.json()["brief_id"]

    ack_response = client.post(
        "/agents/{}/briefs/{}/acknowledge".format(MAYA_ID, brief_id),
        headers=AUTH_HEADERS,
    )
    assert ack_response.status_code == 200
    assert ack_response.json()["status"] == "ACKNOWLEDGED"

    complete_response = client.post(
        "/agents/{}/briefs/{}/complete".format(MAYA_ID, brief_id),
        headers=AUTH_HEADERS,
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "COMPLETED"


def test_invalid_status_transition_returns_409(client, mock_webhook):
    mock_webhook(configured=False)

    create_response = client.post(
        "/agents/{}/briefs".format(MAYA_ID),
        json=_sample_payload(title="Invalid Transition Brief"),
        headers=AUTH_HEADERS,
    )
    brief_id = create_response.json()["brief_id"]

    client.post(
        "/agents/{}/briefs/{}/acknowledge".format(MAYA_ID, brief_id),
        headers=AUTH_HEADERS,
    )
    client.post(
        "/agents/{}/briefs/{}/complete".format(MAYA_ID, brief_id),
        headers=AUTH_HEADERS,
    )

    # A completed brief cannot be acknowledged again.
    response = client.post(
        "/agents/{}/briefs/{}/acknowledge".format(MAYA_ID, brief_id),
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Executive summary integration
# ---------------------------------------------------------------------------


def test_executive_summary_includes_agent_operations(client, mock_webhook, monkeypatch):
    mock_webhook(configured=False)

    async def fake_safe_call(source_name, callable_obj, *args, **kwargs):
        return {
            "source": source_name,
            "available": False,
            "data": None,
            "error": "mocked for test",
        }

    monkeypatch.setattr(main, "safe_call", fake_safe_call)

    client.post(
        "/agents/{}/briefs".format(MAYA_ID),
        json=_sample_payload(title="Executive Summary Brief"),
        headers=AUTH_HEADERS,
    )

    response = client.get("/executive-summary", headers=AUTH_HEADERS)
    assert response.status_code == 200

    body = response.json()
    assert "agent_operations" in body
    maya_ops = body["agent_operations"]["maya"]
    assert maya_ops["agent_id"] == MAYA_ID
    assert maya_ops["webhook_configured"] is False
    assert maya_ops["pending_briefs"] >= 1


# ---------------------------------------------------------------------------
# Existing routes remain backward compatible
# ---------------------------------------------------------------------------


def test_existing_task_routes_still_work(client):
    create_response = client.post(
        "/tasks",
        json={"title": "Backward Compatibility Task"},
        headers=AUTH_HEADERS,
    )
    assert create_response.status_code == 200
    task_id = create_response.json()["id"]

    get_response = client.get(
        "/tasks/{}".format(task_id), headers=AUTH_HEADERS
    )
    assert get_response.status_code == 200

    delete_response = client.delete(
        "/tasks/{}".format(task_id), headers=AUTH_HEADERS
    )
    assert delete_response.status_code == 200


def test_existing_highlevel_route_still_works(client, monkeypatch):
    async def fake_get_location(account_key="wadadlitech"):
        return {
            "location": {
                "id": "loc_123",
                "name": "WadadliTech",
                "timezone": "America/Antigua",
            }
        }

    monkeypatch.setattr(main, "get_location", fake_get_location)

    response = client.get("/highlevel/status", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["location_id"] == "loc_123"


def test_health_endpoint_still_works(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["version"] == "2.2.0"
