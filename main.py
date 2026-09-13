import os
import asyncio

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from dotenv import load_dotenv

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Query,
    Security,
)

from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from services.tasks import (
    TaskError,
    create_task,
    get_task,
    get_tasks,
    update_task,
    complete_task,
    delete_task,
    get_today_tasks,
    get_overdue_tasks,
    get_priority_tasks,
)

from services.highlevel import (
    HighLevelError,
    get_registered_accounts,
    get_location,
    get_pipelines,
    get_workflows,
    get_tags,
    get_conversations,
    search_contacts,
    search_opportunities,
    get_agent_studio_agents,
    get_account_summary,
)

from services.google import (
    GoogleError,
    get_google_status,
    get_google_integration_status,
    log_google_startup_diagnostics,
    check_gmail_connection,
    check_calendar_connection,
    check_drive_connection,
    search_gmail_messages,
    get_gmail_message,
    search_calendar_events,
    search_drive_files,
    get_drive_file,
    get_drive_file_content,
)

from config.agents import (
    get_registered_agents,
    get_agent,
    get_webhook_env_var,
)

from services.agent_briefs import (
    AgentBriefError,
    create_brief,
    get_brief,
    list_briefs,
    update_brief_fields,
    acknowledge_brief,
    complete_brief,
    retry_delivery,
    get_brief_stats,
)

from services.tradehub import (
    TradeHubError,
    TradeHubTimeoutError,
    TradeHubApprovalError,
    get_tradehub_status,
    get_tradehub_accounts,
    get_tradehub_open_trades,
    get_tradehub_trades,
    get_tradehub_performance,
    get_tradehub_runtime,
    get_tradehub_actions,
    request_tradehub_trade,
    request_tradehub_close,
    get_tradehub_integration_status,
    get_tradehub_executive_summary,
)

# New services for personal/business planning
from services.google_auth_enhanced import (
    get_access_token,
    get_google_integration_diagnostics,
)

from services.gmail_operations import (
    GmailError,
    search_messages,
    get_message,
    get_thread,
    list_labels,
    create_label,
    apply_label,
    remove_label,
    archive_message,
    create_draft,
    update_draft,
    send_draft,
    get_email_context,
)

from services.calendar_operations import (
    CalendarError,
    list_calendars,
    get_events,
    check_availability,
    create_focus_block,
    update_focus_block,
    cancel_focus_block,
)

from services.reminders_operations import (
    RemindersError,
    get_pending_sync,
    acknowledge_sync,
)

from services.planning_operations import (
    PlanningError,
    get_daily_plan,
    get_weekly_review,
)


load_dotenv()

COS_API_KEY = os.getenv("COS_API_KEY")


# =========================================================
# FASTAPI
# =========================================================

@asynccontextmanager
async def _lifespan(app: FastAPI):
    log_google_startup_diagnostics()
    yield


app = FastAPI(
    title="WadadliTech Chief of Staff API",
    description=(
        "Secure middleware between WD-AI-001 "
        "and WadadliTech business systems."
    ),
    version="2.2.0",
    lifespan=_lifespan,
)


original_openapi = app.openapi


def custom_openapi():
    """Attach GPT-specific metadata to consequential actions."""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = original_openapi()
    consequential_ops = {
        "sendGmailDraft": {
            "x-openai-isConsequential": True,
            "x-openai-require-approval": True,
            "x-openai-approval-message": "This action sends an email and requires explicit user approval before execution.",
        },
        "createFocusBlock": {
            "x-openai-isConsequential": True,
            "x-openai-require-approval": True,
            "x-openai-approval-message": "This action changes your calendar and requires explicit user approval before execution.",
        },
        "acknowledgeReminderSync": {
            "x-openai-isConsequential": True,
            "x-openai-require-approval": True,
            "x-openai-approval-message": "This action updates reminders sync state and requires explicit user approval before execution.",
        },
    }

    for path_item in openapi_schema.get("paths", {}).values():
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            op_id = operation.get("operationId")
            if op_id in consequential_ops:
                operation.update(consequential_ops[op_id])

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


# =========================================================
# REQUEST MODELS
# =========================================================

class TaskCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    department: Optional[str] = None
    project: Optional[str] = None
    client: Optional[str] = None
    owner: Optional[str] = "Jermain Gordon"
    priority: str = "NORMAL"
    due_date: Optional[str] = None
    next_action: Optional[str] = None
    blocker: Optional[str] = None
    source: str = "COS"
    # Planning fields
    domain: Optional[str] = None  # 'PERSONAL' or 'BUSINESS'
    goal_links: Optional[List[str]] = None  # Goal/project IDs
    estimated_minutes: Optional[int] = None
    deadline: Optional[str] = None  # Real deadline (ISO date)
    planned_work_date: Optional[str] = None  # Planned execution (ISO datetime)
    dependencies: Optional[List[int]] = None  # Task IDs this depends on
    waiting_for_date: Optional[str] = None  # Follow-up date for blockers
    postponement_count: int = 0
    source_system: Optional[str] = None  # gmail, calendar, reminders, etc.
    external_id: Optional[str] = None  # Stable source system ID
    gmail_thread_id: Optional[str] = None
    gmail_message_ids: Optional[List[str]] = None
    calendar_event_id: Optional[str] = None
    reminders_id: Optional[str] = None
    reminders_list: Optional[str] = None


class TaskUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    department: Optional[str] = None
    project: Optional[str] = None
    client: Optional[str] = None
    owner: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    due_date: Optional[str] = None
    next_action: Optional[str] = None
    blocker: Optional[str] = None
    # Planning fields
    domain: Optional[str] = None
    goal_links: Optional[List[str]] = None
    estimated_minutes: Optional[int] = None
    deadline: Optional[str] = None
    planned_work_date: Optional[str] = None
    dependencies: Optional[List[int]] = None
    waiting_for_date: Optional[str] = None
    postponement_count: Optional[int] = None
    source_system: Optional[str] = None
    external_id: Optional[str] = None
    gmail_thread_id: Optional[str] = None
    gmail_message_ids: Optional[List[str]] = None
    calendar_event_id: Optional[str] = None
    reminders_id: Optional[str] = None
    reminders_list: Optional[str] = None
    sync_version: Optional[int] = None
    pending_commands: Optional[List[Dict[str, Any]]] = None
    conflict_flags: Optional[List[str]] = None
    reminders_sync_version: Optional[int] = None
    reminders_last_sync: Optional[str] = None
    actual_duration_minutes: Optional[int] = None


class AgentBriefCreateRequest(BaseModel):
    title: str
    summary: Optional[str] = None
    brief_type: str = "INTELLIGENCE_BRIEF"
    source_agent_id: str = "WD-AI-001"
    priority: str = "NORMAL"
    reporting_period_start: Optional[str] = None
    reporting_period_end: Optional[str] = None
    sections: List[Dict[str, Any]]
    practical_move: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    requires_human_approval: bool = False
    metadata: Optional[Dict[str, Any]] = None
    external_reference: Optional[str] = None


class AgentBriefUpdateRequest(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    practical_move: Optional[str] = None
    tags: Optional[List[str]] = None
    requires_human_approval: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None


class TradeHubTradeRequest(BaseModel):
    account_type: str
    instrument: str
    side: str
    size: Optional[float] = None
    risk: Optional[Dict[str, Any]] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: Optional[str] = None
    requested_by: str = "WD-AI-001"
    source: str = "COS"
    approval_confirmed: bool = False
    request_id: Optional[str] = None


class TradeHubCloseRequest(BaseModel):
    broker: str
    account_type: str
    trade_id: str
    close_type: str = "full"
    size: Optional[float] = None
    reason: Optional[str] = None
    requested_by: str = "WD-AI-001"
    source: str = "COS"
    approval_confirmed: bool = False
    request_id: Optional[str] = None


# =========================================================
# SECURITY
# =========================================================

security = HTTPBearer(
    scheme_name="COS API Key",
    description="Enter the WadadliTech Chief of Staff API key.",
)


def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    if not COS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="COS_API_KEY is not configured.",
        )

    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication is required.",
        )

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Bearer authentication is required.",
        )

    if credentials.credentials != COS_API_KEY:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key.",
        )

    return True


# =========================================================
# SERVICE ERROR HANDLERS
# =========================================================

async def run_highlevel_call(
    callable_obj,
    *args,
    **kwargs
):
    try:
        return await callable_obj(
            *args,
            **kwargs
        )

    except HighLevelError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )


async def run_google_call(
    callable_obj,
    *args,
    **kwargs
):
    try:
        return await callable_obj(
            *args,
            **kwargs
        )

    except GoogleError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )


async def run_tradehub_call(
    callable_obj,
    *args,
    **kwargs
):
    try:
        return await callable_obj(
            *args,
            **kwargs
        )

    except TradeHubApprovalError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        )

    except TradeHubTimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                "TradeHub request timed out for request_id '{}'. The action "
                "may already have been applied - check /tradehub/actions "
                "before retrying.".format(exc.request_id)
            ),
        )

    except TradeHubError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )


def run_task_call(
    callable_obj,
    *args,
    **kwargs
):
    try:
        return callable_obj(
            *args,
            **kwargs
        )

    except TaskError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


async def run_agent_brief_call(
    callable_obj,
    *args,
    **kwargs
):
    try:
        result = callable_obj(
            *args,
            **kwargs
        )

        if asyncio.iscoroutine(result):
            result = await result

        return result

    except AgentBriefError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        )


async def safe_call(
    source_name,
    callable_obj,
    *args,
    **kwargs
):
    try:
        result = await callable_obj(
            *args,
            **kwargs
        )

        return {
            "source": source_name,
            "available": True,
            "data": result,
            "error": None,
        }

    except Exception as exc:
        return {
            "source": source_name,
            "available": False,
            "data": None,
            "error": str(exc),
        }


# =========================================================
# BASIC HEALTH
# =========================================================

@app.get("/")
async def root():
    return {
        "service": "WadadliTech Chief of Staff API",
        "status": "online",
        "version": "2.2.0",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "wadadlitech-cos-api",
        "version": "2.2.0",
    }


# =========================================================
# HIGHLEVEL ACCOUNT REGISTRY
# =========================================================

@app.get("/highlevel/accounts")
async def highlevel_accounts(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return get_registered_accounts()


# =========================================================
# HIGHLEVEL ACCOUNT STATUS
# =========================================================

@app.get(
    "/highlevel/accounts/{account_key}/status"
)
async def highlevel_account_status(
    account_key: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    result = await run_highlevel_call(
        get_location,
        account_key,
    )

    location = result.get(
        "location",
        result,
    )

    return {
        "account_key": account_key,
        "connected": True,
        "location_id": location.get("id"),
        "location_name": location.get("name"),
        "timezone": location.get("timezone"),
    }


# =========================================================
# HIGHLEVEL SUMMARY BY ACCOUNT
# =========================================================

@app.get(
    "/highlevel/accounts/{account_key}/summary"
)
async def highlevel_account_summary(
    account_key: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        get_account_summary,
        account_key,
    )


# =========================================================
# HIGHLEVEL CONTACTS
# =========================================================

@app.get(
    "/highlevel/accounts/{account_key}/contacts"
)
async def highlevel_contacts(
    account_key: str,
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    query: Optional[str] = Query(
        default=None
    ),
    cursor: Optional[str] = Query(
        default=None
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        search_contacts,
        account_key,
        limit,
        query,
        cursor,
    )


# =========================================================
# HIGHLEVEL OPPORTUNITIES
# =========================================================

@app.get(
    "/highlevel/accounts/{account_key}/opportunities"
)
async def highlevel_opportunities(
    account_key: str,
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    query: Optional[str] = Query(
        default=None
    ),
    status: Optional[str] = Query(
        default=None
    ),
    start_after: Optional[int] = Query(
        default=None
    ),
    start_after_id: Optional[str] = Query(
        default=None
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        search_opportunities,
        account_key,
        limit,
        query,
        status,
        start_after,
        start_after_id,
    )


# =========================================================
# HIGHLEVEL PIPELINES
# =========================================================

@app.get(
    "/highlevel/accounts/{account_key}/pipelines"
)
async def highlevel_pipelines(
    account_key: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        get_pipelines,
        account_key,
    )


# =========================================================
# HIGHLEVEL WORKFLOWS
# =========================================================

@app.get(
    "/highlevel/accounts/{account_key}/workflows"
)
async def highlevel_workflows(
    account_key: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        get_workflows,
        account_key,
    )


# =========================================================
# HIGHLEVEL TAGS
# =========================================================

@app.get(
    "/highlevel/accounts/{account_key}/tags"
)
async def highlevel_tags(
    account_key: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        get_tags,
        account_key,
    )


# =========================================================
# HIGHLEVEL CONVERSATIONS
# =========================================================

@app.get(
    "/highlevel/accounts/{account_key}/conversations"
)
async def highlevel_conversations(
    account_key: str,
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        get_conversations,
        account_key,
        limit,
    )


# =========================================================
# HIGHLEVEL AI AGENTS
# =========================================================

@app.get(
    "/highlevel/accounts/{account_key}/agents"
)
async def highlevel_agents(
    account_key: str,
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    published_only: bool = Query(
        default=False
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        get_agent_studio_agents,
        account_key,
        limit,
        offset,
        published_only,
    )


# =========================================================
# ALL HIGHLEVEL ACCOUNTS SUMMARY
# =========================================================

@app.get(
    "/highlevel/all-accounts-summary"
)
async def highlevel_all_accounts_summary(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    account_keys = [
        "wadadlitech",
        "paradigm",
        "jermaingordon",
    ]

    results = await asyncio.gather(
        *[
            safe_call(
                account_key,
                get_account_summary,
                account_key,
            )
            for account_key
            in account_keys
        ]
    )

    accounts = []

    for result in results:
        if result["available"]:
            accounts.append(
                {
                    "account_key": result["source"],
                    "available": True,
                    "data": result["data"],
                }
            )
        else:
            accounts.append(
                {
                    "account_key": result["source"],
                    "available": False,
                    "error": result["error"],
                }
            )

    return {
        "account_count": len(accounts),
        "accounts": accounts,
    }


# =========================================================
# BACKWARD-COMPATIBILITY HIGHLEVEL ROUTES
# =========================================================

@app.get("/highlevel/status")
async def legacy_highlevel_status(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    result = await run_highlevel_call(
        get_location,
        "wadadlitech",
    )

    location = result.get(
        "location",
        result,
    )

    return {
        "connected": True,
        "location_id": location.get("id"),
        "location_name": location.get("name"),
        "timezone": location.get("timezone"),
    }


@app.get("/highlevel/contacts")
async def legacy_highlevel_contacts(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    query: Optional[str] = Query(
        default=None
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        search_contacts,
        "wadadlitech",
        limit,
        query,
    )


@app.get("/highlevel/opportunities")
async def legacy_highlevel_opportunities(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    query: Optional[str] = Query(
        default=None
    ),
    status: Optional[str] = Query(
        default=None
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        search_opportunities,
        "wadadlitech",
        limit,
        query,
        status,
    )


@app.get("/highlevel/pipelines")
async def legacy_highlevel_pipelines(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        get_pipelines,
        "wadadlitech",
    )


@app.get("/highlevel/workflows")
async def legacy_highlevel_workflows(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        get_workflows,
        "wadadlitech",
    )


@app.get("/highlevel/agents")
async def legacy_highlevel_agents(
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    published_only: bool = Query(
        default=False
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_highlevel_call(
        get_agent_studio_agents,
        "wadadlitech",
        limit,
        offset,
        published_only,
    )


# =========================================================
# GOOGLE
# =========================================================

@app.get("/google/status", operation_id="getGoogleStatus")
async def google_status(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_google_call(
        get_google_status
    )


@app.get(
    "/google/gmail/messages",
    operation_id="legacySearchGmailMessages",
)
async def api_search_gmail_messages(
    query: Optional[str] = Query(
        default=None,
        description=(
            "Gmail search syntax, e.g. 'from:someone@example.com is:unread'."
        ),
    ),
    limit: int = Query(default=10, ge=1, le=50),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_google_call(
        search_gmail_messages,
        query,
        limit,
    )


@app.get(
    "/google/gmail/messages/{message_id}",
    operation_id="legacyGetGmailMessage",
)
async def api_get_gmail_message(
    message_id: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_google_call(
        get_gmail_message,
        message_id,
        "full",
    )


@app.get(
    "/google/calendar/events",
    operation_id="legacyListCalendarEvents",
)
async def api_list_calendar_events(
    query: Optional[str] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    calendar_id: str = Query(default="primary"),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_google_call(
        search_calendar_events,
        calendar_id,
        query,
        limit,
    )


@app.get(
    "/google/drive/files",
    operation_id="searchDriveFiles",
)
async def api_search_drive_files(
    query: Optional[str] = Query(
        default=None,
        description="Free-text search across accessible Drive file contents.",
    ),
    limit: int = Query(default=10, ge=1, le=50),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_google_call(
        search_drive_files,
        query,
        limit,
    )


@app.get(
    "/google/drive/files/{file_id}",
    operation_id="getDriveFileMetadata",
)
async def api_get_drive_file(
    file_id: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_google_call(
        get_drive_file,
        file_id,
    )


@app.get(
    "/google/drive/files/{file_id}/content",
    operation_id="getDriveFileContent",
)
async def api_get_drive_file_content(
    file_id: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_google_call(
        get_drive_file_content,
        file_id,
    )


# =========================================================
# GOOGLE INTEGRATION DIAGNOSTICS
# =========================================================

@app.get(
    "/integrations/google/status",
    operation_id="getGoogleIntegrationStatus",
)
async def integrations_google_status(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return {
        "google": await get_google_integration_status()
    }


@app.get(
    "/integrations/google/gmail/test",
    operation_id="testGoogleGmailConnection",
)
async def integrations_google_gmail_test(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return {"gmail": await check_gmail_connection()}


@app.get(
    "/integrations/google/calendar/test",
    operation_id="testGoogleCalendarConnection",
)
async def integrations_google_calendar_test(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return {"calendar": await check_calendar_connection()}


@app.get(
    "/integrations/google/drive/test",
    operation_id="testGoogleDriveConnection",
)
async def integrations_google_drive_test(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return {"drive": await check_drive_connection()}


# =========================================================
# GITHUB (deprecated - removed)
# =========================================================
#
# The COS API no longer aggregates GitHub engineering activity (repository,
# commit, issue, pull request, and branch data). Repository access is now
# handled exclusively by the dedicated direct GitHub connector outside this
# API. These routes are kept only as an explicit deprecation notice for any
# existing caller of the old paths.

GITHUB_DEPRECATION_DETAIL = (
    "Deprecated. Engineering repository data is now accessed through the "
    "direct GitHub connector."
)


async def _github_deprecated(
    path: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    raise HTTPException(
        status_code=410,
        detail=GITHUB_DEPRECATION_DETAIL,
    )


for _method, _operation_id in (
    ("GET", "githubDeprecatedGet"),
    ("POST", "githubDeprecatedPost"),
    ("PUT", "githubDeprecatedPut"),
    ("PATCH", "githubDeprecatedPatch"),
    ("DELETE", "githubDeprecatedDelete"),
):
    app.add_api_route(
        "/github/{path:path}",
        _github_deprecated,
        methods=[_method],
        operation_id=_operation_id,
    )


# =========================================================
# TRADEHUB (read-only monitoring)
# =========================================================

@app.get("/tradehub/status", operation_id="getTradeHubStatus")
async def tradehub_status(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_tradehub_call(
        get_tradehub_status
    )


@app.get("/tradehub/accounts", operation_id="getTradeHubAccounts")
async def tradehub_accounts(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_tradehub_call(
        get_tradehub_accounts
    )


@app.get("/tradehub/open-trades", operation_id="getTradeHubOpenTrades")
async def tradehub_open_trades(
    account_type: Optional[str] = Query(default=None),
    instrument: Optional[str] = Query(default=None),
    broker: Optional[str] = Query(default=None),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_tradehub_call(
        get_tradehub_open_trades,
        account_type,
        instrument,
        broker,
    )


@app.get("/tradehub/trades", operation_id="getTradeHubTradeHistory")
async def tradehub_trades(
    period: Optional[str] = Query(default=None),
    instrument: Optional[str] = Query(default=None),
    account_type: Optional[str] = Query(default=None),
    broker: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_tradehub_call(
        get_tradehub_trades,
        period,
        instrument,
        account_type,
        broker,
        limit,
    )


@app.get("/tradehub/performance", operation_id="getTradeHubPerformance")
async def tradehub_performance(
    period: str = Query(default="all"),
    instrument: Optional[str] = Query(default=None),
    account_type: Optional[str] = Query(default=None),
    broker: Optional[str] = Query(default=None),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_tradehub_call(
        get_tradehub_performance,
        period,
        instrument,
        account_type,
        broker,
    )


@app.get("/tradehub/runtime", operation_id="getTradeHubRuntime")
async def tradehub_runtime(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_tradehub_call(
        get_tradehub_runtime
    )


@app.get("/tradehub/actions", operation_id="getTradeHubActions")
async def tradehub_actions(
    period: Optional[str] = Query(default=None),
    action_type: Optional[str] = Query(default=None),
    execution_status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_tradehub_call(
        get_tradehub_actions,
        period,
        action_type,
        execution_status,
        limit,
    )


# =========================================================
# TRADEHUB (financial execution - approval-controlled)
# =========================================================

@app.post("/tradehub/trade-request", operation_id="requestTradeHubTrade")
async def tradehub_trade_request(
    request: TradeHubTradeRequest,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_tradehub_call(
        request_tradehub_trade,
        account_type=request.account_type,
        instrument=request.instrument,
        side=request.side,
        size=request.size,
        risk=request.risk,
        stop_loss=request.stop_loss,
        take_profit=request.take_profit,
        reason=request.reason,
        requested_by=request.requested_by,
        source=request.source,
        approval_confirmed=request.approval_confirmed,
        request_id=request.request_id,
    )


@app.post("/tradehub/close-request", operation_id="requestTradeHubClose")
async def tradehub_close_request(
    request: TradeHubCloseRequest,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_tradehub_call(
        request_tradehub_close,
        broker=request.broker,
        account_type=request.account_type,
        trade_id=request.trade_id,
        close_type=request.close_type,
        size=request.size,
        reason=request.reason,
        requested_by=request.requested_by,
        source=request.source,
        approval_confirmed=request.approval_confirmed,
        request_id=request.request_id,
    )


# =========================================================
# TRADEHUB INTEGRATION DIAGNOSTICS
# =========================================================

@app.get(
    "/integrations/tradehub/status",
    operation_id="getTradeHubIntegrationStatus",
)
async def integrations_tradehub_status(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return {
        "tradehub": await get_tradehub_integration_status()
    }


# =========================================================
# TASK REGISTRY
# =========================================================

@app.post("/tasks")
async def api_create_task(
    request: TaskCreateRequest,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return run_task_call(
        create_task,
        title=request.title,
        description=request.description,
        department=request.department,
        project=request.project,
        client=request.client,
        owner=request.owner,
        priority=request.priority,
        due_date=request.due_date,
        next_action=request.next_action,
        blocker=request.blocker,
        source=request.source,
        domain=request.domain,
        goal_links=request.goal_links,
        estimated_minutes=request.estimated_minutes,
        deadline=request.deadline,
        planned_work_date=request.planned_work_date,
        dependencies=request.dependencies,
        waiting_for_date=request.waiting_for_date,
        postponement_count=request.postponement_count,
        source_system=request.source_system,
        external_id=request.external_id,
        gmail_thread_id=request.gmail_thread_id,
        gmail_message_ids=request.gmail_message_ids,
        calendar_event_id=request.calendar_event_id,
        reminders_id=request.reminders_id,
        reminders_list=request.reminders_list,
    )


@app.get("/tasks")
async def api_get_tasks(
    status: Optional[str] = Query(
        default=None
    ),
    priority: Optional[str] = Query(
        default=None
    ),
    department: Optional[str] = Query(
        default=None
    ),
    project: Optional[str] = Query(
        default=None
    ),
    client: Optional[str] = Query(
        default=None
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return run_task_call(
        get_tasks,
        status,
        priority,
        department,
        project,
        client,
        limit,
    )


@app.get("/tasks/today")
async def api_today_tasks(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return run_task_call(
        get_today_tasks
    )


@app.get("/tasks/overdue")
async def api_overdue_tasks(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return run_task_call(
        get_overdue_tasks
    )


@app.get("/tasks/priorities")
async def api_priority_tasks(
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return run_task_call(
        get_priority_tasks,
        limit,
    )


@app.get("/tasks/{task_id}")
async def api_get_task(
    task_id: int,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return run_task_call(
        get_task,
        task_id,
    )


@app.patch("/tasks/{task_id}")
async def api_update_task(
    task_id: int,
    request: TaskUpdateRequest,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return run_task_call(
        update_task,
        task_id,
        title=request.title,
        description=request.description,
        department=request.department,
        project=request.project,
        client=request.client,
        owner=request.owner,
        priority=request.priority,
        status=request.status,
        due_date=request.due_date,
        next_action=request.next_action,
        blocker=request.blocker,
        domain=request.domain,
        goal_links=request.goal_links,
        estimated_minutes=request.estimated_minutes,
        deadline=request.deadline,
        planned_work_date=request.planned_work_date,
        dependencies=request.dependencies,
        waiting_for_date=request.waiting_for_date,
        postponement_count=request.postponement_count,
        source_system=request.source_system,
        external_id=request.external_id,
        gmail_thread_id=request.gmail_thread_id,
        gmail_message_ids=request.gmail_message_ids,
        calendar_event_id=request.calendar_event_id,
        reminders_id=request.reminders_id,
        reminders_list=request.reminders_list,
        sync_version=request.sync_version,
        pending_commands=request.pending_commands,
        conflict_flags=request.conflict_flags,
        reminders_sync_version=request.reminders_sync_version,
        reminders_last_sync=request.reminders_last_sync,
        actual_duration_minutes=request.actual_duration_minutes,
    )


@app.post("/tasks/{task_id}/complete")
async def api_complete_task(
    task_id: int,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return run_task_call(
        complete_task,
        task_id,
    )


@app.delete("/tasks/{task_id}")
async def api_delete_task(
    task_id: int,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return run_task_call(
        delete_task,
        task_id,
    )


# =========================================================
# AGENT REGISTRY
# =========================================================

def _require_registered_agent(agent_id: str) -> Dict[str, Any]:

    agent = get_agent(agent_id)

    if not agent:
        raise HTTPException(
            status_code=404,
            detail="Unknown agent '{}'.".format(agent_id),
        )

    return agent


@app.get("/agents", operation_id="listAgents")
async def api_list_agents(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return get_registered_agents()


@app.get("/agents/{agent_id}", operation_id="getAgent")
async def api_get_agent(
    agent_id: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return _require_registered_agent(agent_id)


@app.get("/agents/{agent_id}/status", operation_id="getAgentStatus")
async def api_get_agent_status(
    agent_id: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    agent = _require_registered_agent(agent_id)

    stats = await run_agent_brief_call(
        get_brief_stats,
        agent_id,
    )

    env_var = get_webhook_env_var(agent_id)

    webhook_configured = bool(
        os.getenv(env_var)
    ) if env_var else False

    return {
        "agent_id": agent_id,
        "registered": True,
        "status": agent["status"],
        "brief_queue_available": True,
        "webhook_configured": webhook_configured,
        "pending_briefs": stats["pending_briefs"],
        "failed_deliveries": stats["delivery_failures"],
    }


# =========================================================
# AGENT BRIEFS
# =========================================================

@app.post(
    "/agents/{agent_id}/briefs",
    status_code=201,
    operation_id="createAgentBrief",
)
async def api_create_agent_brief(
    agent_id: str,
    request: AgentBriefCreateRequest,
    idempotency_key: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    agent = _require_registered_agent(agent_id)

    resolved_idempotency_key = (
        idempotency_key or request.external_reference
    )

    brief = await run_agent_brief_call(
        create_brief,
        agent_id=agent_id,
        source_agent_id=request.source_agent_id,
        title=request.title,
        summary=request.summary,
        brief_type=request.brief_type,
        priority=request.priority,
        reporting_period_start=request.reporting_period_start,
        reporting_period_end=request.reporting_period_end,
        sections=request.sections,
        practical_move=request.practical_move,
        tags=request.tags,
        requires_human_approval=request.requires_human_approval,
        metadata=request.metadata,
        idempotency_key=resolved_idempotency_key,
    )

    return {
        "success": True,
        "brief_id": brief["id"],
        "agent_id": agent_id,
        "status": brief["status"],
        "delivery_status": brief["delivery_status"],
        "created_at": brief["created_at"],
        "message": "Brief accepted for {}.".format(agent["name"]),
    }


@app.get(
    "/agents/{agent_id}/briefs",
    operation_id="listAgentBriefs",
)
async def api_list_agent_briefs(
    agent_id: str,
    status: Optional[str] = Query(default=None),
    brief_type: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    _require_registered_agent(agent_id)

    return await run_agent_brief_call(
        list_briefs,
        agent_id=agent_id,
        status=status,
        brief_type=brief_type,
        priority=priority,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/agents/{agent_id}/briefs/{brief_id}",
    operation_id="getAgentBrief",
)
async def api_get_agent_brief(
    agent_id: str,
    brief_id: int,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    _require_registered_agent(agent_id)

    return await run_agent_brief_call(
        get_brief,
        agent_id=agent_id,
        brief_id=brief_id,
    )


@app.patch(
    "/agents/{agent_id}/briefs/{brief_id}",
    operation_id="updateAgentBrief",
)
async def api_update_agent_brief(
    agent_id: str,
    brief_id: int,
    request: AgentBriefUpdateRequest,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    _require_registered_agent(agent_id)

    updates = request.model_dump(exclude_unset=True)

    return await run_agent_brief_call(
        update_brief_fields,
        agent_id=agent_id,
        brief_id=brief_id,
        **updates,
    )


@app.post(
    "/agents/{agent_id}/briefs/{brief_id}/acknowledge",
    operation_id="acknowledgeAgentBrief",
)
async def api_acknowledge_agent_brief(
    agent_id: str,
    brief_id: int,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    _require_registered_agent(agent_id)

    return await run_agent_brief_call(
        acknowledge_brief,
        agent_id=agent_id,
        brief_id=brief_id,
    )


@app.post(
    "/agents/{agent_id}/briefs/{brief_id}/complete",
    operation_id="completeAgentBrief",
)
async def api_complete_agent_brief(
    agent_id: str,
    brief_id: int,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    _require_registered_agent(agent_id)

    return await run_agent_brief_call(
        complete_brief,
        agent_id=agent_id,
        brief_id=brief_id,
    )


@app.post(
    "/agents/{agent_id}/briefs/{brief_id}/retry-delivery",
    operation_id="retryAgentBriefDelivery",
)
async def api_retry_agent_brief_delivery(
    agent_id: str,
    brief_id: int,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    _require_registered_agent(agent_id)

    return await run_agent_brief_call(
        retry_delivery,
        agent_id=agent_id,
        brief_id=brief_id,
    )


# =========================================================
# MASTER EXECUTIVE SUMMARY
# =========================================================

@app.get("/executive-summary")
async def executive_summary(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    generated_at = datetime.now(
        timezone.utc
    ).isoformat()

    # ---------------------------------------------
    # Task data
    # ---------------------------------------------

    priority_tasks = run_task_call(
        get_priority_tasks,
        10,
    )

    today_tasks = run_task_call(
        get_today_tasks
    )

    overdue_tasks = run_task_call(
        get_overdue_tasks
    )

    # ---------------------------------------------
    # Run external systems concurrently
    # ---------------------------------------------

    results = await asyncio.gather(

        safe_call(
            "wadadlitech",
            get_account_summary,
            "wadadlitech",
        ),

        safe_call(
            "paradigm",
            get_account_summary,
            "paradigm",
        ),

        safe_call(
            "jermaingordon",
            get_account_summary,
            "jermaingordon",
        ),

        safe_call(
            "tradehub",
            get_tradehub_executive_summary,
        ),
    )

    result_map = {
        item["source"]: item
        for item in results
    }

    # ---------------------------------------------
    # Build CRM account data
    # ---------------------------------------------

    crm_accounts = {}

    for account_key in [
        "wadadlitech",
        "paradigm",
        "jermaingordon",
    ]:
        result = result_map[
            account_key
        ]

        if result["available"]:
            crm_accounts[
                account_key
            ] = {
                "available": True,
                # Additive: HEALTHY/DEGRADED: a failed resource (e.g.
                # contacts) no longer flips the whole account to unavailable.
                "status": result["data"].get(
                    "status", "HEALTHY"
                ),
                "data": result[
                    "data"
                ],
            }

        else:
            crm_accounts[
                account_key
            ] = {
                "available": False,
                "status": "UNAVAILABLE",
                "error": result[
                    "error"
                ],
            }

    # ---------------------------------------------
    # Trading (TradeHub)
    # ---------------------------------------------

    tradehub_result = result_map[
        "tradehub"
    ]

    # get_tradehub_executive_summary() never raises - it reports its own
    # available/status - so unwrap that inner state rather than relying on
    # safe_call()'s outer "available" (which only reflects whether the
    # coroutine itself raised).
    if tradehub_result["available"]:
        tradehub_data = tradehub_result[
            "data"
        ]

        trading = {
            "available": tradehub_data.get(
                "available", False
            ),
            "status": tradehub_data.get(
                "status", "UNAVAILABLE"
            ),
            "data": tradehub_data,
        }
    else:
        trading = {
            "available": False,
            "status": "UNAVAILABLE",
            "error": tradehub_result[
                "error"
            ],
        }

    # ---------------------------------------------
    # Alerts
    # ---------------------------------------------

    alerts = []

    for account_key in [
        "wadadlitech",
        "paradigm",
        "jermaingordon",
    ]:
        account = crm_accounts[
            account_key
        ]

        if not account[
            "available"
        ]:
            alerts.append(
                {
                    "severity": "P2",
                    "area": "CRM",
                    "account": account_key,
                    "message": (
                        "HighLevel data is "
                        "unavailable for {}."
                        .format(
                            account_key
                        )
                    ),
                }
            )

        elif account.get("status") == "DEGRADED":
            failed_resources = list(
                (
                    account["data"].get("errors")
                    or {}
                ).keys()
            )
            alerts.append(
                {
                    "severity": "P3",
                    "area": "CRM",
                    "account": account_key,
                    "message": (
                        "HighLevel account '{}' is degraded: "
                        "{} resource(s) failed ({})."
                        .format(
                            account_key,
                            len(failed_resources),
                            ", ".join(failed_resources),
                        )
                    ),
                }
            )

    # get_tradehub_executive_summary() already produces a descriptive alert
    # (e.g. "TradeHub authentication failed", "auto-trader not running")
    # whenever it is unavailable or degraded - reuse those rather than
    # inventing a second, less specific message.
    tradehub_alerts = (
        trading.get("data") or {}
    ).get("alerts") or []

    if tradehub_alerts:
        alerts.extend(tradehub_alerts)
    elif not trading["available"]:
        alerts.append(
            {
                "severity": "P2",
                "area": "TradeHub",
                "message": (
                    trading.get("error")
                    or "TradeHub data is unavailable."
                ),
            }
        )

    if overdue_tasks["count"] > 0:
        alerts.append(
            {
                "severity": "P2",
                "area": "Tasks",
                "message": (
                    "{} WadadliTech task(s) "
                    "are overdue."
                    .format(
                        overdue_tasks[
                            "count"
                        ]
                    )
                ),
            }
        )

    # ---------------------------------------------
    # Agent operations (Maya briefs)
    # ---------------------------------------------

    maya_stats = get_brief_stats(
        "MAYA-WD-MKT-001"
    )

    maya_webhook_env_var = get_webhook_env_var(
        "MAYA-WD-MKT-001"
    )

    maya_webhook_configured = bool(
        os.getenv(maya_webhook_env_var)
    ) if maya_webhook_env_var else False

    agent_operations = {
        "maya": {
            "agent_id": "MAYA-WD-MKT-001",
            "pending_briefs": maya_stats[
                "pending_briefs"
            ],
            "delivery_failures": maya_stats[
                "delivery_failures"
            ],
            "acknowledged_briefs": maya_stats[
                "acknowledged_briefs"
            ],
            "completed_briefs": maya_stats[
                "completed_briefs"
            ],
            "latest_brief": maya_stats[
                "latest_brief"
            ],
            "webhook_configured": (
                maya_webhook_configured
            ),
        },
    }

    if maya_stats["delivery_failures"] > 0:
        alerts.append(
            {
                "severity": "P2",
                "area": "Agent Operations",
                "message": (
                    "{} brief(s) failed "
                    "delivery to Maya."
                    .format(
                        maya_stats[
                            "delivery_failures"
                        ]
                    )
                ),
            }
        )

    # ---------------------------------------------
    # Current executive priorities
    # ---------------------------------------------

    priorities = [
        {
            "rank": 1,
            "priority": (
                "Operate and refine "
                "the WadadliTech "
                "Chief of Staff."
            ),
            "status": "ACTIVE",
        },

        {
            "rank": 2,
            "priority": (
                "Complete active "
                "Paradigm Homecare "
                "client deliverables."
            ),
            "status": "ACTIVE",
        },

        {
            "rank": 3,
            "priority": (
                "Prepare the CRM "
                "environment for "
                "additional clients."
            ),
            "status": "ACTIVE",
        },

        {
            "rank": 4,
            "priority": (
                "Resolve hosting "
                "reliability and "
                "replacement strategy."
            ),
            "status": "ACTIVE",
        },

        {
            "rank": 5,
            "priority": (
                "Continue active "
                "CowrieLedger "
                "development."
            ),
            "status": "ACTIVE",
        },
    ]

    # ---------------------------------------------
    # Final payload
    # ---------------------------------------------

    return {
        "company": (
            "Wadadli Technology "
            "Consulting LLC"
        ),

        "generated_at": generated_at,

        "executive_priorities": (
            priorities
        ),

        "alerts": alerts,

        "tasks": {
            "priority": (
                priority_tasks
            ),
            "today": (
                today_tasks
            ),
            "overdue": (
                overdue_tasks
            ),
        },

        "crm": {
            "internal": {
                "wadadlitech": (
                    crm_accounts[
                        "wadadlitech"
                    ]
                )
            },

            "clients": {
                "paradigm": (
                    crm_accounts[
                        "paradigm"
                    ]
                ),

                "jermaingordon": (
                    crm_accounts[
                        "jermaingordon"
                    ]
                ),
            },
        },

        "engineering": {
            "available": False,
            "status": "NOT_PROVIDED_BY_COS_API",
            "source_of_truth": "direct_github_connector",
        },

        "trading": (
            trading
        ),

        "agent_operations": (
            agent_operations
        ),

        "source_health": {
            "wadadlitech": (
                result_map[
                    "wadadlitech"
                ][
                    "available"
                ]
            ),

            "paradigm": (
                result_map[
                    "paradigm"
                ][
                    "available"
                ]
            ),

            "jermaingordon": (
                result_map[
                    "jermaingordon"
                ][
                    "available"
                ]
            ),

            "tradehub": (
                trading["available"]
            ),

            "tasks": True,
        },

        # Additive detail alongside the boolean source_health above:
        # distinguishes a fully healthy account from one that is
        # available but DEGRADED (a partial resource failure).
        "source_health_status": {
            "wadadlitech": crm_accounts[
                "wadadlitech"
            ]["status"],

            "paradigm": crm_accounts[
                "paradigm"
            ]["status"],

            "jermaingordon": crm_accounts[
                "jermaingordon"
            ]["status"],

            "tradehub": trading["status"],
        },
    }# =========================================================
# GMAIL OPERATIONS
# =========================================================

@app.get("/integrations/gmail/messages", operation_id="searchGmailMessages")
async def search_gmail(
    query: Optional[str] = Query(default=None),
    max_results: int = Query(default=10, ge=1, le=100),
    page_token: Optional[str] = Query(default=None),
    authenticated: bool = Security(verify_api_key),
):
    """Search Gmail messages with optional query.
    
    Query examples:
    - "is:request" - messages with request keyword
    - "from:user@example.com" - from specific sender
    - "subject:proposal" - in subject line
    """
    try:
        return await search_messages(query, max_results, page_token)
    except GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/integrations/gmail/messages/{message_id}", operation_id="getGmailMessage")
async def get_gmail_msg(
    message_id: str,
    authenticated: bool = Security(verify_api_key),
):
    """Fetch a complete Gmail message."""
    try:
        return await get_message(message_id)
    except GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/integrations/gmail/threads/{thread_id}", operation_id="getGmailThread")
async def get_gmail_thd(
    thread_id: str,
    authenticated: bool = Security(verify_api_key),
):
    """Fetch a complete Gmail thread."""
    try:
        return await get_thread(thread_id)
    except GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/integrations/gmail/labels", operation_id="listGmailLabels")
async def list_gmail_labels(
    authenticated: bool = Security(verify_api_key),
):
    """List all Gmail labels."""
    try:
        return await list_labels()
    except GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/integrations/gmail/labels", operation_id="createGmailLabel")
async def create_gmail_label(
    name: str = Query(...),
    authenticated: bool = Security(verify_api_key),
):
    """Create a new Gmail label."""
    try:
        return await create_label(name)
    except GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/integrations/gmail/messages/{message_id}/label", operation_id="applyGmailLabel")
async def apply_gmail_label(
    message_id: str,
    label_id: str = Query(...),
    authenticated: bool = Security(verify_api_key),
):
    """Apply a label to a message."""
    try:
        return await apply_label(message_id, label_id)
    except GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.delete("/integrations/gmail/messages/{message_id}/label", operation_id="removeGmailLabel")
async def remove_gmail_label(
    message_id: str,
    label_id: str = Query(...),
    authenticated: bool = Security(verify_api_key),
):
    """Remove a label from a message."""
    try:
        return await remove_label(message_id, label_id)
    except GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/integrations/gmail/messages/{message_id}/archive", operation_id="archiveGmailMessage")
async def archive_gmail_msg(
    message_id: str,
    authenticated: bool = Security(verify_api_key),
):
    """Archive a message (remove from Inbox)."""
    try:
        return await archive_message(message_id)
    except GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/integrations/gmail/drafts", operation_id="createGmailDraft")
async def create_gmail_draft(
    to: str = Query(...),
    subject: str = Query(...),
    body: str = Query(...),
    thread_id: Optional[str] = Query(default=None),
    authenticated: bool = Security(verify_api_key),
):
    """Create a draft message (optional reply to thread)."""
    try:
        return await create_draft(to, subject, body, thread_id=thread_id)
    except GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.put("/integrations/gmail/drafts/{draft_id}", operation_id="updateGmailDraft")
async def update_gmail_draft(
    draft_id: str,
    to: str = Query(...),
    subject: str = Query(...),
    body: str = Query(...),
    authenticated: bool = Security(verify_api_key),
):
    """Update an existing draft."""
    try:
        return await update_draft(draft_id, to, subject, body)
    except GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/integrations/gmail/drafts/{draft_id}/send", operation_id="sendGmailDraft")
async def send_gmail_draft(
    draft_id: str,
    approval_confirmed: bool = Query(default=False),
    authenticated: bool = Security(verify_api_key),
):
    """Send a draft message. Human approval required before calling."""
    try:
        return await send_draft(
            draft_id,
            approval_confirmed=approval_confirmed,
        )
    except GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/integrations/gmail/messages/{message_id}/context", operation_id="getGmailContext")
async def get_gmail_msg_context(
    message_id: str,
    authenticated: bool = Security(verify_api_key),
):
    """Extract context from email for task creation (sanitized)."""
    try:
        return await get_email_context(message_id)
    except GmailError as e:
        raise HTTPException(status_code=502, detail=str(e))


# =========================================================
# GOOGLE CALENDAR OPERATIONS
# =========================================================

@app.get("/integrations/calendar/calendars", operation_id="listCalendars")
async def list_cals(
    authenticated: bool = Security(verify_api_key),
):
    """List accessible calendars."""
    try:
        return await list_calendars()
    except CalendarError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/integrations/calendar/calendars/{calendar_id}/events", operation_id="getCalendarEvents")
async def get_calendar_evs(
    calendar_id: str = "primary",
    time_min: Optional[str] = Query(default=None),
    time_max: Optional[str] = Query(default=None),
    max_results: int = Query(default=50, ge=1, le=250),
    page_token: Optional[str] = Query(default=None),
    authenticated: bool = Security(verify_api_key),
):
    """Fetch calendar events (paginated)."""
    try:
        return await get_events(
            calendar_id, time_min, time_max, max_results, page_token
        )
    except CalendarError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/integrations/calendar/availability", operation_id="checkCalendarAvailability")
async def check_cal_availability(
    calendar_id: str = Query(default="primary"),
    start_time: str = Query(...),
    end_time: str = Query(...),
    timezone_name: str = Query(default="America/New_York"),
    authenticated: bool = Security(verify_api_key),
):
    """Check if a time slot is available (no busy events)."""
    try:
        return await check_availability(
            calendar_id, start_time, end_time, timezone_name
        )
    except CalendarError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/integrations/calendar/focus-blocks", operation_id="createFocusBlock")
async def create_focus_blk(
    calendar_id: str = Query(default="primary"),
    start_time: str = Query(...),
    end_time: str = Query(...),
    title: Optional[str] = Query(default=None),
    description: Optional[str] = Query(default=None),
    timezone_name: str = Query(default="America/New_York"),
    idempotency_key: Optional[str] = Query(default=None),
    approval_confirmed: bool = Query(default=False),
    authenticated: bool = Security(verify_api_key),
):
    """Create a COS-managed focus block on calendar."""
    try:
        return await create_focus_block(
            calendar_id, start_time, end_time, title, description,
            timezone_name, idempotency_key, approval_confirmed
        )
    except CalendarError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.patch("/integrations/calendar/focus-blocks/{event_id}", operation_id="updateFocusBlock")
async def update_focus_blk(
    event_id: str,
    calendar_id: str = Query(default="primary"),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    title: Optional[str] = Query(default=None),
    description: Optional[str] = Query(default=None),
    timezone_name: str = Query(default="America/New_York"),
    authenticated: bool = Security(verify_api_key),
):
    """Update an existing focus block."""
    try:
        return await update_focus_block(
            calendar_id, event_id, start_time, end_time, title,
            description, timezone_name
        )
    except CalendarError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.delete("/integrations/calendar/focus-blocks/{event_id}", operation_id="cancelFocusBlock")
async def cancel_focus_blk(
    event_id: str,
    calendar_id: str = Query(default="primary"),
    authenticated: bool = Security(verify_api_key),
):
    """Cancel a focus block."""
    try:
        return await cancel_focus_block(calendar_id, event_id)
    except CalendarError as e:
        raise HTTPException(status_code=502, detail=str(e))


# =========================================================
# APPLE REMINDERS SYNCHRONIZATION
# =========================================================

@app.get("/integrations/reminders/pending-sync", operation_id="getRemindersPendingSync")
async def get_reminders_pending(
    authorization: str = Header(...),
    last_sync_timestamp: Optional[str] = Query(default=None),
):
    """Get tasks pending sync to Reminders (iPhone Shortcut endpoint).
    
    Authorization header: "Bearer {REMINDERS_BRIDGE_API_KEY}"
    """
    try:
        # Extract key from "Bearer {key}"
        parts = authorization.split(" ")
        if len(parts) != 2 or parts[0] != "Bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header")
        
        bridge_key = parts[1]
        
        result = await get_pending_sync(bridge_key, last_sync_timestamp)
        return result
    
    except RemindersError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/integrations/reminders/acknowledge", operation_id="acknowledgeReminderSync")
async def acknowledge_reminders(
    authorization: str = Header(...),
    request_body: Dict[str, List[Dict[str, Any]]] = None,
    approval_confirmed: bool = Query(default=False),
    authenticated: bool = Security(verify_api_key),
):
    """Accept Reminders sync acknowledgments (iPhone Shortcut endpoint).

    Authorization header: "Bearer {REMINDERS_BRIDGE_API_KEY}"
    Body: {"acknowledgments": [...]} 
    """
    try:
        parts = authorization.split(" ")
        if len(parts) != 2 or parts[0] != "Bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization header")

        bridge_key = parts[1]
        acknowledgments = request_body.get("acknowledgments", []) if request_body else []

        result = await acknowledge_sync(
            bridge_key,
            acknowledgments,
            approval_confirmed=approval_confirmed,
        )
        return result
    except RemindersError as e:
        raise HTTPException(status_code=401, detail=str(e))

# =========================================================
# PLANNING & REVIEW
# =========================================================

@app.get("/planning/daily", operation_id="getDailyPlan")
async def daily_plan(
    max_outcomes: int = Query(default=3, ge=1, le=10),
    buffer_percentage: int = Query(default=30, ge=10, le=80),
    timezone_name: str = Query(default="America/New_York"),
    authenticated: bool = Security(verify_api_key),
):
    """Get daily plan for today.
    
    Combines tasks, calendar, and emails to suggest 3 main outcomes.
    Accounts for buffer time and identifies blocked tasks, conflicts, warnings.
    """
    try:
        # TODO: Fetch actual tasks, calendar events from services
        # For now, return planning structure with empty data
        
        plan = await get_daily_plan(
            max_outcomes=max_outcomes,
            buffer_percentage=buffer_percentage,
            timezone_name=timezone_name,
            tasks=[],
            calendar_events=[],
        )
        
        return plan
    
    except PlanningError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/planning/weekly-review", operation_id="getWeeklyReview")
async def weekly_review(
    timezone_name: str = Query(default="America/New_York"),
    authenticated: bool = Security(verify_api_key),
):
    """Get weekly review report.
    
    Reports planned vs completed, overdue, waiting-for, active projects,
    upcoming deadlines, and evidence-based adjustments.
    """
    try:
        # TODO: Fetch actual tasks from service
        # For now, return review structure with empty data
        
        review = await get_weekly_review(
            tasks=[],
            completed_count=0,
            timezone_name=timezone_name,
        )
        
        return review
    
    except PlanningError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =========================================================
# ENHANCED GOOGLE INTEGRATION DIAGNOSTICS
# =========================================================

@app.get("/integrations/google/diagnostics", operation_id="getGoogleDiagnostics")
async def google_diagnostics(
    authenticated: bool = Security(verify_api_key),
):
    """Comprehensive Google integration diagnostics.
    
    Safe (non-secret) status of Gmail, Calendar, Drive configuration,
    read/write capabilities, and reauthorization requirements.
    """
    try:
        return await get_google_integration_diagnostics()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
