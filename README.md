# universal-chat-agent

The **brain**: a bridge-agnostic conversational agent exposed over HTTP. Any chat
front-end (Telegram, WhatsApp, web, CLI) sends it a message tagged with a
`conversation_id` and gets a reply back. The brain owns the model, the
per-conversation memory, and (later) tools/skills/knowledge.

> Paired bridge: [telegent](../telegent) (Telegram ↔ this brain). Build a new
> bridge for any other platform against the same HTTP contract.

---

## Q1 — What is this?

A small HTTP service that turns `(conversation_id, message)` into a `reply`,
remembering each conversation. It is deliberately **transport-agnostic**: it
knows nothing about Telegram, WhatsApp, or any specific platform — only the
`conversation_id` string a bridge gives it (e.g. `telegram:12345`).

Built on **pydantic-ai** over an **OpenRouter** (OpenAI-compatible) model, with
**SQLite** conversation memory. Structured with the **A-Boxed Level 1** pattern
(flat semantic-prefix layers).

## Q2 — How to set up?

```bash
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then fill OPENROUTER_API_KEY (+ model if you like)
```

## Q3 — How to use?

Run the brain:

```bash
uvicorn application.main:app --host 0.0.0.0 --port 8000
# or: python -m application.main
```

Call it:

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}

curl -s http://localhost:8000/chat \
  -H 'content-type: application/json' \
  -d '{"conversation_id":"cli:me","message":"Hi, my name is Alvi"}'
# {"reply":"Hello Alvi! ..."}

curl -s http://localhost:8000/chat \
  -H 'content-type: application/json' \
  -d '{"conversation_id":"cli:me","message":"What is my name?"}'
# {"reply":"Your name is Alvi!"}   ← memory, keyed by conversation_id
```

## Q4 — How it works?

```
HTTP POST /chat
   │
   ▼
api_controllers/chat_controller  ──uses──▶  api_dto (ChatRequest / ChatResponse)
   │
   ▼
business_services/chat_service
   ├─ business_domain/conversation_domain   (pure: validate id, window history)
   ├─ data_repositories/message_repository   (SQLite: recent + append)
   │      └─ data_entities/message_entity     (schema + Message)
   └─ api_integrations/openrouter/llm_client  (pydantic-ai → OpenRouter)
```

The **contract** (stable across all bridges):

| | |
|---|---|
| `POST /chat` request | `{ "conversation_id": "<platform>:<id>", "message": "<text>" }` |
| `POST /chat` response | `{ "reply": "<text>" }` |
| `GET /health` | `{ "status": "ok" }` |

Memory is isolated per `conversation_id`, so every bridge/user is independent
and survives restarts (SQLite file).

## Q5 — How deployed?

```bash
docker compose up --build      # serves on :8000, memory in a named volume
```

## Q6 — What decisions?

- **HTTP transport (not in-process/gRPC)**: lets any bridge in any language reuse
  the brain with full process + dependency isolation. The localhost hop is
  sub-millisecond and dwarfed by the LLM call, so there is no meaningful
  overhead. Transport sits behind a thin client on the bridge side, so a future
  swap (e.g. gRPC for streaming) stays localized.
- **A-Boxed L1, full skeleton**: semantic-prefix layers with placeholder READMEs
  where a layer is not used yet, so growth is additive.
- **Python folder naming**: underscores (`api_controllers`) not hyphens, because
  Python can't import hyphenated packages — the semantic prefix is preserved.

## Q7 — What's broken / TODO?

- Live end-to-end verified via the paired bridge; standalone load-testing TODO.
- No auth on `/chat` yet (assumes trusted local network / same host). Add an
  API key or network policy before exposing publicly.
- Tools/skills/knowledge (RAG) and the agent-memory bridge are future increments.
