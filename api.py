"""
FastAPI app for Brief.

Serves:
  GET  /            — dashboard UI (dashboard.html)
  GET  /health      — health check
  GET  /api/rules   — list all rules
  POST /api/rules   — add a rule
  PUT  /api/rules/{id} — update a rule
  DELETE /api/rules/{id} — delete a rule
  GET  /api/stats   — stats (token usage, count, etc.)
  POST /api/extract  — extract candidate rules from a transcript
  POST /api/maintain — audit rulebook for verbosity/redundancy

  /mcp              — MCP server endpoint (Streamable HTTP transport)
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from difflib import SequenceMatcher

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import db
from server import mcp, SIMILARITY_THRESHOLD, VALID_CATEGORIES

DASHBOARD = Path(__file__).parent / "dashboard.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Brief", description="Personal AI rulebook server", lifespan=lifespan)

# Mount the MCP server at /mcp.
# Streamable HTTP transport exposes a single endpoint — clients connect to:
#   http://localhost:8080/mcp
# If your mcp SDK version doesn't have streamable_http_app(), fall back to SSE:
#   clients connect to http://localhost:8080/mcp/sse instead.
try:
    mcp_asgi = mcp.streamable_http_app()
    _transport = "streamable-http"
except AttributeError:
    mcp_asgi = mcp.sse_app()
    _transport = "sse"


class _MCPDispatcher:
    """Route /mcp to FastMCP (full path, no prefix stripping) and everything
    else to FastAPI. Also runs both apps' lifespans concurrently so FastMCP's
    session manager task group is properly initialized."""

    def __init__(self, fastapi_app, mcp_app):
        self._api = fastapi_app
        self._mcp = mcp_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._lifespan(scope, receive, send)
            return
        path = scope.get("path", "")
        if scope["type"] == "http" and (path == "/mcp" or path.startswith("/mcp/")):
            # FastMCP validates the Host header against an allowlist as a DNS rebinding
            # defense. Behind Cloudflare Tunnel or nginx the host is the public domain,
            # not localhost, so we rewrite it to the value we allowed in TransportSecuritySettings.
            new_headers = []
            for name, value in scope.get("headers", []):
                if name.lower() == b"host":
                    new_headers.append((b"host", b"0.0.0.0"))
                else:
                    new_headers.append((name, value))
            scope["headers"] = new_headers
            await self._mcp(scope, receive, send)
        else:
            await self._api(scope, receive, send)

    async def _lifespan(self, scope, receive, send):
        import asyncio

        api_ready, mcp_ready = asyncio.Event(), asyncio.Event()
        shutdown_req = asyncio.Event()
        api_done, mcp_done = asyncio.Event(), asyncio.Event()

        async def _run_app(app, ready, done):
            sent_startup = False

            async def _recv():
                nonlocal sent_startup
                if not sent_startup:
                    sent_startup = True
                    return {"type": "lifespan.startup"}
                await shutdown_req.wait()
                return {"type": "lifespan.shutdown"}

            async def _send(msg):
                if msg["type"] == "lifespan.startup.complete":
                    ready.set()
                elif msg["type"] == "lifespan.shutdown.complete":
                    done.set()

            await app(scope, _recv, _send)

        task = asyncio.create_task(asyncio.gather(
            _run_app(self._api, api_ready, api_done),
            _run_app(self._mcp, mcp_ready, mcp_done),
        ))

        await api_ready.wait()
        await mcp_ready.wait()
        await send({"type": "lifespan.startup.complete"})

        await receive()  # lifespan.shutdown from uvicorn
        shutdown_req.set()
        await task
        await send({"type": "lifespan.shutdown.complete"})


# ── REST endpoints used by the dashboard ──────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "brief", "transport": _transport}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD.read_text()


@app.get("/api/rules")
def api_list_rules():
    return db.list_rules()


class RuleBody(BaseModel):
    text: str
    category: str = "general"


@app.post("/api/rules", status_code=201)
def api_add_rule(body: RuleBody):
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(400, f"Invalid category. Use: {', '.join(sorted(VALID_CATEGORIES))}")

    # Mirror the duplicate check from the MCP tool so the dashboard is consistent
    for rule in db.list_rules():
        score = SequenceMatcher(None, body.text.lower(), rule["text"].lower()).ratio()
        if score >= SIMILARITY_THRESHOLD:
            raise HTTPException(
                409,
                f"Near-duplicate of rule id={rule['id']} ({score:.0%} similar): \"{rule['text']}\"",
            )

    rule_id = db.insert_rule(text=body.text, category=body.category, source="manual")
    return db.get_rule(rule_id)


@app.put("/api/rules/{rule_id}")
def api_update_rule(rule_id: int, body: RuleBody):
    if not db.get_rule(rule_id):
        raise HTTPException(404, "Rule not found")
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(400, f"Invalid category. Use: {', '.join(sorted(VALID_CATEGORIES))}")
    db.update_rule(rule_id=rule_id, text=body.text, category=body.category)
    return db.get_rule(rule_id)


@app.delete("/api/rules/{rule_id}")
def api_delete_rule(rule_id: int):
    if not db.get_rule(rule_id):
        raise HTTPException(404, "Rule not found")
    db.delete_rule(rule_id)
    return {"deleted": rule_id}


@app.get("/api/stats")
def api_stats():
    from server import TOKEN_BUDGET
    stats = db.get_stats()
    stats["token_budget"] = TOKEN_BUDGET
    return stats


class TranscriptBody(BaseModel):
    transcript: str


@app.post("/api/extract")
async def api_extract(body: TranscriptBody):
    from extract import extract_from_transcript

    if not body.transcript.strip():
        raise HTTPException(400, "Transcript is empty")
    try:
        proposals = await extract_from_transcript(body.transcript)
        return {"proposals": proposals}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/maintain")
async def api_maintain():
    from extract import maintain_rules

    rules = db.list_rules()
    if not rules:
        return {"proposals": []}
    try:
        proposals = await maintain_rules(rules)
        return {"proposals": proposals}
    except Exception as exc:
        raise HTTPException(500, str(exc))


if __name__ == "__main__":
    port = int(os.environ.get("BRIEF_PORT", 8080))
    asgi_app = _MCPDispatcher(app, mcp_asgi)
    uvicorn.run(asgi_app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
