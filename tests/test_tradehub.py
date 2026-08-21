import asyncio
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

import main
import services.tradehub as tradehub

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
    raise_timeout = False
    raise_connect_error = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None, params=None):
        return await self._handle("GET", url, params)

    async def post(self, url, headers=None, json=None):
        return await self._handle("POST", url, json)

    async def _handle(self, method, url, payload):
        _FakeAsyncClient.requests.append(
            {"method": method, "url": url, "payload": payload}
        )

        if _FakeAsyncClient.raise_timeout:
            raise httpx.TimeoutException("simulated timeout")

        if _FakeAsyncClient.raise_connect_error:
            raise httpx.ConnectError("simulated connection failure")

        for path_key, response in _FakeAsyncClient.responses.items():
            if path_key in url:
                return response

        return _FakeResponse(200, {"success": True, "data": {}})


def _set_response(path_key, status_code=200, json_data=None):
    _FakeAsyncClient.responses[path_key] = _FakeResponse(status_code, json_data)


@pytest.fixture(autouse=True)
def _reset_fake_client(monkeypatch):
    _FakeAsyncClient.responses = {}
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.raise_timeout = False
    _FakeAsyncClient.raise_connect_error = False
    monkeypatch.setattr(tradehub.httpx, "AsyncClient", _FakeAsyncClient)
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# 1-9: read-only client methods
# ---------------------------------------------------------------------------


def test_get_tradehub_status_returns_data():
    _set_response(
        "/api/cos/status",
        200,
        {"success": True, "data": {"status": "HEALTHY", "database_status": "HEALTHY"}},
    )

    result = asyncio.run(tradehub.get_tradehub_status())

    assert result["status"] == "HEALTHY"


def test_get_tradehub_accounts_returns_data():
    _set_response(
        "/api/cos/accounts",
        200,
        {"success": True, "data": {"accounts": [{"account_type": "demo", "balance": 1000}]}},
    )

    result = asyncio.run(tradehub.get_tradehub_accounts())

    assert result["accounts"][0]["account_type"] == "demo"


def test_get_tradehub_open_trades_returns_data_and_forwards_filters():
    _set_response(
        "/api/cos/open-trades",
        200,
        {"success": True, "data": {"trades": []}},
    )

    result = asyncio.run(
        tradehub.get_tradehub_open_trades(account_type="demo", instrument="EUR_USD")
    )

    assert result["trades"] == []
    assert _FakeAsyncClient.requests[-1]["payload"]["account_type"] == "demo"
    assert _FakeAsyncClient.requests[-1]["payload"]["instrument"] == "EUR_USD"


@pytest.mark.parametrize("period", ["daily", "weekly", "monthly", "all"])
def test_get_tradehub_performance_supports_all_periods(period):
    _set_response(
        "/api/cos/performance",
        200,
        {"success": True, "data": {"period": period, "realized_pl": 12.5}},
    )

    result = asyncio.run(tradehub.get_tradehub_performance(period=period))

    assert result["period"] == period
    assert _FakeAsyncClient.requests[-1]["payload"]["period"] == period


def test_get_tradehub_runtime_returns_data():
    _set_response(
        "/api/cos/runtime",
        200,
        {"success": True, "data": {"desired_state": "RUNNING", "actual_state": "RUNNING"}},
    )

    result = asyncio.run(tradehub.get_tradehub_runtime())

    assert result["desired_state"] == "RUNNING"
    assert result["actual_state"] == "RUNNING"


def test_get_tradehub_actions_returns_data():
    _set_response(
        "/api/cos/actions",
        200,
        {"success": True, "data": {"actions": [{"request_id": "wd-cos-trade-1"}]}},
    )

    result = asyncio.run(tradehub.get_tradehub_actions(limit=10))

    assert result["actions"][0]["request_id"] == "wd-cos-trade-1"


# ---------------------------------------------------------------------------
# 10-13: error handling
# ---------------------------------------------------------------------------


def test_missing_api_key_fails_safely(monkeypatch):
    monkeypatch.setattr(tradehub, "TRADEHUB_COS_API_KEY", None)

    with pytest.raises(tradehub.TradeHubError):
        asyncio.run(tradehub.get_tradehub_status())


def test_bad_authentication_is_surfaced_cleanly():
    _set_response("/api/cos/status", 401, {"success": False, "message": "invalid key"})

    with pytest.raises(tradehub.TradeHubError) as exc_info:
        asyncio.run(tradehub.get_tradehub_status())

    assert "authentication failed" in str(exc_info.value).lower()


def test_timeout_is_handled():
    _FakeAsyncClient.raise_timeout = True

    with pytest.raises(tradehub.TradeHubTimeoutError) as exc_info:
        asyncio.run(tradehub.get_tradehub_status())

    assert "timed out" in str(exc_info.value).lower()


def test_envelope_success_false_is_handled():
    _set_response(
        "/api/cos/status", 200, {"success": False, "message": "database offline"}
    )

    with pytest.raises(tradehub.TradeHubError) as exc_info:
        asyncio.run(tradehub.get_tradehub_status())

    assert "database offline" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 14-18: execution authority model (approval + demo/live gates)
# ---------------------------------------------------------------------------


def test_demo_trade_request_with_approval_succeeds():
    _set_response(
        "/api/cos/trade-request",
        200,
        {"success": True, "data": {"request_id": "wd-cos-trade-abc", "status": "ACCEPTED"}},
    )

    result = asyncio.run(
        tradehub.request_tradehub_trade(
            account_type="demo",
            instrument="EUR_USD",
            side="buy",
            approval_confirmed=True,
        )
    )

    assert result["status"] == "ACCEPTED"


def test_live_trade_request_is_blocked_by_default():
    with pytest.raises(tradehub.TradeHubApprovalError):
        asyncio.run(
            tradehub.request_tradehub_trade(
                account_type="live",
                instrument="EUR_USD",
                side="buy",
                approval_confirmed=True,
            )
        )

    # The disabled live-execution gate must block the request before any
    # network call is made.
    assert _FakeAsyncClient.requests == []


def test_live_trade_requires_cos_live_flag(monkeypatch):
    monkeypatch.setattr(tradehub, "COS_TRADEHUB_LIVE_EXECUTION_ENABLED", True)
    _set_response(
        "/api/cos/trade-request",
        200,
        {"success": True, "data": {"status": "ACCEPTED"}},
    )

    result = asyncio.run(
        tradehub.request_tradehub_trade(
            account_type="live",
            instrument="EUR_USD",
            side="buy",
            approval_confirmed=True,
        )
    )

    assert result["status"] == "ACCEPTED"


def test_trade_request_without_approval_is_blocked():
    with pytest.raises(tradehub.TradeHubApprovalError):
        asyncio.run(
            tradehub.request_tradehub_trade(
                account_type="demo",
                instrument="EUR_USD",
                side="buy",
                approval_confirmed=False,
            )
        )

    assert _FakeAsyncClient.requests == []


def test_close_request_without_approval_is_blocked():
    with pytest.raises(tradehub.TradeHubApprovalError):
        asyncio.run(
            tradehub.request_tradehub_close(
                broker="oanda",
                account_type="demo",
                trade_id="t-1",
                approval_confirmed=False,
            )
        )

    assert _FakeAsyncClient.requests == []


# ---------------------------------------------------------------------------
# 19-20: request-id stability and ambiguous-timeout safety
# ---------------------------------------------------------------------------


def test_request_id_remains_stable_across_reconciliation():
    fixed_request_id = "wd-cos-trade-fixed-123"
    _set_response(
        "/api/cos/actions",
        200,
        {
            "success": True,
            "data": {
                "actions": [
                    {"request_id": fixed_request_id, "execution_status": "COMPLETED"}
                ]
            },
        },
    )

    result = asyncio.run(tradehub.reconcile_action_by_request_id(fixed_request_id))

    assert result["request_id"] == fixed_request_id
    assert result["execution_status"] == "COMPLETED"


def test_timeout_does_not_auto_generate_new_request_id():
    _FakeAsyncClient.raise_timeout = True
    fixed_request_id = "wd-cos-trade-preassigned"

    with pytest.raises(tradehub.TradeHubTimeoutError) as exc_info:
        asyncio.run(
            tradehub.request_tradehub_trade(
                account_type="demo",
                instrument="EUR_USD",
                side="buy",
                approval_confirmed=True,
                request_id=fixed_request_id,
            )
        )

    # The original caller-supplied request_id must be preserved on the
    # exception and in the outbound payload - never silently replaced.
    assert exc_info.value.request_id == fixed_request_id
    assert _FakeAsyncClient.requests[-1]["payload"]["request_id"] == fixed_request_id


# ---------------------------------------------------------------------------
# 21: executive summary resilience
# ---------------------------------------------------------------------------


def test_executive_summary_snapshot_handles_unavailable_tradehub(monkeypatch):
    monkeypatch.setattr(tradehub, "TRADEHUB_COS_API_KEY", None)

    result = asyncio.run(tradehub.get_tradehub_executive_summary())

    assert result["available"] is False
    assert result["status"] == "UNAVAILABLE"
    assert result["alerts"][0]["area"] == "TradeHub"


def test_executive_summary_endpoint_does_not_fail_when_tradehub_unavailable(client):
    async def fake_tradehub_summary():
        return {"available": False, "status": "UNAVAILABLE", "alerts": []}

    async def fake_get_account_summary(account_key):
        return {
            "account_key": account_key,
            "name": account_key,
            "relationship": "internal",
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

    async def fake_get_active_project_activity():
        return {"projects": []}

    from unittest.mock import patch

    with patch.object(main, "get_tradehub_executive_summary", fake_tradehub_summary), \
         patch.object(main, "get_account_summary", fake_get_account_summary), \
         patch.object(main, "get_active_project_activity", fake_get_active_project_activity):

        response = client.get("/executive-summary", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()

    assert body["trading"]["available"] is False
    assert body["trading"]["status"] == "UNAVAILABLE"
    assert body["source_health"]["tradehub"] is False
    assert body["source_health_status"]["tradehub"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# 22: OpenAPI / connector action exposure
# ---------------------------------------------------------------------------

# operation_id -> (method, path)
EXPECTED_TRADEHUB_OPERATIONS = {
    "getTradeHubStatus": ("get", "/tradehub/status"),
    "getTradeHubAccounts": ("get", "/tradehub/accounts"),
    "getTradeHubOpenTrades": ("get", "/tradehub/open-trades"),
    "getTradeHubTradeHistory": ("get", "/tradehub/trades"),
    "getTradeHubPerformance": ("get", "/tradehub/performance"),
    "getTradeHubRuntime": ("get", "/tradehub/runtime"),
    "getTradeHubActions": ("get", "/tradehub/actions"),
    "requestTradeHubTrade": ("post", "/tradehub/trade-request"),
    "requestTradeHubClose": ("post", "/tradehub/close-request"),
}


@pytest.mark.parametrize(
    "operation_id,method,path", [
        (op_id, method, path)
        for op_id, (method, path) in EXPECTED_TRADEHUB_OPERATIONS.items()
    ]
)
def test_tradehub_action_appears_in_openapi_schema(client, operation_id, method, path):
    schema = client.get("/openapi.json").json()

    path_item = schema["paths"].get(path)
    assert path_item is not None, "{} missing from OpenAPI paths".format(path)

    operation = path_item.get(method)
    assert operation is not None, "{} {} missing from OpenAPI schema".format(method.upper(), path)
    assert operation["operationId"] == operation_id


def test_all_nine_tradehub_actions_are_registered_as_fastapi_routes():
    registered = {
        (route.path, method)
        for route in main.app.routes
        for method in getattr(route, "methods", set())
    }

    for operation_id, (method, path) in EXPECTED_TRADEHUB_OPERATIONS.items():
        assert (path, method.upper()) in registered, (
            "{} ({}) is not a registered FastAPI route".format(operation_id, path)
        )
