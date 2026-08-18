import os
import asyncio

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel
from dotenv import load_dotenv

from fastapi import (
    FastAPI,
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


load_dotenv()

COS_API_KEY = os.getenv("COS_API_KEY")


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="WadadliTech Chief of Staff API",
    description=(
        "Secure middleware between WD-AI-001 "
        "and WadadliTech business systems."
    ),
    version="2.1.0",
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
        "version": "2.1.0",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "wadadlitech-cos-api",
        "version": "2.1.0",
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