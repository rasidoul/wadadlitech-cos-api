"""One-off script to verify GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET work end-to-end.

Opens a browser for Google consent, catches the OAuth callback on
http://localhost:8000/auth/google/callback (already a registered redirect
URI), exchanges the code for tokens, then calls the Gmail and Calendar APIs
to confirm access.

Usage: python scripts/test_google_connection.py
"""

import http.server
import os
import threading
import urllib.parse
import webbrowser

import httpx
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8000/auth/google/callback"
SCOPES = " ".join([
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
])

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

auth_result = {}


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        error = params.get("error", [None])[0]

        # Ignore anything that isn't the actual OAuth redirect (favicon, probes, etc.)
        if parsed.path == "/auth/google/callback" and (code or error):
            auth_result["code"] = code
            auth_result["error"] = error
            message = b"Google authorization complete. You can close this tab."
        else:
            message = b"Waiting for Google authorization..."

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(message)

    def log_message(self, format, *args):
        pass


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise SystemExit("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set in .env")

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    server = http.server.HTTPServer(("localhost", 8000), CallbackHandler)
    server.timeout = 120

    def serve_until_callback():
        # Keep handling requests (ignoring stray ones) until the real callback lands.
        while "code" not in auth_result and "error" not in auth_result:
            server.handle_request()

    thread = threading.Thread(target=serve_until_callback)
    thread.start()

    print("Opening browser for Google authorization...")
    print(url)
    webbrowser.open(url)

    thread.join(timeout=180)
    server.server_close()

    if thread.is_alive():
        raise SystemExit("Timed out waiting for Google authorization callback.")

    if auth_result.get("error"):
        raise SystemExit(f"Google returned an error: {auth_result['error']}")

    code = auth_result.get("code")
    if not code:
        raise SystemExit("No authorization code received.")

    token_resp = httpx.post(TOKEN_URL, data={
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })

    if not token_resp.is_success:
        raise SystemExit(f"Token exchange failed ({token_resp.status_code}): {token_resp.text}")

    tokens = token_resp.json()
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token")

    headers = {"Authorization": f"Bearer {access_token}"}

    gmail_resp = httpx.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers=headers,
    )
    calendar_resp = httpx.get(
        "https://www.googleapis.com/calendar/v3/users/me/calendarList",
        headers=headers,
    )

    print("\nGmail profile:", gmail_resp.status_code, gmail_resp.json() if gmail_resp.is_success else gmail_resp.text)
    print("Calendar list:", calendar_resp.status_code, calendar_resp.json() if calendar_resp.is_success else calendar_resp.text)

    if refresh_token:
        print(f"\nRefresh token (save to .env as GOOGLE_REFRESH_TOKEN):\n{refresh_token}")
    else:
        print(
            "\nNo refresh token returned (Google only issues one on first consent). "
            "Revoke access at https://myaccount.google.com/permissions and re-run to force one."
        )


if __name__ == "__main__":
    main()
