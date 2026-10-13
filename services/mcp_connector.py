"""Remote MCP connector for Claude (claude.ai custom connectors, Claude Code).

Exposes the COS API's existing routes as MCP tools over the Streamable HTTP
transport at ``/mcp``, and a minimal single-owner OAuth 2.1 authorization
server so Claude can connect without ever seeing COS_API_KEY.

Design notes:

- Stateless. Client registrations, authorization codes, access tokens and
  refresh tokens are HMAC-signed blobs, so nothing is stored in SQLite and a
  Render redeploy does not disconnect Claude. Rotating COS_API_KEY (or
  COS_MCP_SIGNING_SECRET) revokes every token at once.
- The consent page asks for COS_API_KEY. Only the person holding the key can
  approve a connection; the key itself never leaves this server afterwards.
- Redirect URIs are allowlisted (claude.ai callback + Claude Code loopback),
  so nobody can use this flow to mint tokens for a third-party site.
- Tools call the existing FastAPI routes in-process, so every route keeps its
  own validation and business rules (including the TradeHub live-execution
  gate). New routes become tools automatically.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

SERVER_NAME = "wadadlitech-cos"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SCOPE = "cos"

CODE_TTL_SECONDS = 300
ACCESS_TTL_SECONDS = 3600
REFRESH_TTL_SECONDS = 30 * 24 * 3600

ALLOWED_REDIRECT_URIS = {
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
}
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1"}

# Routes that should not become tools: deprecated, device-only, duplicates of
# newer routes, or the connector's own plumbing.
_EXCLUDED_PATH_PREFIXES = (
    "/github",
    "/integrations/reminders",
    "/mcp",
    "/oauth",
    "/.well-known",
)
_EXCLUDED_PATHS = {"/", "/docs", "/redoc", "/openapi.json"}

# Friendlier names for routes whose operationId FastAPI generated.
_TOOL_NAME_OVERRIDES = {
    "/executive-summary": "get_executive_summary",
    "/health": "get_api_health",
}

# Extra safety hints so Claude asks before doing anything that leaves the API.
_DESTRUCTIVE_OPERATIONS = {
    "sendGmailDraft",
    "requestTradeHubTrade",
    "requestTradeHubClose",
    "archiveGmailMessage",
}
_READ_ONLY_POST_OPERATIONS = {"checkCalendarAvailability"}
_OPEN_WORLD_OPERATIONS = {"sendGmailDraft", "requestTradeHubTrade", "requestTradeHubClose"}

_used_token_ids: Dict[str, float] = {}
_failed_logins: List[float] = []


# =========================================================
# CONFIG
# =========================================================

def _api_key() -> Optional[str]:
    return os.getenv("COS_API_KEY")


def _signing_key() -> bytes:
    secret = os.getenv("COS_MCP_SIGNING_SECRET") or _api_key() or ""
    if not secret:
        raise RuntimeError("COS_API_KEY is not configured.")
    return hmac.new(secret.encode(), b"wadadlitech-cos-mcp-oauth-v1", hashlib.sha256).digest()


def _base_url(request: Request) -> str:
    configured = os.getenv("COS_PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
    if configured:
        return configured.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{proto}://{host}"


def _extra_redirect_uris() -> set:
    raw = os.getenv("COS_MCP_EXTRA_REDIRECT_URIS", "")
    return {uri.strip() for uri in raw.split(",") if uri.strip()}


def is_allowed_redirect_uri(uri: str) -> bool:
    if uri in ALLOWED_REDIRECT_URIS or uri in _extra_redirect_uris():
        return True
    parsed = urlparse(uri)
    # Claude Code uses an RFC 8252 loopback redirect on an ephemeral port.
    return (
        parsed.scheme == "http"
        and parsed.hostname in _LOOPBACK_HOSTS
        and parsed.path == "/callback"
        and not parsed.query
        and not parsed.fragment
    )


# =========================================================
# SIGNED TOKENS
# =========================================================

def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(payload: Dict[str, Any]) -> str:
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64e(mac)}"


def _verify(token: str, typ: str) -> Optional[Dict[str, Any]]:
    if not token or token.count(".") != 1:
        return None
    body, mac = token.split(".")
    expected = hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_b64d(mac), expected):
            return None
        payload = json.loads(_b64d(body))
    except (ValueError, TypeError):
        return None
    if payload.get("typ") != typ:
        return None
    exp = payload.get("exp")
    if exp is not None and exp < time.time():
        return None
    return payload


def _client_ref(client_id: str) -> str:
    return hashlib.sha256(client_id.encode()).hexdigest()[:32]


def _consume_once(jti: str, exp: float) -> bool:
    """Mark a single-use token id as spent. False if it was already used."""
    now = time.time()
    for key in [k for k, v in _used_token_ids.items() if v < now]:
        _used_token_ids.pop(key, None)
    if jti in _used_token_ids:
        return False
    _used_token_ids[jti] = exp
    return True


def _issue_tokens(client_id: str, scope: str, resource: Optional[str]) -> Dict[str, Any]:
    now = int(time.time())
    common = {"cid": _client_ref(client_id), "scope": scope, "aud": resource}
    access = _sign({**common, "typ": "access", "exp": now + ACCESS_TTL_SECONDS, "jti": secrets.token_hex(8)})
    refresh = _sign({**common, "typ": "refresh", "exp": now + REFRESH_TTL_SECONDS, "jti": secrets.token_hex(16)})
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL_SECONDS,
        "refresh_token": refresh,
        "scope": scope,
    }


def is_authorized_bearer(authorization: Optional[str]) -> bool:
    if not authorization or not authorization.lower().startswith("bearer "):
        return False
    token = authorization[7:].strip()
    key = _api_key()
    # Static header path: Claude Code `--header` or claude.ai request headers.
    if key and hmac.compare_digest(token.encode(), key.encode()):
        return True
    return _verify(token, "access") is not None


# =========================================================
# TOOL REGISTRY (generated from the app's own OpenAPI)
# =========================================================

def _snake(name: str) -> str:
    name = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    return re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_").lower()


def _resolve_refs(schema: Any, components: Dict[str, Any], seen: Tuple[str, ...] = ()) -> Any:
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[-1]
            if name in seen:
                return {"type": "object"}
            target = components.get(name, {"type": "object"})
            merged = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
            return _resolve_refs(merged, components, seen + (name,))
        return {k: _resolve_refs(v, components, seen) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_resolve_refs(item, components, seen) for item in schema]
    return schema


def _is_excluded(path: str, operation: Dict[str, Any]) -> bool:
    if path in _EXCLUDED_PATHS or path.startswith(_EXCLUDED_PATH_PREFIXES):
        return True
    op_id = operation.get("operationId") or ""
    summary = operation.get("summary") or ""
    return op_id.startswith("legacy") or summary.lower().startswith("legacy") or operation.get("deprecated", False)


def _tool_name(path: str, operation: Dict[str, Any], endpoint_names: Dict[Tuple[str, str], str], method: str) -> str:
    if path in _TOOL_NAME_OVERRIDES and method == "get":
        return _TOOL_NAME_OVERRIDES[path]
    op_id = operation.get("operationId") or ""
    if re.fullmatch(r"[a-z][a-zA-Z0-9]*", op_id) and any(c.isupper() for c in op_id):
        return _snake(op_id)
    endpoint = endpoint_names.get((path, method.upper()), op_id)
    return _snake(re.sub(r"^api_", "", endpoint))[:64]


def build_tools(app: FastAPI) -> Dict[str, Dict[str, Any]]:
    spec = app.openapi()
    components = (spec.get("components") or {}).get("schemas", {})
    endpoint_names: Dict[Tuple[str, str], str] = {}
    for route in app.routes:
        for method in getattr(route, "methods", None) or ():
            endpoint_names[(getattr(route, "path", ""), method)] = getattr(route.endpoint, "__name__", "")

    tools: Dict[str, Dict[str, Any]] = {}
    for path, item in (spec.get("paths") or {}).items():
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete"} or not isinstance(operation, dict):
                continue
            if _is_excluded(path, operation):
                continue

            properties: Dict[str, Any] = {}
            required: List[str] = []
            path_params: List[str] = []
            query_params: List[str] = []
            for param in operation.get("parameters", []):
                where = param.get("in")
                if where not in {"path", "query"}:
                    continue
                pname = param["name"]
                pschema = _resolve_refs(param.get("schema", {}), components)
                if param.get("description") and "description" not in pschema:
                    pschema = {**pschema, "description": param["description"]}
                properties[pname] = pschema
                (path_params if where == "path" else query_params).append(pname)
                if param.get("required"):
                    required.append(pname)

            body_mode = None
            body_fields: List[str] = []
            content = ((operation.get("requestBody") or {}).get("content") or {}).get("application/json")
            if content:
                body_schema = _resolve_refs(content.get("schema", {}), components)
                body_props = body_schema.get("properties")
                if body_props and not set(body_props) & set(properties):
                    body_mode = "flat"
                    body_fields = list(body_props)
                    properties.update(body_props)
                    required.extend(body_schema.get("required", []))
                else:
                    body_mode = "body"
                    properties["body"] = body_schema
                    if (operation.get("requestBody") or {}).get("required"):
                        required.append("body")

            op_id = operation.get("operationId") or ""
            name = _tool_name(path, operation, endpoint_names, method)
            if name in tools:
                name = f"{name}_{method}"[:64]
            title = operation.get("summary") or name
            description = (operation.get("description") or "").strip()
            text = f"{title}. {description}" if description else title
            text = f"{text}\n\nCOS API: {method.upper()} {path}"

            readonly = method == "get" or op_id in _READ_ONLY_POST_OPERATIONS
            annotations = {
                "title": title,
                "readOnlyHint": readonly,
                "destructiveHint": (method == "delete" or op_id in _DESTRUCTIVE_OPERATIONS) if not readonly else False,
                "openWorldHint": op_id in _OPEN_WORLD_OPERATIONS,
            }
            input_schema: Dict[str, Any] = {"type": "object", "properties": properties}
            if required:
                input_schema["required"] = sorted(set(required))

            tools[name] = {
                "definition": {
                    "name": name,
                    "title": title,
                    "description": text[:2000],
                    "inputSchema": input_schema,
                    "annotations": annotations,
                },
                "method": method.upper(),
                "path": path,
                "path_params": path_params,
                "query_params": query_params,
                "body_mode": body_mode,
                "body_fields": body_fields,
            }
    return tools


async def call_tool(app: FastAPI, tool: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    arguments = dict(arguments or {})
    path = tool["path"]
    for pname in tool["path_params"]:
        if pname not in arguments:
            return _tool_result(f"Missing required argument: {pname}", True)
        path = path.replace("{" + pname + "}", quote(str(arguments.pop(pname)), safe=""))
    params = {k: arguments.pop(k) for k in tool["query_params"] if k in arguments and arguments[k] is not None}
    params = {
        k: str(v).lower() if isinstance(v, bool) else json.dumps(v) if isinstance(v, dict) else v
        for k, v in params.items()
    }

    body = None
    if tool["body_mode"] == "flat":
        body = {k: arguments[k] for k in tool["body_fields"] if k in arguments}
    elif tool["body_mode"] == "body":
        body = arguments.get("body", {})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://cos.internal", timeout=120) as client:
        response = await client.request(
            tool["method"],
            path,
            params=params,
            json=body if tool["body_mode"] else None,
            headers={"Authorization": f"Bearer {_api_key()}"},
        )
    try:
        payload: Any = response.json()
        text = json.dumps(payload, indent=2, default=str)
    except ValueError:
        text = response.text
    if response.status_code >= 400:
        text = f"COS API returned HTTP {response.status_code}:\n{text}"
    return _tool_result(text, response.status_code >= 400)


def _tool_result(text: str, is_error: bool) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


# =========================================================
# CONSENT PAGE
# =========================================================

def _consent_page(fields: Dict[str, str], client_name: str, error: str = "") -> HTMLResponse:
    host = urlparse(fields.get("redirect_uri", "")).hostname or "unknown"
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in fields.items()
    )
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect to WadadliTech COS</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:420px;margin:48px auto;padding:0 16px;color:#1a1a1a}}
input[type=password]{{width:100%;padding:10px;font-size:16px;box-sizing:border-box;margin:8px 0 16px}}
button{{padding:10px 18px;font-size:16px;cursor:pointer}}
.error{{color:#b00020}} .muted{{color:#555;font-size:14px}}
</style></head><body>
<h1>Connect to WadadliTech COS</h1>
<p><strong>{html.escape(client_name)}</strong> is asking for access to the Chief of Staff API
(tasks, executive summary, HighLevel, Google, TradeHub).</p>
<p class="muted">After you approve, you will be sent back to <strong>{html.escape(host)}</strong>.</p>
{error_html}
<form method="post" action="/oauth/authorize">
{hidden}
<label for="key">COS API key</label>
<input id="key" name="api_key" type="password" autocomplete="current-password" required autofocus>
<button type="submit">Approve</button>
</form>
</body></html>"""
    return HTMLResponse(page, status_code=400 if error else 200, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


def _oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


async def _form(request: Request) -> Dict[str, str]:
    raw = (await request.body()).decode("utf-8", errors="replace")
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}


def _authorize_params(params: Dict[str, str]) -> Tuple[Optional[str], Dict[str, Any], Dict[str, str]]:
    """Validate an authorization request. Returns (error, client, fields)."""
    client = _verify(params.get("client_id", ""), "client")
    if client is None:
        return "Unknown client. Remove the connector in Claude and add it again.", {}, {}
    redirect_uri = params.get("redirect_uri", "")
    if redirect_uri not in client.get("redirect_uris", []) or not is_allowed_redirect_uri(redirect_uri):
        return "Redirect URI is not allowed.", client, {}
    if params.get("response_type") != "code":
        return "Only response_type=code is supported.", client, {}
    if not params.get("code_challenge") or params.get("code_challenge_method") != "S256":
        return "PKCE with S256 is required.", client, {}
    fields = {
        key: params.get(key, "")
        for key in ("client_id", "redirect_uri", "response_type", "code_challenge", "code_challenge_method", "state", "scope", "resource")
    }
    return None, client, fields


# =========================================================
# ROUTES
# =========================================================

def register_mcp(app: FastAPI) -> None:
    tool_cache: Dict[str, Dict[str, Any]] = {}

    def tools() -> Dict[str, Dict[str, Any]]:
        if not tool_cache:
            tool_cache.update(build_tools(app))
        return tool_cache

    def protected_resource_metadata(request: Request) -> JSONResponse:
        base = _base_url(request)
        return JSONResponse({
            "resource": f"{base}/mcp",
            "authorization_servers": [base],
            "scopes_supported": [SCOPE],
            "bearer_methods_supported": ["header"],
            "resource_name": "WadadliTech Chief of Staff API",
        })

    app.add_api_route("/.well-known/oauth-protected-resource", protected_resource_metadata, methods=["GET"], include_in_schema=False)
    app.add_api_route("/.well-known/oauth-protected-resource/mcp", protected_resource_metadata, methods=["GET"], include_in_schema=False)

    @app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    async def authorization_server_metadata(request: Request):
        base = _base_url(request)
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [SCOPE],
        }

    @app.post("/oauth/register", include_in_schema=False)
    async def oauth_register(request: Request):
        try:
            data = await request.json()
        except ValueError:
            return _oauth_error("invalid_client_metadata", "Body must be JSON.")
        redirect_uris = data.get("redirect_uris") or []
        if not isinstance(redirect_uris, list) or not redirect_uris:
            return _oauth_error("invalid_redirect_uri", "redirect_uris is required.")
        bad = [uri for uri in redirect_uris if not isinstance(uri, str) or not is_allowed_redirect_uri(uri)]
        if bad:
            return _oauth_error("invalid_redirect_uri", f"Redirect URI not allowed: {bad[0]}")
        client_name = str(data.get("client_name") or "Claude")[:100]
        client_id = _sign({"typ": "client", "redirect_uris": redirect_uris, "name": client_name, "iat": int(time.time())})
        return JSONResponse(
            {
                "client_id": client_id,
                "client_id_issued_at": int(time.time()),
                "client_name": client_name,
                "redirect_uris": redirect_uris,
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            status_code=201,
        )

    @app.get("/oauth/authorize", include_in_schema=False)
    async def oauth_authorize_page(request: Request):
        error, client, fields = _authorize_params(dict(request.query_params))
        if error:
            return HTMLResponse(f"<p>{html.escape(error)}</p>", status_code=400)
        return _consent_page(fields, client.get("name", "Claude"))

    @app.post("/oauth/authorize", include_in_schema=False)
    async def oauth_authorize_submit(request: Request):
        form = await _form(request)
        error, client, fields = _authorize_params(form)
        if error:
            return HTMLResponse(f"<p>{html.escape(error)}</p>", status_code=400)

        now = time.time()
        _failed_logins[:] = [t for t in _failed_logins if t > now - 900]
        if len(_failed_logins) >= 10:
            return _consent_page(fields, client.get("name", "Claude"), "Too many failed attempts. Try again in 15 minutes.")

        key = _api_key() or ""
        if not key or not hmac.compare_digest(form.get("api_key", "").encode(), key.encode()):
            _failed_logins.append(now)
            return _consent_page(fields, client.get("name", "Claude"), "That key is not correct.")

        code = _sign({
            "typ": "code",
            "cid": _client_ref(fields["client_id"]),
            "redirect_uri": fields["redirect_uri"],
            "cc": fields["code_challenge"],
            "scope": SCOPE,
            "resource": fields.get("resource") or None,
            "exp": int(now) + CODE_TTL_SECONDS,
            "jti": secrets.token_hex(16),
        })
        query = {"code": code}
        if fields.get("state"):
            query["state"] = fields["state"]
        separator = "&" if "?" in fields["redirect_uri"] else "?"
        return RedirectResponse(f"{fields['redirect_uri']}{separator}{urlencode(query)}", status_code=302)

    @app.post("/oauth/token", include_in_schema=False)
    async def oauth_token(request: Request):
        form = await _form(request)
        grant_type = form.get("grant_type")
        client_id = form.get("client_id", "")
        if _verify(client_id, "client") is None:
            return _oauth_error("invalid_client", "Unknown client.", 401)

        if grant_type == "authorization_code":
            code = _verify(form.get("code", ""), "code")
            if code is None or code.get("cid") != _client_ref(client_id):
                return _oauth_error("invalid_grant", "Authorization code is invalid or expired.")
            if form.get("redirect_uri") != code.get("redirect_uri"):
                return _oauth_error("invalid_grant", "redirect_uri does not match.")
            verifier = form.get("code_verifier", "")
            challenge = _b64e(hashlib.sha256(verifier.encode()).digest())
            if not verifier or not hmac.compare_digest(challenge, code.get("cc", "")):
                return _oauth_error("invalid_grant", "PKCE verification failed.")
            if not _consume_once(code["jti"], code["exp"]):
                return _oauth_error("invalid_grant", "Authorization code was already used.")
            return JSONResponse(_issue_tokens(client_id, code.get("scope", SCOPE), code.get("resource")), headers={"Cache-Control": "no-store"})

        if grant_type == "refresh_token":
            refresh = _verify(form.get("refresh_token", ""), "refresh")
            if refresh is None or refresh.get("cid") != _client_ref(client_id):
                return _oauth_error("invalid_grant", "Refresh token is invalid or expired.")
            if not _consume_once(refresh["jti"], refresh["exp"]):
                return _oauth_error("invalid_grant", "Refresh token was already used.")
            return JSONResponse(_issue_tokens(client_id, refresh.get("scope", SCOPE), refresh.get("aud")), headers={"Cache-Control": "no-store"})

        return _oauth_error("unsupported_grant_type", "Use authorization_code or refresh_token.")

    def unauthorized(request: Request) -> JSONResponse:
        base = _base_url(request)
        return JSONResponse(
            {"error": "unauthorized", "error_description": "Sign in to the WadadliTech COS connector."},
            status_code=401,
            headers={"WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource", scope="{SCOPE}"'},
        )

    async def handle_message(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if msg_id is None:
            return None  # notification

        def ok(result: Dict[str, Any]) -> Dict[str, Any]:
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}

        def err(code: int, text: str) -> Dict[str, Any]:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            return ok({
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": "WadadliTech Chief of Staff", "version": app.version},
                "instructions": (
                    "WadadliTech Chief of Staff API. Start daily briefs with get_executive_summary. "
                    "Tasks, HighLevel CRM, Gmail, Calendar, Drive, planning, agent briefs and TradeHub "
                    "monitoring are available as tools. Get the user's explicit approval before sending "
                    "email, changing the calendar, or requesting any trade."
                ),
            })
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": [tool["definition"] for tool in tools().values()]})
        if method == "tools/call":
            tool = tools().get(params.get("name", ""))
            if tool is None:
                return err(-32602, f"Unknown tool: {params.get('name')}")
            try:
                return ok(await call_tool(app, tool, params.get("arguments") or {}))
            except Exception as exc:  # surface failures to Claude instead of a 500
                return ok(_tool_result(f"Tool failed: {exc}", True))
        if method in {"resources/list", "prompts/list"}:
            return ok({method.split("/")[0]: []})
        return err(-32601, f"Method not found: {method}")

    @app.post("/mcp", include_in_schema=False)
    async def mcp_endpoint(request: Request):
        if not is_authorized_bearer(request.headers.get("authorization")):
            return unauthorized(request)
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)

        if isinstance(payload, list):
            responses = [r for r in [await handle_message(m) for m in payload if isinstance(m, dict)] if r]
            return JSONResponse(responses) if responses else Response(status_code=202)
        if not isinstance(payload, dict):
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}, status_code=400)
        response = await handle_message(payload)
        return JSONResponse(response) if response else Response(status_code=202)

    @app.api_route("/mcp", methods=["GET", "DELETE"], include_in_schema=False)
    async def mcp_no_stream(request: Request):
        if not is_authorized_bearer(request.headers.get("authorization")):
            return unauthorized(request)
        # Stateless server: no standalone SSE stream and no sessions to delete.
        return Response(status_code=405, headers={"Allow": "POST"})
