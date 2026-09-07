import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient

import main

COS_API_KEY = os.environ["COS_API_KEY"]
AUTH_HEADERS = {"Authorization": "Bearer {}".format(COS_API_KEY)}

client = TestClient(main.app)


# ---------------------------------------------------------------------------
# The COS API no longer aggregates GitHub engineering activity. GitHub
# repository/commit/issue/PR access is handled exclusively by the direct
# GitHub connector outside this API.
# ---------------------------------------------------------------------------


def test_no_github_token_required_to_start_api():
    assert "GITHUB_TOKEN" not in os.environ
    assert "GITHUB_OWNER" not in os.environ

    response = client.get("/health")
    assert response.status_code == 200


def test_health_still_works():
    response = client.get("/health")
    assert response.status_code == 200


def test_executive_summary_works_without_github_aggregation():
    response = client.get("/executive-summary", headers=AUTH_HEADERS)
    assert response.status_code == 200

    body = response.json()

    assert "github" not in body["source_health"]
    assert body["engineering"]["available"] is False
    assert body["engineering"]["source_of_truth"] == "direct_github_connector"


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/github/projects"),
        ("get", "/github/activity"),
        ("get", "/github/projects/tradehub"),
        ("get", "/github/projects/tradehub/repository"),
        ("get", "/github/projects/tradehub/commits"),
        ("get", "/github/projects/tradehub/issues"),
        ("get", "/github/projects/tradehub/pulls"),
        ("get", "/github/projects/tradehub/branches"),
    ],
)
def test_deprecated_github_routes_return_410(method, path):
    response = getattr(client, method)(path, headers=AUTH_HEADERS)

    assert response.status_code == 410
    assert "direct GitHub connector" in response.json()["detail"]


def test_no_stale_github_service_module():
    assert not os.path.exists(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "services",
            "github.py",
        )
    )

    assert "services.github" not in sys.modules

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("services.github")


def test_main_has_no_github_service_symbols():
    for name in (
        "GitHubError",
        "get_registered_projects",
        "get_repository",
        "get_commits",
        "get_issues",
        "get_pull_requests",
        "get_branches",
        "get_project_summary",
        "get_active_project_activity",
        "run_github_call",
    ):
        assert not hasattr(main, name)


def test_task_registry_endpoints_still_work():
    response = client.get("/tasks/today", headers=AUTH_HEADERS)
    assert response.status_code == 200

    response = client.get("/tasks/overdue", headers=AUTH_HEADERS)
    assert response.status_code == 200

    response = client.get("/tasks/priorities", headers=AUTH_HEADERS)
    assert response.status_code == 200
