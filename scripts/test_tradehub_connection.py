"""Opt-in integration check: verifies the deployed COS API can reach the real
TradeHub service using the configured environment variables.

This performs a live network call to TRADEHUB_BASE_URL and is never run
automatically (not part of `pytest tests/`). Never prints the Bearer token.

Usage:
    python scripts/test_tradehub_connection.py
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Ensure the project root is importable when run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.tradehub import (  # noqa: E402
    TRADEHUB_BASE_URL,
    TRADEHUB_COS_API_KEY,
    TradeHubError,
    get_tradehub_status,
)


async def main():
    print("TradeHub base URL: {}".format(TRADEHUB_BASE_URL))
    print(
        "TRADEHUB_COS_API_KEY configured: {}".format(
            "YES" if TRADEHUB_COS_API_KEY else "NO"
        )
    )

    if not TRADEHUB_COS_API_KEY:
        print("FAIL: TRADEHUB_COS_API_KEY is not set. Aborting.")
        return 1

    try:
        status = await get_tradehub_status()
    except TradeHubError as exc:
        print("FAIL: {}".format(exc))
        return 1

    print("OK: TradeHub responded successfully.")
    print("Status payload: {}".format(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
