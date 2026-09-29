---
project: "universal-chat-agent"
description: "Orientation map for the brain — a bridge-agnostic conversational agent exposed over HTTP, reusable behind any chat front-end."
created: "2026-09-09"
last_full_scan: "2026-09-09"
---

# Orientation Map — universal-chat-agent (the brain)

Index of orientation artifacts in this repo. Used by agents at awakening and refreshed at
wrap-up via the `/map-orientation` skill. Entry paths are relative to this repo's root.

This repo stands alone: it is vendored as a submodule by the telegent stack, but nothing here
depends on that parent, and this map deliberately does not reference it. The brain is
front-end agnostic by design — Telegram, WhatsApp, web or CLI all speak the same
`{conversation_id, message} → {reply}` contract.

**Where the decisions live:** no `docs/adr/` folder — the ADRs sit in the *What Decisions
Were Made?* section of `README.md`.

## Status Legend

- **useful** — current, accurate, future tasks will rely on it.
- **stale-but-valuable** — could be useful if updated. Repair when a task hits its scope.
- **obsolete** — neither current nor valuable. Ignore.
- **unverified** — never verified, or mtime changed since `last_verified`.

## Scope Legend

Single-role repo: every entry is `shared` with empty `roles`, so the role filter is a no-op.

---

## Entries

### `README.md`

- **type**: 7q-readme
- **scope**: shared
- **roles**: []
- **status**: useful
- **tags**: [overview, entry-point, brain, http, memory, agents, toolsets, extension-points]
- **last_verified**: "2026-09-29"
- **verified_by**: "software-architect / 2026-09-29 platform-free brain session"
- **update_trigger**: ""
- **notes**: "Root README. The brain is a bridge-agnostic conversational agent over HTTP: any chat front-end sends a message tagged with a conversation_id and gets a reply. The brain owns the model, the per-conversation memory, and the agent runtime. Carries this repo's ADRs. Updated 2026-09-29: the brain ships NO integration of its own — `TOOLSET_SOURCES` / `LINK_PROVIDERS` name where each toolset and link provider comes from, the reference adapters are role-named (`llm/`, `oidc/`, `memory_service/`), and the env vars are `LLM_*` / `OIDC_ISSUER` / `MEMORY_SERVICE_*`."

### `application/common/README.md`

- **type**: other
- **scope**: shared
- **roles**: []
- **status**: useful
- **tags**: [a-boxed, placeholder, layer, helpers]
- **last_verified**: "2026-09-29"
- **verified_by**: "software-architect / 2026-09-29 platform-free brain session"
- **update_trigger**: ""
- **notes**: "No longer empty: `loading.py` turns a config-named `module:attribute` path into the object it names, shared by the two extension points (toolset sources and link providers). Still the layer for pure, stateless helpers; the brain's actual domain rules remain in `business_domain` because they are domain-specific rather than generic."

---

## How to Use This File

Load this map alongside the repo. Every entry is `shared`, so no filtering applies. This map
is self-contained — a clone of just this repository is fully served by what is here.
