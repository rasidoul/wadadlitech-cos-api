import asyncio
from datetime import datetime, timezone

import os
from typing import Optional
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
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.highlevel import (
    HighLevelError,
    get_location,
    get_pipelines,
    get_workflows,
    get_tags,
    get_conversations,
    search_contacts,
    search_opportunities,
    get_agent_studio_agents,
)

load_dotenv()

COS_API_KEY = os.getenv("COS_API_KEY")

app = FastAPI(
    title="WadadliTech Chief of Staff API",
    description=(
        "Secure middleware between WD-AI-001 "
        "and WadadliTech business systems."
    ),
    version="1.2.0",
)

security = HTTPBearer(
    scheme_name="COS API Key",
    description="Enter the COS API key. Swagger will send it as a Bearer token.",
)


def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    if not COS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="COS_API_KEY is not configured on the server.",
        )

    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authorization credentials are required.",
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


async def run_highlevel_call(callable_obj, *args, **kwargs):
    try:
        return await callable_obj(*args, **kwargs)

    except HighLevelError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )

async def safe_call(source_name, callable_obj, *args, **kwargs):
    """
    Run an external service call without allowing one failed integration
    to crash the entire executive summary.
    """

    try:
        result = await callable_obj(*args, **kwargs)

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
    
async def run_github_call(callable_obj, *args, **kwargs):

    try:
        return await callable_obj(*args, **kwargs)

    except GitHubError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )

@app.get("/")
async def root():
    return {
        "service": "WadadliTech Chief of Staff API",
        "status": "online",
        "version": "1.2.0",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "wadadlitech-cos-api",
        "version": "1.2.0",
    }


@app.get("/highlevel/status")
async def highlevel_status(
    authenticated: bool = Security(verify_api_key),
):
    result = await run_highlevel_call(get_location)

    location = result.get("location", result)

    return {
        "connected": True,
        "location_id": location.get("id"),
        "location_name": location.get("name"),
        "timezone": location.get("timezone"),
    }


@app.get("/highlevel/pipelines")
async def highlevel_pipelines(
    authenticated: bool = Security(verify_api_key),
):
    return await run_highlevel_call(get_pipelines)


@app.get("/highlevel/workflows")
async def highlevel_workflows(
    authenticated: bool = Security(verify_api_key),
):
    return await run_highlevel_call(get_workflows)


@app.get("/highlevel/tags")
async def highlevel_tags(
    authenticated: bool = Security(verify_api_key),
):
    return await run_highlevel_call(get_tags)


@app.get("/highlevel/conversations")
async def highlevel_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    authenticated: bool = Security(verify_api_key),
):
    return await run_highlevel_call(
        get_conversations,
        limit,
    )


@app.get("/highlevel/contacts")
async def highlevel_contacts(
    limit: int = Query(default=20, ge=1, le=100),
    query: Optional[str] = Query(default=None),
    authenticated: bool = Security(verify_api_key),
):
    return await run_highlevel_call(
        search_contacts,
        limit,
        query,
    )


@app.get("/highlevel/opportunities")
async def highlevel_opportunities(
    limit: int = Query(default=20, ge=1, le=100),
    query: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    authenticated: bool = Security(verify_api_key),
):
    return await run_highlevel_call(
        search_opportunities,
        limit,
        query,
        status,
    )


@app.get("/highlevel/agents")
async def highlevel_agents(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    published_only: bool = Query(default=False),
    authenticated: bool = Security(verify_api_key),
):
    return await run_highlevel_call(
        get_agent_studio_agents,
        limit,
        offset,
        published_only,
    )

@app.get("/github/projects")
async def github_projects(
    authenticated: bool = Security(verify_api_key),
):
    return get_registered_projects()


@app.get("/github/activity")
async def github_activity(
    authenticated: bool = Security(verify_api_key),
):
    return await run_github_call(
        get_active_project_activity
    )


@app.get("/github/projects/{project_key}")
async def github_project(
    project_key: str,
    authenticated: bool = Security(verify_api_key),
):
    return await run_github_call(
        get_project_summary,
        project_key,
    )


@app.get("/github/projects/{project_key}/repository")
async def github_repository(
    project_key: str,
    authenticated: bool = Security(verify_api_key),
):
    return await run_github_call(
        get_repository,
        project_key,
    )


@app.get("/github/projects/{project_key}/commits")
async def github_commits(
    project_key: str,
    limit: int = Query(default=10, ge=1, le=100),
    authenticated: bool = Security(verify_api_key),
):
    return await run_github_call(
        get_commits,
        project_key,
        limit,
    )


@app.get("/github/projects/{project_key}/issues")
async def github_issues(
    project_key: str,
    limit: int = Query(default=20, ge=1, le=100),
    authenticated: bool = Security(verify_api_key),
):
    return await run_github_call(
        get_issues,
        project_key,
        limit,
    )


@app.get("/github/projects/{project_key}/pulls")
async def github_pull_requests(
    project_key: str,
    limit: int = Query(default=20, ge=1, le=100),
    authenticated: bool = Security(verify_api_key),
):
    return await run_github_call(
        get_pull_requests,
        project_key,
        limit,
    )

@app.get("/executive-summary")
async def executive_summary(
    authenticated: bool = Security(verify_api_key),
):

    generated_at = datetime.now(timezone.utc).isoformat()

    # Run independent system queries concurrently.
    results = await asyncio.gather(
        safe_call(
            "highlevel_location",
            get_location,
        ),
        safe_call(
            "highlevel_pipelines",
            get_pipelines,
        ),
        safe_call(
            "highlevel_workflows",
            get_workflows,
        ),
        safe_call(
            "highlevel_contacts",
            search_contacts,
            20,
            None,
        ),
        safe_call(
            "highlevel_opportunities",
            search_opportunities,
            100,
            None,
            None,
        ),
        safe_call(
            "highlevel_agents",
            get_agent_studio_agents,
            100,
            0,
            False,
        ),
        safe_call(
            "github_activity",
            get_active_project_activity,
        ),
    )

    result_map = {
        item["source"]: item
        for item in results
    }

    #
    # HIGHLEVEL
    #

    location_result = result_map["highlevel_location"]
    pipelines_result = result_map["highlevel_pipelines"]
    workflows_result = result_map["highlevel_workflows"]
    contacts_result = result_map["highlevel_contacts"]
    opportunities_result = result_map["highlevel_opportunities"]
    agents_result = result_map["highlevel_agents"]

    location_data = {}

    if location_result["available"]:
        raw_location = location_result["data"]
        location_data = raw_location.get(
            "location",
            raw_location,
        )

    pipeline_list = []

    if pipelines_result["available"]:
        pipeline_list = pipelines_result["data"].get(
            "pipelines",
            [],
        )

    workflow_list = []

    if workflows_result["available"]:
        workflow_list = workflows_result["data"].get(
            "workflows",
            [],
        )

    contact_list = []
    contact_total = None

    if contacts_result["available"]:
        raw_contacts = contacts_result["data"]

        contact_list = raw_contacts.get(
            "contacts",
            [],
        )

        contact_total = (
            raw_contacts.get("total")
            or raw_contacts.get("count")
            or len(contact_list)
        )

    opportunity_list = []

    if opportunities_result["available"]:
        opportunity_list = opportunities_result[
            "data"
        ].get(
            "opportunities",
            [],
        )

    agent_list = []

    if agents_result["available"]:
        raw_agents = agents_result["data"]

        agent_list = (
            raw_agents.get("agents")
            or raw_agents.get("data")
            or []
        )

    #
    # OPPORTUNITY ANALYSIS
    #

    open_opportunities = []
    won_opportunities = []
    lost_opportunities = []

    for opportunity in opportunity_list:

        status = str(
            opportunity.get("status", "")
        ).lower()

        if status == "won":
            won_opportunities.append(opportunity)

        elif status in (
            "lost",
            "abandoned",
        ):
            lost_opportunities.append(opportunity)

        else:
            open_opportunities.append(opportunity)

    open_pipeline_value = sum(
        float(
            opportunity.get("monetaryValue")
            or 0
        )
        for opportunity in open_opportunities
    )

    #
    # GITHUB
    #

    github_result = result_map[
        "github_activity"
    ]

    engineering_projects = []

    if github_result["available"]:

        engineering_projects = (
            github_result["data"].get(
                "active_projects",
                [],
            )
        )

    engineering_summary = []

    engineering_alerts = []

    for project_entry in engineering_projects:

        project_name = project_entry.get(
            "project"
        )

        project_status = project_entry.get(
            "status"
        )

        if project_status != "AVAILABLE":

            engineering_alerts.append(
                {
                    "project": project_name,
                    "severity": "P2",
                    "message": (
                        "Engineering data unavailable "
                        "for {}".format(
                            project_name
                        )
                    ),
                }
            )

            continue

        data = project_entry.get(
            "data",
            {},
        )

        latest_commit = data.get(
            "latest_commit"
        )

        engineering_summary.append(
            {
                "project": project_name,
                "business_status": data.get(
                    "business_status"
                ),
                "deployment": data.get(
                    "deployment"
                ),
                "repository": data.get(
                    "repository"
                ),
                "default_branch": data.get(
                    "default_branch"
                ),
                "latest_commit": latest_commit,
                "open_issues": data.get(
                    "open_issues"
                ),
                "open_pull_requests": data.get(
                    "open_pull_requests"
                ),
                "pushed_at": data.get(
                    "pushed_at"
                ),
            }
        )

    #
    # BUSINESS ALERTS
    #

    alerts = []

    if not location_result["available"]:
        alerts.append(
            {
                "severity": "P1",
                "area": "HighLevel",
                "message": (
                    "HighLevel connection is unavailable."
                ),
            }
        )

    if opportunities_result["available"]:

        if len(open_opportunities) > 0:

            alerts.append(
                {
                    "severity": "P3",
                    "area": "Sales",
                    "message": (
                        "{} open CRM opportunities "
                        "require monitoring.".format(
                            len(
                                open_opportunities
                            )
                        )
                    ),
                }
            )

    else:

        alerts.append(
            {
                "severity": "P2",
                "area": "Sales",
                "message": (
                    "CRM opportunity data "
                    "could not be retrieved."
                ),
            }
        )

    for alert in engineering_alerts:
        alerts.append(
            {
                "severity": alert[
                    "severity"
                ],
                "area": "Engineering",
                "message": alert[
                    "message"
                ],
            }
        )

    #
    # CURRENT EXECUTIVE PRIORITIES
    #

    current_priorities = [
        {
            "rank": 1,
            "priority": (
                "Get the WadadliTech "
                "Chief of Staff operational."
            ),
            "status": "ACTIVE",
        },
        {
            "rank": 2,
            "priority": (
                "Complete active Paradigm "
                "Homecare client deliverables."
            ),
            "status": "ACTIVE",
        },
        {
            "rank": 3,
            "priority": (
                "Prepare the CRM environment "
                "for additional WadadliTech clients."
            ),
            "status": "ACTIVE",
        },
        {
            "rank": 4,
            "priority": (
                "Resolve hosting reliability "
                "and replacement strategy."
            ),
            "status": "ACTIVE",
        },
        {
            "rank": 5,
            "priority": (
                "Continue active "
                "CowrieLedger development."
            ),
            "status": "ACTIVE",
        },
    ]

    #
    # SOURCE HEALTH
    #

    source_health = {
        item["source"]: {
            "available": item[
                "available"
            ],
            "error": item[
                "error"
            ],
        }
        for item in results
    }

    #
    # FINAL EXECUTIVE PAYLOAD
    #

    return {
        "company": (
            "Wadadli Technology "
            "Consulting LLC"
        ),

        "generated_at": generated_at,

        "executive_priorities": (
            current_priorities
        ),

        "alerts": alerts,

        "crm": {

            "location": {
                "id": location_data.get(
                    "id"
                ),
                "name": location_data.get(
                    "name"
                ),
                "timezone": (
                    location_data.get(
                        "timezone"
                    )
                ),
            },

            "contacts": {
                "total": contact_total,
                "sample_returned": len(
                    contact_list
                ),
            },

            "pipelines": {
                "count": len(
                    pipeline_list
                ),
                "items": [
                    {
                        "id": pipeline.get(
                            "id"
                        ),
                        "name": pipeline.get(
                            "name"
                        ),
                    }
                    for pipeline
                    in pipeline_list
                ],
            },

            "opportunities": {
                "total_returned": len(
                    opportunity_list
                ),
                "open_count": len(
                    open_opportunities
                ),
                "won_count": len(
                    won_opportunities
                ),
                "lost_count": len(
                    lost_opportunities
                ),
                "open_pipeline_value": (
                    open_pipeline_value
                ),
                "open_items": [
                    {
                        "id": item.get(
                            "id"
                        ),
                        "name": item.get(
                            "name"
                        ),
                        "status": item.get(
                            "status"
                        ),
                        "monetary_value": (
                            item.get(
                                "monetaryValue"
                            )
                        ),
                        "pipeline_id": (
                            item.get(
                                "pipelineId"
                            )
                        ),
                        "stage_id": (
                            item.get(
                                "pipelineStageId"
                            )
                        ),
                    }
                    for item
                    in open_opportunities[
                        :20
                    ]
                ],
            },

            "workflows": {
                "count": len(
                    workflow_list
                ),
                "items": [
                    {
                        "id": workflow.get(
                            "id"
                        ),
                        "name": workflow.get(
                            "name"
                        ),
                        "status": workflow.get(
                            "status"
                        ),
                    }
                    for workflow
                    in workflow_list
                ],
            },

            "agents": {
                "count": len(
                    agent_list
                ),
                "items": [
                    {
                        "id": agent.get(
                            "id"
                        ),
                        "name": agent.get(
                            "name"
                        ),
                        "status": agent.get(
                            "status"
                        ),
                    }
                    for agent
                    in agent_list
                ],
            },
        },

        "engineering": {
            "active_project_count": len(
                engineering_summary
            ),
            "projects": (
                engineering_summary
            ),
        },

        "source_health": (
            source_health
        ),
    }
@app.get("/github/projects/{project_key}/branches")
async def github_branches(
    project_key: str,
    limit: int = Query(default=50, ge=1, le=100),
    authenticated: bool = Security(verify_api_key),
):
    return await run_github_call(
        get_branches,
        project_key,
        limit,
    )
@app.get("/highlevel/executive-summary")
async def highlevel_executive_summary(
    authenticated: bool = Security(verify_api_key),
):
    location = await run_highlevel_call(get_location)
    pipelines = await run_highlevel_call(get_pipelines)
    workflows = await run_highlevel_call(get_workflows)
    contacts = await run_highlevel_call(
        search_contacts,
        10,
        None,
    )
    opportunities = await run_highlevel_call(
        search_opportunities,
        50,
        None,
        None,
    )
    agents = await run_highlevel_call(
        get_agent_studio_agents,
        50,
        0,
        False,
    )

    location_data = location.get("location", location)

    contact_list = contacts.get("contacts", [])
    opportunity_list = opportunities.get("opportunities", [])
    pipeline_list = pipelines.get("pipelines", [])
    workflow_list = workflows.get("workflows", [])

    agent_list = (
        agents.get("agents")
        or agents.get("data")
        or []
    )

    open_opportunities = [
        opp
        for opp in opportunity_list
        if str(opp.get("status", "")).lower()
        not in ("won", "lost", "abandoned")
    ]

    total_pipeline_value = sum(
        float(opp.get("monetaryValue") or 0)
        for opp in open_opportunities
    )

    return {
        "location": {
            "id": location_data.get("id"),
            "name": location_data.get("name"),
            "timezone": location_data.get("timezone"),
        },
        "contacts": {
            "returned": len(contact_list),
            "reported_total": (
                contacts.get("total")
                or contacts.get("count")
            ),
        },
        "pipelines": {
            "count": len(pipeline_list),
            "items": [
                {
                    "id": pipeline.get("id"),
                    "name": pipeline.get("name"),
                }
                for pipeline in pipeline_list
            ],
        },
        "opportunities": {
            "returned": len(opportunity_list),
            "open_count": len(open_opportunities),
            "open_value": total_pipeline_value,
        },
        "workflows": {
            "count": len(workflow_list),
            "items": [
                {
                    "id": workflow.get("id"),
                    "name": workflow.get("name"),
                    "status": workflow.get("status"),
                }
                for workflow in workflow_list
            ],
        },
        "agents": {
            "count": len(agent_list),
            "items": [
                {
                    "id": agent.get("id"),
                    "name": agent.get("name"),
                    "status": agent.get("status"),
                }
                for agent in agent_list
            ],
        },
    }