"""
LLM extraction and maintenance logic for Brief.

Two public functions:
  extract_from_transcript(transcript)  — find durable preferences in a conversation
  maintain_rules(rules)                — audit existing rules for verbosity/redundancy

Configure via environment variables:
  BRIEF_LLM_PROVIDER  — claude | openai | ollama  (default: claude)
  BRIEF_API_KEY       — API key (not needed for ollama)
  BRIEF_LLM_MODEL     — override the default model for the provider
  BRIEF_OLLAMA_URL    — Ollama server URL (default: http://localhost:11434)
"""

import os
import json

VALID_CATEGORIES = {"writing", "coding", "tone", "general"}

# ── System prompts ─────────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You extract durable AI behavior preferences from conversation transcripts.

Find rules the user would want applied to FUTURE conversations too:
- Writing style ("no em dashes", "short paragraphs")
- Coding preferences ("always TypeScript", "no comments unless the why is non-obvious")
- Tone ("be direct", "skip preamble and recap")
- General style ("explain tradeoffs", "prefer simple over clever")

Do NOT extract:
- One-off task requests ("fix this bug", "make this shorter")
- Facts about today, the current project, or specific files
- Instructions that only apply right now

Return a JSON array only. Each item: {"text": "...", "category": "writing|coding|tone|general"}
Return [] if nothing durable found. No explanation outside the JSON."""

MAINTENANCE_PROMPT = """You are a personal AI rulebook editor. Your job: make the rules shorter, clearer, and non-redundant.

Given a JSON list of rules (id, text, category, hit_count), propose improvements.

Three proposal types:
1. "merge"  — two+ rules that overlap; combine into one tighter rule
2. "reword" — a verbose rule; propose a shorter version (target: ≤ 10 words)
3. "delete" — a rule that is redundant, covered elsewhere, or too project-specific

Be aggressive about brevity. Example rewrites:
  "Always prefer TypeScript over JavaScript when both are available" → "Prefer TypeScript over JavaScript"
  "Keep paragraphs short and avoid run-on sentences in all writing" → "Short paragraphs, no run-ons"

Return a JSON array. Each item:
{
  "action": "merge" | "reword" | "delete",
  "rule_ids": [<id>, ...],
  "new_text": "<proposed text>",      // omit for "delete"
  "new_category": "<category>",       // omit for "delete"
  "reason": "<one sentence>"
}

Return [] if the rulebook is already tight.
JSON only — no explanation outside the array."""


# ── Generic LLM dispatch ───────────────────────────────────────────────────────

async def _call_provider(system_prompt: str, user_message: str) -> str:
    """Send a message to the configured LLM and return the raw text response."""
    provider = os.environ.get("BRIEF_LLM_PROVIDER", "claude").lower()
    api_key = os.environ.get("BRIEF_API_KEY", "")

    if provider == "claude":
        return await _claude(system_prompt, user_message, api_key)
    elif provider == "openai":
        return await _openai(system_prompt, user_message, api_key)
    elif provider == "ollama":
        return await _ollama(system_prompt, user_message)
    else:
        raise ValueError(
            f"Unknown provider '{provider}'. "
            "Set BRIEF_LLM_PROVIDER to: claude, openai, or ollama"
        )


async def _claude(system: str, user: str, api_key: str) -> str:
    import anthropic
    model = os.environ.get("BRIEF_LLM_MODEL", "claude-haiku-4-5-20251001")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model, max_tokens=1024, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


async def _openai(system: str, user: str, api_key: str) -> str:
    import openai
    model = os.environ.get("BRIEF_LLM_MODEL", "gpt-4o-mini")
    client = openai.AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model=model, max_tokens=1024,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content


async def _ollama(system: str, user: str) -> str:
    import httpx
    url = os.environ.get("BRIEF_OLLAMA_URL", "http://localhost:11434")
    model = os.environ.get("BRIEF_LLM_MODEL", "llama3")
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{url}/api/chat", json={
            "model": model, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        resp.raise_for_status()
        return resp.json()["message"]["content"]


# ── JSON parsing helpers ───────────────────────────────────────────────────────

def _parse_json_array(text: str) -> list:
    """Find and parse the first JSON array in an LLM response."""
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        return json.loads(text[start: end + 1])
    except json.JSONDecodeError:
        return []


# ── Public functions ───────────────────────────────────────────────────────────

async def extract_from_transcript(transcript: str) -> list[dict]:
    """Find durable AI behavior preferences in a conversation transcript."""
    raw = await _call_provider(EXTRACTION_PROMPT, f"Transcript:\n\n{transcript}")
    result = []
    for item in _parse_json_array(raw):
        if not isinstance(item, dict) or "text" not in item:
            continue
        cat = item.get("category", "general")
        if cat not in VALID_CATEGORIES:
            cat = "general"
        result.append({"text": str(item["text"]).strip(), "category": cat})
    return result


async def maintain_rules(rules: list[dict]) -> list[dict]:
    """
    Audit the current rulebook and propose merges, rewrites, and deletions.
    Returns structured proposals — nothing is changed automatically.
    """
    rules_json = json.dumps(
        [{"id": r["id"], "text": r["text"], "category": r["category"], "hit_count": r["hit_count"]}
         for r in rules],
        indent=2,
    )
    raw = await _call_provider(MAINTENANCE_PROMPT, f"Rules:\n{rules_json}")

    proposals = []
    for item in _parse_json_array(raw):
        action = item.get("action", "")
        if action not in ("merge", "reword", "delete"):
            continue
        ids = item.get("rule_ids", [])
        if not isinstance(ids, list) or not ids:
            continue
        proposal: dict = {"action": action, "rule_ids": ids, "reason": item.get("reason", "")}
        if action != "delete":
            cat = item.get("new_category", "general") or "general"
            if cat not in VALID_CATEGORIES:
                cat = "general"
            proposal["new_text"] = str(item.get("new_text", "")).strip()
            proposal["new_category"] = cat
        proposals.append(proposal)
    return proposals
