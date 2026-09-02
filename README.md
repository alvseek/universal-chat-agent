---
doc_type: 7q-readme
---

# universal-chat-agent

## Table of Contents

- [What Is This?](#what-is-this)
- [How Do I Set It Up?](#how-do-i-set-it-up)
- [How Do I Use It?](#how-do-i-use-it)
- [How Does It Work Inside?](#how-does-it-work-inside)
- [How Is It Deployed?](#how-is-it-deployed)
- [What Decisions Were Made?](#what-decisions-were-made)
- [What's Broken / Known Debts?](#whats-broken--known-debts)

---

## What Is This?

The **brain**: a bridge-agnostic conversational agent exposed over HTTP. Any chat front-end
(Telegram, WhatsApp, web, CLI) sends it a message tagged with a `conversation_id` and gets
a reply back. The brain owns the model, the per-conversation memory, and (later)
tools/skills/knowledge. It knows nothing about any specific platform — only the
`conversation_id` string a bridge gives it (e.g. `telegram:12345`).

For **anyone building chat front-ends** who wants one reusable agent behind all of them.
Paired bridge: [telegent](../telegent) (Telegram). Build a new bridge for any platform
against the same HTTP contract.

### Architecture

Follows the **A-Boxed Level 1** pattern (flat semantic-prefix layers, full skeleton with
placeholder READMEs where a layer is unused).

```
HTTP POST /chat
   │
   ▼
api_controllers/chat_controller  ──uses──▶  api_dto (ChatRequest / ChatResponse)
   │
   ▼
business_services/chat_service
   ├─ business_domain/conversation_domain    (pure: validate id, window history)
   ├─ data_repositories/message_repository    (SQLite: recent + append)
   │      └─ data_entities/message_entity      (schema + Message)
   └─ api_integrations/openrouter/llm_client   (pydantic-ai → OpenRouter)
```

Memory is isolated per `conversation_id`, so every bridge/user is independent and survives
restarts (SQLite file).

### Tech Stack

- **Runtime**: Python 3.12+
- **Web**: FastAPI + uvicorn (async, matches the async agent)
- **Model**: pydantic-ai over an OpenRouter (OpenAI-compatible) model
- **Database**: SQLite (conversation memory)

---

## How Do I Set It Up?

### Prerequisites

- Python 3.12+ (`python --version`)
- An OpenRouter API key ([openrouter.ai/keys](https://openrouter.ai/keys))

### Setup

1. Clone and install:
   ```sh
   git clone <repo-url>
   cd universal-chat-agent
   python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Configure environment:
   ```sh
   cp .env.example .env
   # Fill OPENROUTER_API_KEY (and the model, if you like)
   ```

3. Start:
   ```sh
   uvicorn application.main:app --host 0.0.0.0 --port 8000
   # or: python -m application.main
   ```

4. Verify it works:
   ```sh
   curl -s http://localhost:8000/health
   # Expected: {"status":"ok"}
   ```

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `OPENROUTER_API_KEY` | OpenRouter API key (required) | `sk-or-...` |
| `OPENROUTER_MODEL` | Model id on OpenRouter (required) | `deepseek/deepseek-chat` |
| `OPENROUTER_BASE_URL` | OpenAI-compatible endpoint | `https://openrouter.ai/api/v1` |
| `MEMORY_WINDOW` | Recent turns remembered per conversation | `15` |
| `DB_PATH` | SQLite memory file | `agent.db` |
| `SYSTEM_PROMPT` | The default agent's persona (used when a request names no `agent_id`) | `You are a helpful...` |
| `HOST` / `PORT` | Where the server listens | `0.0.0.0` / `8000` |

**Memory service (optional)** — set `MUNNIN_URL` and the brain can *become* any agent that service holds (see [Agents from a memory service](#agents-from-a-memory-service)):

| Variable | Description | Example |
|----------|-------------|---------|
| `MUNNIN_URL` | The memory service; empty = single default agent | `https://munnin.example` |
| `MUNNIN_RESOURCE` | API resource the token is bound to (default `<MUNNIN_URL>/mcp`) | `https://munnin.example/mcp` |
| `MUNNIN_M2M_CLIENT_ID` / `MUNNIN_M2M_CLIENT_SECRET` | This brain's machine credential at the identity provider | — |
| `MUNNIN_M2M_SCOPE` | Scope requested with the token (optional) | `memory:read` |
| `AUTHENTRA_ISSUER` | OIDC issuer; token endpoint is `<issuer>/token` | `https://auth.example/oidc` |
| `AGENT_CACHE_TTL_SECONDS` | How long a built agent stays warm | `28800` |
| `AWAKENING_LAYERS` / `AWAKENING_EXCLUDE` | Which awakening layers become the prompt (empty = all, canonical order) | `identity,shared.reasoning` |

**Toolsets (optional; needs the memory service)** — bind named toolsets to agents so an
agent can *act*, not just answer (see [Agents from a memory service](#agents-from-a-memory-service)):

| Variable | Description | Example |
|----------|-------------|---------|
| `AGENT_TOOLSETS` | Comma-separated `agent=toolset` pairs; empty = no agent has tools | `invintiry-operator=invintiry` |
| `INVINTIRY_API_URL` | The Invintiry API the `invintiry` toolset calls | `https://api.invintiry.example` |
| `INVINTIRY_AGENT_TOKEN` | RS256 agent token minted by the workspace OWNER (shown once at mint) | — |

---

## How Do I Use It?

### Commands

| Command | Description |
|---------|-------------|
| `uvicorn application.main:app --port 8000` | Start the server |
| `python -m pytest tests/ -q` | Run model-free tests |

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Liveness check → `{"status":"ok"}` |
| POST | `/chat` | `{conversation_id, message, agent_id?}` → `{reply}` — `agent_id` absent: the default agent answers; present: that agent is awakened from the memory service and answers as itself (404 if it does not exist, 400 if no memory service is configured, 503 if it cannot be reached) |
| POST | `/agents/{agent_id}/reload` | Rebuild one agent from the memory service now, ahead of its cache TTL → `{agent_id, reloaded: true}` |

```sh
curl -s http://localhost:8000/chat -H 'content-type: application/json' \
  -d '{"conversation_id":"cli:me","message":"Hi, my name is Alvi"}'
# {"reply":"Hello Alvi! ..."}

curl -s http://localhost:8000/chat -H 'content-type: application/json' \
  -d '{"conversation_id":"cli:me","message":"What is my name?"}'
# {"reply":"Your name is Alvi!"}   ← memory, keyed by conversation_id

# with a memory service configured: answer as a named agent
curl -s http://localhost:8000/chat -H 'content-type: application/json' \
  -d '{"conversation_id":"cli:me","message":"who are you?","agent_id":"invintiry-operator"}'
# {"reply":"I'm your Inventory Operator ..."}   ← memory keyed invintiry-operator:cli:me
```

Errors are always `{"error": "<message>"}` with the status code carrying the kind: 400 the caller can fix, 404 unknown agent, 503 memory service or identity provider unavailable, 500 a bug here.

---

## How Does It Work Inside?

### Core Flow: POST /chat → reply

1. **Receive** (`application/api_controllers/chat_controller.py`)
   - FastAPI validates the body against `ChatRequest` (`api_dto`).
2. **Orchestrate** (`application/business_services/chat_service.py`)
   - Validate + normalize the `conversation_id` (`business_domain`), load the recent window
     from the repository, run the LLM, persist the user + assistant turns.
3. **Generate** (`application/api_integrations/openrouter/llm_client.py`)
   - pydantic-ai runs the model over the message + history, returns the reply text.

### Agents from a memory service

With `MUNNIN_URL` set the brain is a runtime for agents that live as **data** in a memory
service, not as code here. A request naming `agent_id` goes through:

1. **Registry** (`business_services/agent_registry.py`) — is that agent warm? If so, use it.
   Otherwise load it (one load per agent even under concurrent requests), keep it for
   `AGENT_CACHE_TTL_SECONDS`, then refresh. A refresh that fails keeps serving the cached
   copy with a warning; an agent the service no longer holds is dropped and answered 404.
2. **Awaken** (`api_integrations/munnin/munnin_client.py`) — `GET /api/awaken?agent_id=…`
   with a bearer from `api_integrations/authentra/token_provider.py` (OAuth
   `client_credentials`, cached until shortly before expiry).
3. **Render** (`business_domain/awakening_domain.py`) — the payload's layers become the
   system prompt **by shape, never by name**: every layer the service sends is rendered
   (whole records as sections, index entries as one-liners), in a canonical order with
   unknown layers appended, so a new layer needs no code here. Narrowing is configuration
   (`AWAKENING_LAYERS` / `AWAKENING_EXCLUDE`), never a hidden default.
4. **Build** — a pydantic-ai `Agent` with that prompt and the agent's bound toolsets
   (below), then the normal chat flow, with history keyed `agent_id:conversation_id` so
   two agents sharing one brain never share a conversation.

### Toolsets: what an agent may *do*

Tools are code; which agent holds them is configuration. `business_services/toolsets/`
is a registry of named toolsets (today: `invintiry` — find/get/create/move over the
Invintiry API via `api_integrations/invintiry/`), and `AGENT_TOOLSETS` binds them to
agents. Three properties are structural, not stylistic:

- **The prompt lists only real tools.** Every agent's system prompt ends with an
  *Available Tools* section derived from the toolsets actually bound — `none`, with an
  instruction to say so, when unbound — so an agent's identity text can never claim a
  capability the runtime doesn't hold.
- **Writes cannot run on the model's say-so.** Write tools are registered with
  `requires_approval`, so a write call *pauses* the run (pydantic-ai deferred tools):
  the paused state is parked in `pending_approvals` (same SQLite file), the user gets a
  confirmation line restating the exact call, and only a clear "yes" as the next message
  resumes and executes it. Anything else denies the call and answers the message
  normally. The pending row is deleted before resuming, so a crash loses the write
  rather than doubling it.
- **API failures are answers, not errors.** The tools return `{"error": …}` data
  (`unreachable`, `auth_failed`, `ambiguous` with candidates, contract failures like
  `insufficient_stock`) for the model to phrase; a tool never raises a stack trace into
  the chat.

### Data Model

One table, keyed by an opaque `conversation_id` so any bridge namespaces cleanly — and
by `agent_id:conversation_id` when a request names an agent:

```
messages(id, conversation_id, role, content, ts)
  index (conversation_id, id)   — role ∈ {user, assistant}
```

### External Integrations

| Service | Purpose | Protocol | Timeout |
|---------|---------|----------|---------|
| OpenRouter | The LLM (OpenAI-compatible) | HTTPS | provider default |
| Memory service (optional, `MUNNIN_URL`) | An agent's awakening, by `agent_id` | HTTPS, bearer | 30 s |
| Identity provider (optional, `AUTHENTRA_ISSUER`) | The machine credential for the memory service | HTTPS, `client_credentials` | 30 s |

---

## How Is It Deployed?

### Environments

| Environment | URL | Branch | Auto-deploy |
|-------------|-----|--------|-------------|
| Local | `http://localhost:8000` | `main` | No |

### Docker

```sh
docker compose up --build      # serves on :8000, memory in a named volume
```

### CI/CD

[NOT FOUND: no CI/CD pipeline configured yet.]

---

## What Decisions Were Made?

### ADR-001: HTTP service, not in-process or gRPC (2026-08-05)

**Context**: The brain must be reusable by any bridge, with dependency + failure isolation.
**Decision**: Expose it as an HTTP service (`POST /chat`); bridges are thin clients.
**Trade-off**: A network hop and a second process — but the localhost hop measured ~0.85 ms
vs a ~4.2 s LLM call (0.02%), so it's free in practice. gRPC/streaming can be added later
behind the same client boundary if ever needed.

### ADR-002: conversation_id is the contract (2026-08-05)

**Context**: How does the brain stay platform-agnostic while isolating each chat's memory?
**Decision**: The bridge supplies an opaque `conversation_id` (e.g. `telegram:12345`); the
brain keys all memory on it.
**Trade-off**: The brain trusts the bridge to namespace correctly — accepted, since it keeps
the brain free of any platform knowledge.

### ADR-003: A-Boxed L1, full skeleton (2026-08-05)

**Context**: The codebase is small but expected to grow (tools, RAG, more endpoints).
**Decision**: Apply the full A-Boxed L1 layer structure now, with placeholder READMEs where a
layer is unused, so growth is additive. Python uses underscore folders (`api_controllers`)
because hyphens break imports — the semantic prefix is preserved.
**Trade-off**: Some near-empty folders today — accepted for a consistent, additive growth path.

---

## What's Broken / Known Debts?

### High Priority

- **No auth on `/chat`.** *Why*: M2 assumes a trusted local network / same host. Add an API
  key or network policy before exposing publicly.

### Medium Priority

- **Live-verified only via the paired bridge + manual curl** (memory recall + isolation +
  latency). *Why*: M2 scope — no load/soak testing yet.

### Known Limitations

- Single SQLite connection per process (fine for one process; not built for multi-writer).
- No tools/skills/knowledge (RAG) yet, and no agent-memory bridge — those are future
  increments.

---

## License

MIT — see [LICENSE](LICENSE).
