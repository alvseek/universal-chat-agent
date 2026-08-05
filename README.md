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
| `SYSTEM_PROMPT` | The agent's persona | `You are a helpful...` |
| `HOST` / `PORT` | Where the server listens | `0.0.0.0` / `8000` |

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
| POST | `/chat` | `{conversation_id, message}` → `{reply}` |

```sh
curl -s http://localhost:8000/chat -H 'content-type: application/json' \
  -d '{"conversation_id":"cli:me","message":"Hi, my name is Alvi"}'
# {"reply":"Hello Alvi! ..."}

curl -s http://localhost:8000/chat -H 'content-type: application/json' \
  -d '{"conversation_id":"cli:me","message":"What is my name?"}'
# {"reply":"Your name is Alvi!"}   ← memory, keyed by conversation_id
```

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

### Data Model

One table, keyed by an opaque `conversation_id` so any bridge namespaces cleanly:

```
messages(id, conversation_id, role, content, ts)
  index (conversation_id, id)   — role ∈ {user, assistant}
```

### External Integrations

| Service | Purpose | Protocol | Timeout |
|---------|---------|----------|---------|
| OpenRouter | The LLM (OpenAI-compatible) | HTTPS | provider default |

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
