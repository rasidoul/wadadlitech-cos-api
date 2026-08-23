"""Diagnostic script: verifies COS API authentication against the deployed
Render service and reports Google integration status.

This performs a live network call to the deployed COS API (not localhost)
using the local COS_API_KEY from .env. Never prints the actual API key or
any other secret value.

Usage:
    python scripts/test_cos_google_status.py
"""

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

# Ensure the project root is importable when run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COS_API_BASE_URL = os.getenv(
    "COS_API_BASE_URL", "https://wadadlitech-cos-api.onrender.com"
)
COS_API_KEY = os.getenv("COS_API_KEY", "")

STATUS_PATH = "/integrations/google/status"


def main():
    print("COS API base URL: {}".format(COS_API_BASE_URL))
    print("COS_API_KEY loaded: {} (length {})".format(
        "YES" if COS_API_KEY else "NO", len(COS_API_KEY)
    ))

    if not COS_API_KEY:
        print("FAIL: COS_API_KEY is empty. Check .env and that it is loaded.")
        return 1

    url = "{}{}".format(COS_API_BASE_URL, STATUS_PATH)

    try:
        response = httpx.get(
            url,
            headers={"Authorization": "Bearer {}".format(COS_API_KEY)},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        print("FAIL: request error: {}".format(exc))
        return 1

    print("HTTP status: {}".format(response.status_code))
    print("Response body: {}".format(response.text))

    if response.status_code == 401 or response.status_code == 403:
        print(
            "FAIL: authentication rejected. The local COS_API_KEY likely "
            "does not match the COS_API_KEY configured on Render."
        )
        return 1

    if response.status_code >= 400:
        print("FAIL: request reached the API but returned an error.")
        return 1

    print("OK: authenticated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
