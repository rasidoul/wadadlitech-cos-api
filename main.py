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

from services.github import (
    GitHubError,
    get_registered_projects,
    get_repository,
    get_commits,
    get_issues,
    get_pull_requests,
    get_branches,
    get_project_summary,
    get_active_project_activity,
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


async def run_github_call(
    callable_obj,
    *args,
    **kwargs
):
    try:
        return await callable_obj(
            *args,
            **kwargs
        )

    except GitHubError as exc:
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
    operation_id="searchGmailMessages",
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
    operation_id="getGmailMessage",
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
    operation_id="listCalendarEvents",
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
# GITHUB
# =========================================================

@app.get("/github/projects")
async def github_projects(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return get_registered_projects()


@app.get("/github/activity")
async def github_activity(
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_github_call(
        get_active_project_activity
    )


@app.get("/github/projects/{project_key}")
async def github_project(
    project_key: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_github_call(
        get_project_summary,
        project_key,
    )


@app.get(
    "/github/projects/{project_key}/repository"
)
async def github_repository(
    project_key: str,
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_github_call(
        get_repository,
        project_key,
    )


@app.get(
    "/github/projects/{project_key}/commits"
)
async def github_commits(
    project_key: str,
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_github_call(
        get_commits,
        project_key,
        limit,
    )


@app.get(
    "/github/projects/{project_key}/issues"
)
async def github_issues(
    project_key: str,
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_github_call(
        get_issues,
        project_key,
        limit,
    )


@app.get(
    "/github/projects/{project_key}/pulls"
)
async def github_pull_requests(
    project_key: str,
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_github_call(
        get_pull_requests,
        project_key,
        limit,
    )


@app.get(
    "/github/projects/{project_key}/branches"
)
async def github_branches(
    project_key: str,
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    authenticated: bool = Security(
        verify_api_key
    ),
):
    return await run_github_call(
        get_branches,
        project_key,
        limit,
    )


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
            "github",
            get_active_project_activity,
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
                "data": result[
                    "data"
                ],
            }

        else:
            crm_accounts[
                account_key
            ] = {
                "available": False,
                "error": result[
                    "error"
                ],
            }

    # ---------------------------------------------
    # Engineering
    # ---------------------------------------------

    github_result = result_map[
        "github"
    ]

    engineering = {
        "available": github_result[
            "available"
        ]
    }

    if github_result["available"]:
        engineering["data"] = (
            github_result["data"]
        )
    else:
        engineering["error"] = (
            github_result["error"]
        )

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

    if not engineering[
        "available"
    ]:
        alerts.append(
            {
                "severity": "P2",
                "area": "Engineering",
                "message": (
                    "GitHub engineering "
                    "data is unavailable."
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

        "engineering": (
            engineering
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

            "github": (
                result_map[
                    "github"
                ][
                    "available"
                ]
            ),

            "tasks": True,
        },
    }