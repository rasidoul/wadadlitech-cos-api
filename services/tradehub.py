import json
import logging
import os
import uuid

from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("wadadlitech.tradehub")

TRADEHUB_BASE_URL = os.getenv(
    "TRADEHUB_BASE_URL",
    "https://tradehub-7l1b.onrender.com",
).rstrip("/")

TRADEHUB_COS_API_KEY = os.getenv(
    "TRADEHUB_COS_API_KEY", ""
).strip()

TRADEHUB_TIMEOUT_SECONDS = float(
    os.getenv("TRADEHUB_TIMEOUT_SECONDS", "15")
)

# Independent COS-side safety gate. TradeHub separately enforces its own
# ALLOW_LIVE_TRADING / COS_LIVE_TRADE_EXECUTION_ENABLED - this is defense in
# depth, not a replacement for those controls. Safe default: disabled.
COS_TRADEHUB_LIVE_EXECUTION_ENABLED = (
    os.getenv("COS_TRADEHUB_LIVE_EXECUTION_ENABLED", "false")
    .strip()
    .lower()
    == "true"
)

API_PREFIX = "/api/cos"

LIVE_ACCOUNT_TYPES = {"live"}


class TradeHubError(Exception):
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_type: Optional[str] = None,
        response_text: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type or _classify_error_type(status_code)
        self.response_text = response_text


class TradeHubTimeoutError(TradeHubError):
    """A TradeHub request timed out ambiguously.

    Callers MUST NOT automatically retry an execution request with a new
    request_id on this error - the broker action may already have occurred.
    Use reconcile_action_by_request_id(request_id) to determine what
    actually happened before deciding whether/how to proceed.
    """

    def __init__(self, message: str, request_id: Optional[str] = None):
        super().__init__(message, status_code=None, error_type="TIMEOUT")
        self.request_id = request_id


class TradeHubApprovalError(TradeHubError):
    """A financial execution request was missing required approval or was
    blocked by the COS live-execution safety gate."""


def _classify_error_type(status_code: Optional[int]) -> str:

    if status_code is None:
        return "CONNECTION_ERROR"

    if status_code in (401, 403):
        return "AUTH_ERROR"

    if status_code == 404:
        return "NOT_FOUND"

    if status_code == 429:
        return "RATE_LIMITED"

    if status_code >= 500:
        return "UPSTREAM_SERVER_ERROR"

    return "UPSTREAM_ERROR"


_FRIENDLY_MESSAGES = {
    "AUTH_ERROR": "TradeHub authentication failed.",
    "NOT_FOUND": "TradeHub endpoint not found.",
    "RATE_LIMITED": "TradeHub rate limit exceeded.",
    "UPSTREAM_SERVER_ERROR": "TradeHub service unavailable.",
    "CONNECTION_ERROR": "TradeHub service unavailable.",
    "TIMEOUT": "TradeHub request timed out.",
    "INVALID_RESPONSE": "TradeHub returned an invalid response.",
}


def _friendly_message(error_type: str, fallback: Optional[str] = None) -> str:
    return _FRIENDLY_MESSAGES.get(error_type, fallback or "TradeHub request failed.")


def _require_config():
    if not TRADEHUB_COS_API_KEY:
        raise TradeHubError(
            "TRADEHUB_COS_API_KEY is not configured.",
            error_type="CONFIG_ERROR",
        )


def _headers() -> Dict[str, str]:
    _require_config()

    return {
        "Authorization": "Bearer {}".format(TRADEHUB_COS_API_KEY),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _clean_params(params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in params.items()
        if value is not None
    }


def _extract_envelope_message(body: Dict[str, Any]) -> str:

    message = body.get("message") or body.get("error")

    if isinstance(message, str) and message:
        return message

    return "Trade request was rejected by TradeHub."


async def _request(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_payload: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> Any:

    _require_config()

    url = "{}{}".format(TRADEHUB_BASE_URL, path)

    try:
        async with httpx.AsyncClient(
            timeout=TRADEHUB_TIMEOUT_SECONDS
        ) as client:

            if method == "GET":
                response = await client.get(
                    url,
                    headers=_headers(),
                    params=params,
                )
            else:
                response = await client.post(
                    url,
                    headers=_headers(),
                    json=json_payload or {},
                )

    except httpx.TimeoutException:
        logger.warning(
            "TradeHub request timed out method=%s path=%s request_id=%s",
            method,
            path,
            request_id,
        )
        raise TradeHubTimeoutError(
            _friendly_message("TIMEOUT"),
            request_id=request_id,
        )

    except httpx.RequestError as exc:
        logger.warning(
            "TradeHub connection error method=%s path=%s error=%s",
            method,
            path,
            type(exc).__name__,
        )
        raise TradeHubError(
            _friendly_message("CONNECTION_ERROR"),
            error_type="CONNECTION_ERROR",
        )

    if response.status_code >= 400:
        error_type = _classify_error_type(response.status_code)

        logger.warning(
            "TradeHub returned error status=%s path=%s error_type=%s",
            response.status_code,
            path,
            error_type,
        )

        raise TradeHubError(
            _friendly_message(error_type),
            status_code=response.status_code,
            error_type=error_type,
        )

    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        raise TradeHubError(
            _friendly_message("INVALID_RESPONSE"),
            error_type="INVALID_RESPONSE",
        )

    if isinstance(body, dict) and body.get("success") is False:
        raise TradeHubError(
            _extract_envelope_message(body),
            error_type="ENVELOPE_ERROR",
        )

    if isinstance(body, dict) and "data" in body:
        return body["data"]

    return body


# =========================================================
# READ-ONLY MONITORING
# =========================================================

async def get_tradehub_status() -> Dict[str, Any]:
    return await _request("GET", "{}/status".format(API_PREFIX))


async def get_tradehub_accounts() -> Dict[str, Any]:
    return await _request("GET", "{}/accounts".format(API_PREFIX))


async def get_tradehub_open_trades(
    account_type: Optional[str] = None,
    instrument: Optional[str] = None,
    broker: Optional[str] = None,
) -> Dict[str, Any]:

    params = _clean_params(
        {
            "account_type": account_type,
            "instrument": instrument,
            "broker": broker,
        }
    )

    return await _request(
        "GET",
        "{}/open-trades".format(API_PREFIX),
        params=params,
    )


async def get_tradehub_trades(
    period: Optional[str] = None,
    instrument: Optional[str] = None,
    account_type: Optional[str] = None,
    broker: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:

    params = _clean_params(
        {
            "period": period,
            "instrument": instrument,
            "account_type": account_type,
            "broker": broker,
            "limit": limit,
        }
    )

    return await _request(
        "GET",
        "{}/trades".format(API_PREFIX),
        params=params,
    )


async def get_tradehub_performance(
    period: str = "all",
    instrument: Optional[str] = None,
    account_type: Optional[str] = None,
    broker: Optional[str] = None,
) -> Dict[str, Any]:

    params = _clean_params(
        {
            "period": period,
            "instrument": instrument,
            "account_type": account_type,
            "broker": broker,
        }
    )

    return await _request(
        "GET",
        "{}/performance".format(API_PREFIX),
        params=params,
    )


async def get_tradehub_runtime() -> Dict[str, Any]:
    return await _request("GET", "{}/runtime".format(API_PREFIX))


async def get_tradehub_actions(
    period: Optional[str] = None,
    action_type: Optional[str] = None,
    execution_status: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:

    params = _clean_params(
        {
            "period": period,
            "action_type": action_type,
            "execution_status": execution_status,
            "limit": limit,
        }
    )

    return await _request(
        "GET",
        "{}/actions".format(API_PREFIX),
        params=params,
    )


# =========================================================
# REQUEST IDS
# =========================================================

def generate_trade_request_id() -> str:
    return "wd-cos-trade-{}".format(uuid.uuid4())


def generate_close_request_id() -> str:
    return "wd-cos-close-{}".format(uuid.uuid4())


# =========================================================
# EXECUTION AUTHORITY MODEL (READ_ONLY vs FINANCIAL_EXECUTION)
# =========================================================

def _validate_execution_request(account_type: str, approval_confirmed: bool):

    if not approval_confirmed:
        raise TradeHubApprovalError(
            "Trade execution requires explicit approval_confirmed=true.",
            error_type="APPROVAL_REQUIRED",
        )

    is_live = (account_type or "").strip().lower() in LIVE_ACCOUNT_TYPES

    if is_live and not COS_TRADEHUB_LIVE_EXECUTION_ENABLED:
        raise TradeHubApprovalError(
            "Live trade execution is disabled by "
            "COS_TRADEHUB_LIVE_EXECUTION_ENABLED.",
            error_type="LIVE_EXECUTION_DISABLED",
        )


async def request_tradehub_trade(
    account_type: str,
    instrument: str,
    side: str,
    size: Optional[float] = None,
    risk: Optional[Dict[str, Any]] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    reason: Optional[str] = None,
    requested_by: str = "WD-AI-001",
    source: str = "COS",
    approval_confirmed: bool = False,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:

    _validate_execution_request(account_type, approval_confirmed)

    # Preserve a caller-supplied request_id (e.g. a reconciliation retry) -
    # never mint a new one behind the caller's back.
    resolved_request_id = request_id or generate_trade_request_id()

    payload = _clean_params(
        {
            "request_id": resolved_request_id,
            "account_type": account_type,
            "instrument": instrument,
            "side": side,
            "size": size,
            "risk": risk,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "reason": reason,
            "requested_by": requested_by,
            "source": source,
            "approval_confirmed": approval_confirmed,
        }
    )

    return await _request(
        "POST",
        "{}/trade-request".format(API_PREFIX),
        json_payload=payload,
        request_id=resolved_request_id,
    )


async def request_tradehub_close(
    broker: str,
    account_type: str,
    trade_id: str,
    close_type: str = "full",
    size: Optional[float] = None,
    reason: Optional[str] = None,
    requested_by: str = "WD-AI-001",
    source: str = "COS",
    approval_confirmed: bool = False,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:

    _validate_execution_request(account_type, approval_confirmed)

    resolved_request_id = request_id or generate_close_request_id()

    payload = _clean_params(
        {
            "request_id": resolved_request_id,
            "broker": broker,
            "account_type": account_type,
            "trade_id": trade_id,
            "close_type": close_type,
            "size": size,
            "reason": reason,
            "requested_by": requested_by,
            "source": source,
            "approval_confirmed": approval_confirmed,
        }
    )

    return await _request(
        "POST",
        "{}/close-request".format(API_PREFIX),
        json_payload=payload,
        request_id=resolved_request_id,
    )


# =========================================================
# RECONCILIATION (ambiguous timeout recovery)
# =========================================================

async def reconcile_action_by_request_id(
    request_id: str,
) -> Optional[Dict[str, Any]]:
    """Look up a previously submitted trade/close request in TradeHub's
    action history by request_id.

    Call this after a TradeHubTimeoutError instead of blindly resending the
    request with a new ID - the original request may have already been
    applied by TradeHub.
    """

    actions = await get_tradehub_actions(limit=100)

    items = actions.get("actions") if isinstance(actions, dict) else actions

    if not isinstance(items, list):
        return None

    for item in items:
        if isinstance(item, dict) and item.get("request_id") == request_id:
            return item

    return None


# =========================================================
# CONNECTION HEALTH
# =========================================================

async def get_tradehub_integration_status() -> Dict[str, Any]:
    """Sanitized configured/reachable/authenticated/healthy report.

    Never returns TRADEHUB_COS_API_KEY or raises - callers (including the
    executive summary) can rely on this always returning a usable dict.
    """

    configured = bool(TRADEHUB_COS_API_KEY)

    if not configured:
        return {
            "configured": False,
            "reachable": False,
            "authenticated": False,
            "status": "UNAVAILABLE",
            "message": "TRADEHUB_COS_API_KEY is not configured.",
        }

    try:
        status_payload = await get_tradehub_status()

    except TradeHubError as exc:

        if exc.error_type == "AUTH_ERROR":
            return {
                "configured": True,
                "reachable": True,
                "authenticated": False,
                "status": "UNAVAILABLE",
                "message": "TradeHub authentication failed.",
            }

        return {
            "configured": True,
            "reachable": False,
            "authenticated": False,
            "status": "UNAVAILABLE",
            "message": str(exc),
        }

    application_healthy = True

    if isinstance(status_payload, dict):
        db_status = status_payload.get("database_status") or status_payload.get("database")
        app_status = status_payload.get("status") or status_payload.get("application_status")

        unhealthy_values = {"DOWN", "UNHEALTHY", "ERROR"}

        if str(app_status or "").upper() in unhealthy_values:
            application_healthy = False

        if str(db_status or "").upper() in unhealthy_values:
            application_healthy = False

    return {
        "configured": True,
        "reachable": True,
        "authenticated": True,
        "status": "HEALTHY" if application_healthy else "DEGRADED",
        "data": status_payload,
    }


# =========================================================
# EXECUTIVE SUMMARY SNAPSHOT
# =========================================================

async def get_tradehub_executive_summary() -> Dict[str, Any]:
    """Compact TradeHub snapshot for the executive summary: health, runtime,
    today's P/L, open trade count, and any elevated alerts.

    Field names are read defensively (multiple fallbacks) since this client
    cannot inspect TradeHub's source. Verify against TradeHub's actual
    response shape once connectivity is confirmed.

    Never raises - a TradeHub outage must never fail the whole briefing.
    """

    integration_status = await get_tradehub_integration_status()

    alerts: List[Dict[str, str]] = []

    if integration_status["status"] != "HEALTHY":
        alerts.append(
            {
                "severity": "P2",
                "area": "TradeHub",
                "message": integration_status.get(
                    "message", "TradeHub is unavailable."
                ),
            }
        )

        return {
            "available": False,
            "status": integration_status["status"],
            "alerts": alerts,
        }

    runtime = None
    performance_daily = None
    open_trades = None

    try:
        runtime = await get_tradehub_runtime()
    except TradeHubError:
        runtime = None

    try:
        performance_daily = await get_tradehub_performance(period="daily")
    except TradeHubError:
        performance_daily = None

    try:
        open_trades = await get_tradehub_open_trades()
    except TradeHubError:
        open_trades = None

    if isinstance(runtime, dict):
        desired = runtime.get("desired_state") or runtime.get("configured_state")
        actual = runtime.get("actual_state") or runtime.get("worker_state")

        running_values = {"RUNNING", "ENABLED", "ON", "ACTIVE"}

        if (
            desired is not None
            and actual is not None
            and str(desired).upper() in running_values
            and str(actual).upper() not in running_values
        ):
            alerts.append(
                {
                    "severity": "P2",
                    "area": "TradeHub",
                    "message": (
                        "Auto-trading is configured to run but the "
                        "runtime worker is not active."
                    ),
                }
            )

    open_trade_count = None

    if isinstance(open_trades, dict):
        trades_list = open_trades.get("trades") or open_trades.get("open_trades")

        if isinstance(trades_list, list):
            open_trade_count = len(trades_list)
        elif isinstance(open_trades.get("count"), int):
            open_trade_count = open_trades.get("count")

    realized_pl = None
    unrealized_pl = None

    if isinstance(performance_daily, dict):
        realized_pl = performance_daily.get("realized_pl")
        unrealized_pl = performance_daily.get("unrealized_pl")

    return {
        "available": True,
        "status": integration_status["status"],
        "runtime": runtime,
        "today_realized_pl": realized_pl,
        "today_unrealized_pl": unrealized_pl,
        "open_trade_count": open_trade_count,
        "alerts": alerts,
    }
