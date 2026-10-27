# wallet-agent

Reference implementation of a conversational wallet assistant whose sensitive actions are gated by code, not prompts. See `docs/spec.md` (what), `docs/plan.md` (how) and `docs/tasks.md` (progress). Keep `docs/tasks.md` up to date as work proceeds.

## Layout

- `idp/` — toy login + JWKS (FastAPI)
- `wallet-api/` — accounts, contacts, transfers; owns all controls (FastAPI, SQLAlchemy 2)
- `agent-service/` — LangGraph agent (FastAPI, psycopg); `evals/`, `knowledge/`, `scripts/`
- `frontend/` — Streamlit client
- Postgres with schemas `wallet` and `agent`

Each service has its own `pyproject.toml`, `uv.lock` and `Dockerfile`. No code is shared between services; duplicate small things (JWT validation, error body, logging setup) instead.

## Non-negotiable invariants

1. agent-service never connects to the `wallet` schema; it calls wallet-api over HTTP.
2. The LLM never sees or decides user identity. The user id comes from the JWT `sub`; the token travels in LangGraph `config["configurable"]`, never in messages or graph state. No tool parameter may carry a user id.
3. `execute_transfer` is reachable only through `confirm` (the `interrupt()` node). Do not add edges that bypass it.
4. wallet-api derives the user from `sub` only, never from body, path or query.
5. A chat thread is usable only by its owner; foreign threads return `404`.
6. No provider class is imported directly. Models come from `init_chat_model` / `init_embeddings` driven by environment variables.
7. No secrets in the repo. `.env` is git-ignored; keep `.env.example` complete.

## Conventions

- Python 3.12, `uv`. Type hints everywhere. Pydantic v2 models for every request and response. Configuration via `pydantic-settings`.
- `ruff check` and `ruff format` must pass in every service before committing.
- Errors: uniform body `{"error": {"code", "message", "request_id"}}`; never return stack traces; every inter-service call has a timeout.
- Logging: structured JSON; `X-Request-ID` generated if absent, propagated across services; never log tokens or passwords.
- Money: `Decimal` in code, `NUMERIC(18,2)` in the database, strings in JSON.
- Keep code simple. No speculative abstractions. Comments only for design rationale, never to narrate what a line does.
- LangChain/LangGraph APIs change often: check the current documentation before using an API and do not guess signatures. Pin versions.
- Dockerfiles: slim base, non-root user, `HEALTHCHECK`. Compose uses `depends_on: condition: service_healthy`.
- All repository content (code, comments, docs, commits) is in English. The assistant replies to end users in Spanish.

## Commands

Run from a service directory unless noted.

```
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

From the repo root: `docker compose up --build`. Evals (`agent-service/evals`) need the running stack and a model API key; run them manually, never in CI.

## Workflow

- Work in stages as listed in `docs/tasks.md`. One conventional-commit per stage (`feat(scope):`, `chore:`, `docs:`, `fix:`).
- Do not start a stage on top of a broken one. Verify with real requests against running containers, not only unit tests.
- When a decision is open, take the simplest option that satisfies the spec and record it under "Open decisions" in `docs/tasks.md`.
- Never commit `.env`, keys or tokens.
