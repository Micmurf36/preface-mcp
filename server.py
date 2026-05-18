"""
Preface MCP server — all tools live here.

Tools exposed to MCP clients:
  get_preface    — returns the full rulebook as a compact string
  add_rule       — adds a rule (with near-duplicate detection)
  update_rule    — edits a rule by id
  delete_rule    — removes a rule by id
  extract_rules  — analyzes a transcript and proposes new rules (no auto-save)
  maintain_preface — audits the rulebook for verbosity and redundancy (no auto-save)
  get_stats      — returns rule count, token estimate, last updated
"""

import os
from difflib import SequenceMatcher

import mcp.server.transport_security as ts
# FastMCP's default DNS rebinding protection rejects requests whose Host header
# isn't localhost/127.0.0.1. That breaks Cloudflare Tunnel and any reverse proxy,
# which forward requests with the public hostname as Host. We replace the middleware
# with a minimal version that still validates Content-Type but skips the host check.
class DummyMiddleware:
    def __init__(self, settings=None): pass
    async def validate_request(self, request, is_post=False):
        if is_post:
            ct = request.headers.get("content-type")
            if not ct or not ct.lower().startswith("application/json"):
                from starlette.responses import Response
                return Response("Invalid Content-Type header", status_code=400)
        return None
ts.TransportSecurityMiddleware = DummyMiddleware

from mcp.server.fastmcp import FastMCP
import db

mcp = FastMCP(
    "preface",
    host="0.0.0.0",
    transport_security=ts.TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
        allowed_hosts=["*"],
        allowed_origins=["*"]
    ),
    instructions=(
        "Preface stores the user's personal AI preferences. "
        "Call get_preface at the start of every conversation to load their rulebook."
    ),
)

# Configurable via PREFACE_TOKEN_BUDGET env var.
# Default is 600 — enough for ~30–40 concise rules without eating into your context window.
TOKEN_BUDGET = int(os.environ.get("PREFACE_TOKEN_BUDGET", "600"))
SIMILARITY_THRESHOLD = 0.85
VALID_CATEGORIES = {"writing", "coding", "tone", "general"}


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 token ≈ 4 characters for English text."""
    return max(1, len(text) // 4)


def _similarity(a: str, b: str) -> float:
    """Returns a 0–1 similarity score between two strings (case-insensitive)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


@mcp.tool()
def get_preface() -> str:
    """
    Returns the user's full AI rulebook as a compact string.
    Call this at the start of every conversation.
    If the rulebook exceeds the token budget, low-use rules are flagged for pruning
    rather than silently truncated.
    """
    rules = db.list_rules()
    if not rules:
        return (
            "[PREFACE] No rules yet.\n"
            "Use add_rule to start building your rulebook, or "
            "paste a transcript into extract_rules to find preferences automatically."
        )

    db.increment_hit_counts()

    # Group rules by category for a readable output
    by_category: dict[str, list[dict]] = {}
    for rule in rules:
        by_category.setdefault(rule["category"], []).append(rule)

    lines = ["[PREFACE]", ""]
    token_count = 0
    pruning_candidates = []

    for category in sorted(by_category.keys()):
        lines.append(f"[{category.title()}]")
        for rule in by_category[category]:
            rule_tokens = _estimate_tokens(rule["text"])
            if token_count + rule_tokens > TOKEN_BUDGET:
                pruning_candidates.append(rule)
                continue
            lines.append(f"- {rule['text']}")
            token_count += rule_tokens
        lines.append("")

    if pruning_candidates:
        lines.append(f"[{len(pruning_candidates)} rule(s) over budget — run maintain_preface to tighten up]")

    return "\n".join(lines)


@mcp.tool()
def add_rule(text: str, category: str = "general") -> str:
    """
    Add a new rule to the rulebook.
    Category must be one of: writing, coding, tone, general.
    Returns a conflict warning if a similar rule already exists (85%+ similarity).
    """
    if category not in VALID_CATEGORIES:
        return (
            f"Invalid category '{category}'. "
            f"Use one of: {', '.join(sorted(VALID_CATEGORIES))}"
        )

    for existing in db.list_rules():
        score = _similarity(text, existing["text"])
        if score >= SIMILARITY_THRESHOLD:
            return (
                f"Near-duplicate detected ({score:.0%} similar to existing rule).\n"
                f"Existing rule id={existing['id']}: \"{existing['text']}\"\n"
                "Use update_rule to modify it, or delete_rule first if you want to replace it."
            )

    rule_id = db.insert_rule(text=text, category=category, source="manual")
    return f"Rule added (id={rule_id})."


@mcp.tool()
def update_rule(rule_id: int, text: str, category: str) -> str:
    """
    Edit an existing rule by its id.
    Use get_stats to see all rule ids and their current text.
    """
    if category not in VALID_CATEGORIES:
        return (
            f"Invalid category '{category}'. "
            f"Use one of: {', '.join(sorted(VALID_CATEGORIES))}"
        )

    if not db.get_rule(rule_id):
        return f"No rule found with id={rule_id}. Use get_stats to list all ids."

    db.update_rule(rule_id=rule_id, text=text, category=category)
    return f"Rule id={rule_id} updated."


@mcp.tool()
def delete_rule(rule_id: int) -> str:
    """
    Delete a rule permanently by its id.
    Use get_preface or get_stats to find the id you want to remove.
    """
    if not db.get_rule(rule_id):
        return f"No rule found with id={rule_id}. Use get_stats to list all ids."

    db.delete_rule(rule_id)
    return f"Rule id={rule_id} deleted."


@mcp.tool()
async def extract_rules(transcript: str) -> str:
    """
    Analyze a conversation transcript and propose durable preference rules found in it.
    Does NOT save anything automatically — returns proposals for you to review.
    Call add_rule for each proposal you want to keep.
    """
    from extract import extract_from_transcript

    if not transcript.strip():
        return "Transcript is empty — paste in a conversation to analyze."

    try:
        proposals = await extract_from_transcript(transcript)
    except Exception as exc:
        return f"Extraction failed: {exc}"

    if not proposals:
        return (
            "No durable preferences found in this transcript.\n"
            "The model only extracts habits that apply to future conversations — "
            "one-off requests and project-specific context are ignored."
        )

    lines = [f"Found {len(proposals)} candidate rule(s):\n"]
    for i, rule in enumerate(proposals, 1):
        lines.append(f"  {i}. [{rule['category']}] {rule['text']}")
    lines.append("\nTo save: add_rule(text=\"...\", category=\"...\")")
    return "\n".join(lines)


@mcp.tool()
async def maintain_preface() -> str:
    """
    Audit your rulebook with an LLM and get proposals to merge overlapping rules,
    shorten verbose ones, or delete redundant ones.
    Nothing changes automatically — review proposals, then apply with
    update_rule, delete_rule, or add_rule (for merges: delete the originals first).
    Run this occasionally when your rulebook feels bloated or over budget.
    """
    from extract import maintain_rules

    rules = db.list_rules()
    if not rules:
        return "No rules to audit yet."
    if len(rules) < 2:
        return "Only one rule — nothing to merge or compare yet."

    try:
        proposals = await maintain_rules(rules)
    except Exception as exc:
        return f"Maintenance audit failed: {exc}"

    if not proposals:
        return "Your rulebook looks tight — no improvements suggested."

    lines = [f"Maintenance audit: {len(proposals)} suggestion(s)\n"]
    for i, p in enumerate(proposals, 1):
        ids_str = " + ".join(f"id={x}" for x in p["rule_ids"])
        if p["action"] == "merge":
            lines.append(f"  {i}. MERGE {ids_str}")
            lines.append(f"     → \"{p['new_text']}\" [{p['new_category']}]")
        elif p["action"] == "reword":
            lines.append(f"  {i}. SHORTEN {ids_str}")
            lines.append(f"     → \"{p['new_text']}\" [{p['new_category']}]")
        elif p["action"] == "delete":
            lines.append(f"  {i}. DELETE {ids_str}")
        lines.append(f"     Reason: {p['reason']}")

    stats = db.get_stats()
    lines.append(
        f"\nCurrent: {stats['rule_count']} rules, ~{stats['estimated_tokens']} tokens "
        f"(budget: {TOKEN_BUDGET})"
    )
    return "\n".join(lines)


@mcp.tool()
def get_stats() -> str:
    """
    Returns rule count, estimated token usage, last updated time, and all rule ids.
    Useful for deciding which rules to prune.
    """
    stats = db.get_stats()
    rules = db.list_rules()

    budget_pct = int(stats["estimated_tokens"] / TOKEN_BUDGET * 100) if TOKEN_BUDGET else 0
    lines = [
        "[PREFACE Stats]",
        f"  Rules:        {stats['rule_count']}",
        f"  Tokens:       ~{stats['estimated_tokens']} / {TOKEN_BUDGET} ({budget_pct}%)",
        f"  Last updated: {stats['last_updated']}",
        "",
    ]

    if rules:
        lines.append("All rules:")
        for rule in rules:
            snippet = rule["text"][:80] + ("..." if len(rule["text"]) > 80 else "")
            lines.append(
                f"  id={rule['id']:>3}  [{rule['category']:<8}]  "
                f"hits={rule['hit_count']:>3}  {snippet}"
            )

    return "\n".join(lines)
