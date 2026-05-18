# Preface — Self-Hosted MCP Memory Server for AI Tools

**Preface** is a lightweight, self-hosted [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that stores your personal AI preferences and injects them into every conversation automatically. Write your rules once. Every MCP-compatible AI tool reads them without you having to repeat yourself.

Works with Claude Desktop, Cursor, Cline, Windsurf, and any other MCP client.

---

## The Problem

Every time you open a new chat with Claude or Cursor, you start from zero. You re-explain that you hate em dashes, that you want TypeScript over JavaScript, that you prefer short answers. ChatGPT has memory but it's locked to ChatGPT. Cursor has its rules file but it's per-project. Nothing portable exists across tools.

Preface fixes that.

---

## How It Works

Preface runs a small server (Docker or bare Python) that exposes your personal rulebook over MCP. When any AI tool connects, it calls `get_preface` at the start of the conversation and gets your preferences back as a compact string — tone, writing style, coding conventions, whatever you've saved. No re-explaining. No per-project configs to maintain.

```
Claude Desktop ──┐
Cursor          ──┤── HTTP → Preface (MCP server) ── SQLite rulebook
Cline           ──┤
Your own agent  ──┘
```

Preface also uses an LLM to extract durable preferences from your existing conversation transcripts, so you can bootstrap your rulebook from conversations you've already had.

---

## Features

- **Persistent AI memory across tools** — one rulebook, read by Claude, Cursor, Cline, or any custom MCP client
- **Self-hosted** — your preferences live on your machine or VPS, not in a third-party cloud
- **Web dashboard** — add, edit, and delete rules from a browser; no build step, just one HTML file
- **AI-powered rule extraction** — paste a conversation transcript and Preface proposes durable rules it finds
- **AI maintenance** — run `maintain_preface` to merge, shorten, or delete redundant rules automatically
- **Token budget enforcement** — `get_preface` stays under your configured limit and surfaces low-use rules as pruning candidates
- **Multi-provider LLM support** — Claude, OpenAI, or local Ollama for extraction features
- **Docker + bare Python** — ships with a Dockerfile and `docker-compose.yml`; also runs with a single `python api.py`
- **Cloudflare Tunnel compatible** — access your preferences from anywhere, not just localhost

---

## Quick Start (Docker)

```bash
git clone https://github.com/Micmurf36/preface-mcp.git
cd preface-mcp
cp .env.example .env
# Edit .env — set PREFACE_API_KEY if you want the extract/maintain features
docker compose up -d
```

- Dashboard: `http://localhost:8080`
- MCP endpoint: `http://localhost:8080/mcp`

---

## Connecting to Claude Desktop

Edit your Claude Desktop config:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "preface": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

Restart Claude Desktop, then ask Claude to call `get_preface`. It should return your rulebook (or a prompt to add your first rule).

---

## Connecting to Cursor, Cline, or Windsurf

| Client | Where to configure |
|---|---|
| Cursor | Settings > MCP > Add server > URL: `http://localhost:8080/mcp` |
| Cline (VS Code) | MCP settings > add server with the URL |
| Windsurf | MCP settings > remote server URL |
| Any custom MCP client | Point it at `http://localhost:8080/mcp` |

Preface uses [Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http). If your client only supports the older SSE transport, check the startup logs — Preface prints which transport it detected and the correct URL.

---

## Deploying Publicly — Pick Your Path

Preface is currently tested and running on a home server via Cloudflare Tunnel. If you have a VPS with a real domain, the nginx/Caddy path works too — but that configuration is less battle-tested by this project's author. Both options are documented below.

---

### Option A: Cloudflare Tunnel (Recommended for Home Servers)

No static IP, no port forwarding, no firewall rules. Cloudflare Tunnel punches an outbound connection from your machine to Cloudflare's edge, then gives you a stable HTTPS URL. This is how this project is deployed and has been running without issues.

**You need:** A free Cloudflare account with a domain pointed at Cloudflare DNS.

**1. Install cloudflared**

```bash
# Debian/Ubuntu
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg > /dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared
```

**2. Authenticate and create a tunnel**

```bash
cloudflared tunnel login
cloudflared tunnel create preface
```

**3. Create a config file** at `~/.cloudflared/config.yml`:

```yaml
tunnel: <your-tunnel-id>
credentials-file: /home/<user>/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: preface.yourdomain.com
    service: http://localhost:8080
  - service: http_status:404
```

**4. Route DNS and start**

```bash
cloudflared tunnel route dns preface preface.yourdomain.com
cloudflared tunnel run preface
```

To run the tunnel automatically on boot:

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

**5. Update your MCP clients**

Replace `http://localhost:8080/mcp` with `https://preface.yourdomain.com/mcp` in your Claude Desktop, Cursor, or Cline config. Preface's server is written to work correctly behind Cloudflare Tunnel with no additional configuration needed.

> **Security note:** The MCP endpoint has no built-in authentication. If you expose Preface publicly, lock it down with [Cloudflare Zero Trust Access](https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/) (free tier available) or restrict it to your IP in Cloudflare's firewall rules.

---

### Option B: VPS with a Real Domain (nginx or Caddy)

If you have a VPS (DigitalOcean, Hetzner, Linode, etc.) with a domain pointed at it, you can put Preface behind a standard reverse proxy. This is the more traditional self-hosting path.

**Caddy** is the easiest option — it handles HTTPS certificates automatically:

```
# /etc/caddy/Caddyfile
preface.yourdomain.com {
    reverse_proxy localhost:8080
    basicauth * {
        youruser <bcrypt-hashed-password>
    }
}
```

**nginx** if you prefer:

```nginx
server {
    listen 443 ssl;
    server_name preface.yourdomain.com;

    # SSL via certbot: sudo certbot --nginx -d preface.yourdomain.com

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Then point your MCP clients at `https://preface.yourdomain.com/mcp`.

> **Security note:** Add HTTP basic auth to your reverse proxy. The MCP endpoint has no authentication of its own — without it, anyone who knows your URL can read and modify your rulebook.

---

## Running Without Docker

```bash
pip install -r requirements.txt
cp .env.example .env
# Set PREFACE_DB_PATH=preface.db for local development
python api.py
```

---

## MCP Tools Reference

| Tool | Description |
|---|---|
| `get_preface` | Returns your full rulebook. Call this at the start of every conversation. Tracks hit counts for pruning. |
| `add_rule` | Adds a rule. Rejects near-duplicates (85%+ similarity) with a warning. |
| `update_rule` | Edit a rule by its `id`. |
| `delete_rule` | Remove a rule by its `id`. |
| `extract_rules` | Analyzes a conversation transcript and proposes durable preference rules. Does not save automatically. |
| `maintain_preface` | Audits the rulebook with an LLM and proposes merges, rewrites, and deletions. Nothing changes automatically. |
| `get_stats` | Shows rule count, token usage, last updated, and all rule IDs. |

### Token Budget

`get_preface` enforces a configurable token limit (default: 600 tokens, roughly 30-40 concise rules). Rules that push past the budget are surfaced as pruning candidates by hit count rather than silently dropped. Tune this with `PREFACE_TOKEN_BUDGET` in your `.env`.

---

## Web Dashboard

The dashboard at `http://localhost:8080` lets you manage your rulebook from a browser:

- Add, edit, and delete rules
- Live token budget meter
- Paste a transcript and review extracted rule proposals before accepting them

No JavaScript framework, no build step. One HTML file.

---

## Extracting Rules from Existing Conversations

The fastest way to populate your rulebook is from conversations you've already had.

1. Copy a conversation from Claude.ai, ChatGPT, Cursor, or anywhere else
2. Paste it into the **Extract from Transcript** box in the dashboard (or call `extract_rules` via MCP)
3. Preface sends it to your configured LLM with a focused prompt that pulls out only durable, generalizable preferences — not one-off requests
4. Review the proposals and add the ones you want to keep

The extraction model is configured via `PREFACE_LLM_PROVIDER`. Set it to `claude`, `openai`, or `ollama`.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PREFACE_LLM_PROVIDER` | `claude` | LLM for extraction features: `claude`, `openai`, or `ollama` |
| `PREFACE_API_KEY` | — | API key for the selected provider. Not needed for Ollama. |
| `PREFACE_LLM_MODEL` | provider default | Override the model (e.g. `claude-haiku-4-5-20251001`, `gpt-4o-mini`, `llama3`) |
| `PREFACE_OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `PREFACE_PORT` | `8080` | Port Preface listens on |
| `PREFACE_DB_PATH` | `/data/preface.db` | Path to the SQLite database |
| `PREFACE_TOKEN_BUDGET` | `600` | Max tokens for `get_preface` output |

---

## Docker Persistence

Preface is stateless except for the SQLite file. The `docker-compose.yml` stores it in a named Docker volume so it survives container restarts and upgrades. You can also bind-mount to a specific host path if you prefer:

```yaml
volumes:
  - /home/youruser/preface-data:/data
```

---

## Rule Schema

Each rule has these fields:

| Field | Description |
|---|---|
| `id` | Auto-incrementing integer |
| `text` | The rule text |
| `category` | `writing`, `coding`, `tone`, or `general` |
| `created_at` | ISO 8601 timestamp |
| `hit_count` | Incremented each time `get_preface` runs — useful for spotting unused rules |
| `source` | `manual` (added directly) or `extracted` (proposed by `extract_rules` and accepted) |

---

## Contributing

Preface is intentionally small — under 500 lines of Python — so it stays readable and forkable. Good contributions:

- Additional LLM providers in `extract.py`
- Rule import/export (JSON)
- Optional authentication for the API
- Better token estimation (tiktoken as an optional dep)
- MCP resource support for streaming the rulebook

To contribute:
1. Fork the repo
2. Create a branch: `git checkout -b my-feature`
3. Make your changes — keep each file focused
4. Open a PR with a clear description of what and why

Please keep the spirit of the project: self-hostable, minimal dependencies, readable code.

---

## License

MIT
