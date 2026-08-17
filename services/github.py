import os
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "rasidoul")

BASE_URL = "https://api.github.com"

# Business-facing project names mapped to actual GitHub repositories.
PROJECTS = {
    "cowrieledger": {
        "name": "CowrieLedger",
        "repo": "managemeright",
        "business_status": "ACTIVE",
        "deployment": "PRODUCTION",
        "include_in_daily_brief": True,
    },
    "tradehub": {
        "name": "TradeHub",
        "repo": "tradehub",
        "business_status": "ACTIVE",
        "deployment": "LOCAL",
        "include_in_daily_brief": True,
    },
    "semi": {
        "name": "SEMI",
        "repo": "SEMI",
        "business_status": "PAUSED",
        "deployment": "DEVELOPMENT",
        "include_in_daily_brief": False,
    },
}


class GitHubError(Exception):
    pass


def _require_config():
    if not GITHUB_TOKEN:
        raise GitHubError("GITHUB_TOKEN is not configured.")


def _headers() -> Dict[str, str]:
    _require_config()

    return {
        "Authorization": "Bearer {}".format(GITHUB_TOKEN),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "WadadliTech-Chief-of-Staff",
    }


async def _get(
    path: str,
    params: Optional[Dict[str, Any]] = None,
) -> Any:

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            "{}{}".format(BASE_URL, path),
            headers=_headers(),
            params=params,
        )

    if response.status_code >= 400:
        raise GitHubError(
            "GitHub returned {}: {}".format(
                response.status_code,
                response.text,
            )
        )

    return response.json()


def _get_project(project_key: str) -> Dict[str, Any]:

    normalized = project_key.lower()

    project = PROJECTS.get(normalized)

    if not project:
        raise GitHubError(
            "Unknown project '{}'. Valid projects are: {}".format(
                project_key,
                ", ".join(PROJECTS.keys()),
            )
        )

    return project


async def get_repository(project_key: str) -> Dict[str, Any]:

    project = _get_project(project_key)

    repo = project["repo"]

    result = await _get(
        "/repos/{}/{}".format(GITHUB_OWNER, repo)
    )

    return {
        "project": project["name"],
        "project_key": project_key.lower(),
        "repository": result.get("full_name"),
        "private": result.get("private"),
        "description": result.get("description"),
        "default_branch": result.get("default_branch"),
        "updated_at": result.get("updated_at"),
        "pushed_at": result.get("pushed_at"),
        "open_issues_count": result.get("open_issues_count"),
        "archived": result.get("archived"),
        "business_status": project["business_status"],
        "deployment": project["deployment"],
    }


async def get_commits(
    project_key: str,
    limit: int = 10,
) -> Dict[str, Any]:

    project = _get_project(project_key)

    repo = project["repo"]

    commits = await _get(
        "/repos/{}/{}/commits".format(
            GITHUB_OWNER,
            repo,
        ),
        params={
            "per_page": limit,
        },
    )

    results = []

    for item in commits:
        commit_data = item.get("commit", {})
        author_data = commit_data.get("author") or {}

        results.append(
            {
                "sha": item.get("sha"),
                "message": commit_data.get("message"),
                "author": author_data.get("name"),
                "date": author_data.get("date"),
                "html_url": item.get("html_url"),
            }
        )

    return {
        "project": project["name"],
        "repository": "{}/{}".format(
            GITHUB_OWNER,
            repo,
        ),
        "commit_count_returned": len(results),
        "commits": results,
    }


async def get_issues(
    project_key: str,
    limit: int = 20,
) -> Dict[str, Any]:

    project = _get_project(project_key)

    repo = project["repo"]

    issues = await _get(
        "/repos/{}/{}/issues".format(
            GITHUB_OWNER,
            repo,
        ),
        params={
            "state": "open",
            "per_page": limit,
            "sort": "updated",
            "direction": "desc",
        },
    )

    # GitHub's Issues endpoint may also return pull requests.
    # Filter those out here so the COS sees true issues separately.
    issue_results = []

    for item in issues:
        if item.get("pull_request"):
            continue

        issue_results.append(
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "html_url": item.get("html_url"),
            }
        )

    return {
        "project": project["name"],
        "open_issue_count_returned": len(issue_results),
        "issues": issue_results,
    }


async def get_pull_requests(
    project_key: str,
    limit: int = 20,
) -> Dict[str, Any]:

    project = _get_project(project_key)

    repo = project["repo"]

    pulls = await _get(
        "/repos/{}/{}/pulls".format(
            GITHUB_OWNER,
            repo,
        ),
        params={
            "state": "open",
            "per_page": limit,
            "sort": "updated",
            "direction": "desc",
        },
    )

    results = []

    for item in pulls:
        results.append(
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "draft": item.get("draft"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "html_url": item.get("html_url"),
            }
        )

    return {
        "project": project["name"],
        "open_pull_request_count": len(results),
        "pull_requests": results,
    }


async def get_branches(
    project_key: str,
    limit: int = 50,
) -> Dict[str, Any]:

    project = _get_project(project_key)

    repo = project["repo"]

    branches = await _get(
        "/repos/{}/{}/branches".format(
            GITHUB_OWNER,
            repo,
        ),
        params={
            "per_page": limit,
        },
    )

    results = []

    for branch in branches:
        commit = branch.get("commit") or {}

        results.append(
            {
                "name": branch.get("name"),
                "protected": branch.get("protected"),
                "commit_sha": commit.get("sha"),
            }
        )

    return {
        "project": project["name"],
        "branch_count": len(results),
        "branches": results,
    }


async def get_project_summary(
    project_key: str,
) -> Dict[str, Any]:

    repository = await get_repository(project_key)
    commits = await get_commits(project_key, 5)
    issues = await get_issues(project_key, 20)
    pull_requests = await get_pull_requests(project_key, 20)

    latest_commit = None

    if commits["commits"]:
        latest_commit = commits["commits"][0]

    return {
        "project": repository["project"],
        "business_status": repository["business_status"],
        "deployment": repository["deployment"],
        "repository": repository["repository"],
        "default_branch": repository["default_branch"],
        "pushed_at": repository["pushed_at"],
        "latest_commit": latest_commit,
        "open_issues": issues["open_issue_count_returned"],
        "open_pull_requests": pull_requests["open_pull_request_count"],
        "archived": repository["archived"],
    }


async def get_active_project_activity() -> Dict[str, Any]:

    results = []

    for project_key, project in PROJECTS.items():

        if not project["include_in_daily_brief"]:
            continue

        try:
            summary = await get_project_summary(project_key)

            results.append(
                {
                    "project": project["name"],
                    "status": "AVAILABLE",
                    "data": summary,
                }
            )

        except GitHubError as exc:
            results.append(
                {
                    "project": project["name"],
                    "status": "ERROR",
                    "error": str(exc),
                }
            )

    return {
        "active_projects": results,
        "count": len(results),
    }


def get_registered_projects() -> Dict[str, Any]:

    projects = []

    for project_key, project in PROJECTS.items():
        projects.append(
            {
                "project_key": project_key,
                "name": project["name"],
                "repository": "{}/{}".format(
                    GITHUB_OWNER,
                    project["repo"],
                ),
                "business_status": project["business_status"],
                "deployment": project["deployment"],
                "include_in_daily_brief": project[
                    "include_in_daily_brief"
                ],
            }
        )

    return {
        "projects": projects,
        "count": len(projects),
    }