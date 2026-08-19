"""Registry of AI agents that the Chief of Staff can address directly."""

from typing import Any, Dict, Optional

# =========================================================
# AGENT REGISTRY
# =========================================================

AGENT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "WD-AI-001": {
        "agent_id": "WD-AI-001",
        "name": "Chief of Staff",
        "company": "Wadadli Technology Consulting LLC",
        "account_key": "wadadlitech",
        "department": "Executive",
        "role": "Chief of Staff AI Agent",
        "reports_to": None,
        "status": "ACTIVE",
    },

    "MAYA-WD-MKT-001": {
        "agent_id": "MAYA-WD-MKT-001",
        "name": "Maya",
        "company": "Wadadli Technology Consulting LLC",
        "account_key": "wadadlitech",
        "department": "Marketing",
        "role": "Marketing AI Agent",
        "reports_to": "WD-AI-001",
        "status": "ACTIVE",
    },
}

# Maps an agent_id to the name of the environment variable that holds its
# outbound delivery webhook, if the agent supports brief delivery.
AGENT_WEBHOOK_ENV_VARS: Dict[str, str] = {
    "MAYA-WD-MKT-001": "MAYA_WD_WEBHOOK_URL",
}


def get_registered_agents() -> Dict[str, Any]:
    agents = list(AGENT_REGISTRY.values())

    return {
        "count": len(agents),
        "agents": agents,
    }


def get_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    return AGENT_REGISTRY.get(agent_id)


def agent_exists(agent_id: str) -> bool:
    return agent_id in AGENT_REGISTRY


def get_webhook_env_var(agent_id: str) -> Optional[str]:
    return AGENT_WEBHOOK_ENV_VARS.get(agent_id)
