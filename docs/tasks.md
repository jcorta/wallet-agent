# wallet-agent — Tasks

Checklist derived from [plan.md](plan.md). A stage is done when every item is checked, lint and tests pass, the stack runs, and its commit is made.

## Stage 0 — Scaffold

- [x] Directory structure for `idp`, `wallet-api`, `agent-service`, `frontend`
- [x] `docker-compose.yml` with `pgvector/pgvector` Postgres, healthcheck, init script creating schemas and a dedicated role per schema
- [x] `.env.example` with every variable documented
- [x] `.gitignore` covering `.env`, virtualenvs and caches
- [x] MIT `LICENSE`
- [x] README skeleton (what it is, architecture placeholder, quickstart placeholder)
- [x] GitHub Actions workflow skeleton (matrix over services: ruff check, ruff format --check, pytest)
- [x] Verify: `docker compose up postgres` becomes healthy; schemas exist
- [x] Commit: `chore: scaffold monorepo`

## Stage 1 — idp

- [x] `pyproject.toml`, pinned dependencies, ruff config
- [x] Settings via `pydantic-settings` (issuer, audience, users, key path)
- [x] RSA key generation on first start, persisted to the configured path
- [x] `POST /login` returning RS256 JWT (`sub`, `iss`, `aud`, `exp` = 15 min)
- [x] `GET /.well-known/jwks.json`
- [x] `GET /health`
- [x] Request-id middleware, JSON logging, uniform error body
- [x] Tests: valid login, wrong password, token verifies against JWKS, expiry claim
- [x] Dockerfile (slim, non-root, healthcheck); compose entry
- [x] Verify with real requests against the container
- [x] Commit: `feat(idp): add login endpoint and JWKS`

## Stage 2 — wallet-api

- [x] Project setup, settings, logging, error handling, request-id middleware
- [x] JWT validation dependency (JWKS fetch + cache, refetch on unknown `kid`, `iss`/`aud`/`exp` checks)
- [x] SQLAlchemy models and table creation for `accounts`, `contacts`, `transfers`
- [x] Idempotent seed data for both users (ids match idp config)
- [x] `GET /accounts/me/balance`
- [x] `GET /accounts/me/transactions`
- [x] `GET /accounts/me/contacts`
- [x] `POST /transfers` with ordered controls, row locks, stable lock order, atomic debit/credit
- [x] Rejected attempts recorded with reason; idempotent replay of both outcomes
- [x] `GET /health`
- [x] Tests: insufficient funds, over daily limit, foreign contact, non-positive amount, idempotent retry, same key different payload (409), token of another user, missing/expired/bad-signature token, concurrent transfers
- [x] Dockerfile and compose entry (`depends_on` idp and postgres healthy)
- [x] Verify end to end with real tokens from the idp container
- [x] Commit: `feat(wallet-api): add account endpoints and controlled transfers`

## Stage 3 — agent-service (minimal)

- [ ] Project setup; check current LangChain/LangGraph docs; pin versions
- [ ] Settings including `LLM_PROVIDER`, `LLM_MODEL`
- [ ] Chat model built only through `init_chat_model`
- [ ] Wallet client (httpx, timeouts, request-id propagation, token forwarded from config)
- [ ] `get_balance` tool reading the token from `RunnableConfig`
- [ ] Graph: `agent` ↔ `read_tools`, Spanish system prompt
- [ ] Console client in `scripts/` (login, chat loop)
- [ ] Tests with a fake chat model: tool routing, token not present in messages or state
- [ ] Dockerfile and compose entry
- [ ] Verify with the configured model through the console client
- [ ] Commit: `feat(agent): add minimal agent with balance tool`

## Stage 4 — Transfers, confirmation, ownership

- [ ] `get_transactions`, `list_contacts` tools
- [ ] `request_transfer` tool (no execution), Pydantic-validated arguments
- [ ] `AgentState` with `pending_transfer` and `approved`
- [ ] Nodes `prepare_transfer`, `confirm`, `execute_transfer`, `cancel_transfer`
- [ ] Routing function; every tool call answered exactly once
- [ ] Idempotency key generated in `prepare_transfer`
- [ ] Postgres checkpointer in schema `agent` (verify `search_path` behavior)
- [ ] `agent.threads` ownership table
- [ ] `POST /chat` and `POST /chat/{thread_id}/confirm` with JWT validation, ownership check, `409` cases
- [ ] Console client: y/n confirmation
- [ ] Tests (fake model, stubbed wallet-api): no execution without approval, decline path, wallet rejection surfaced, ambiguous/unknown contact, foreign thread returns 404, confirm without pending interrupt returns 409, resume after checkpointer reload
- [ ] Verify: restart `agent-service` mid-confirmation, then confirm
- [ ] Commit: `feat(agent): add transfer flow with human approval`

## Stage 5 — Frontend

- [ ] Streamlit app: login, chat, Confirm/Cancel on `pending_confirmation`, logout
- [ ] Handle `401` by returning to login
- [ ] Dockerfile and compose entry
- [ ] Verify the full flow in a browser
- [ ] Commit: `feat(frontend): add Streamlit chat client`

## Stage 6 — Observability and evals

- [ ] LangSmith enabled only through environment variables; verify startup without them
- [ ] Eval harness: login, reset/inspect wallet state, call the agent, retry with backoff on rate limits
- [ ] Cases: no transfer without confirmation; balance matches wallet-api; other-user data refused; prompt injection; over-limit rejection reported; unknown contact; ambiguous contact
- [ ] Additional cases to reach 10–15 (decline path, repeated confirm, non-positive amount, thread of another user)
- [ ] Assertions against wallet-api state where possible
- [ ] Document how to run subsets and how to compare providers
- [ ] Run with Gemini; run with Anthropic if a key is available; record results
- [ ] Commit: `feat(agent): add tracing and evaluation suite`

## Stage 7 — Retrieval

- [ ] 2–3 markdown documents in `agent-service/knowledge/`
- [ ] Embeddings through `init_embeddings`; pgvector table in schema `agent`
- [ ] Idempotent indexing at startup
- [ ] `search_knowledge` tool wired into `read_tools`
- [ ] 2–3 FAQ eval cases
- [ ] Commit: `feat(agent): add knowledge retrieval`

## Stage 8 — Documentation

- [ ] README: overview, Mermaid architecture diagram, design decisions (agent does not touch the database; RS256 + JWKS; token outside LLM context; interrupt in graph, not in prompt; idempotency; production changes)
- [ ] README: quickstart, tests, evals (manual), switching models, known limitations, next steps
- [ ] `.env.example` re-checked against code
- [ ] CI green on a clean clone
- [ ] Full acceptance run against spec section 8
- [ ] Commit: `docs: finalize README`

## Open decisions

- **Self transfers** are rejected with `self_transfer` (not in the original control list).
- **`transfers` has an extra `contact_id` column** so idempotent replays can compare the original request, and so rejected attempts for unknown contacts are still auditable.
- **Wallet-api tests need Postgres** (row locks and concurrency are part of what is tested). They read `TEST_DATABASE_URL`; each run uses a throwaway schema. Compose publishes Postgres on `127.0.0.1:5433` for this purpose.
- **Seed data:** besides the two users, three external accounts exist as transfer destinations (`external:*` user ids). Alice has two contacts named Juan to exercise ambiguity.
