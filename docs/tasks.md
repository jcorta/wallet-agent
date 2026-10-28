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

- [x] Project setup; check current LangChain/LangGraph docs; pin versions
- [x] Settings including `LLM_PROVIDER`, `LLM_MODEL`
- [x] Chat model built only through `init_chat_model`
- [x] Wallet client (httpx, timeouts, request-id propagation, token forwarded from config)
- [x] `get_balance` tool reading the token from `RunnableConfig`
- [x] Graph: `agent` ↔ `read_tools`, Spanish system prompt
- [x] Console client in `scripts/` (login, chat loop)
- [x] Tests with a fake chat model: tool routing, token not present in messages or state
- [x] Dockerfile and compose entry
- [x] Verify with the configured model through the console client
- [x] Commit: `feat(agent): add minimal agent with balance tool`

## Stage 4 — Transfers, confirmation, ownership

- [x] `get_transactions`, `list_contacts` tools
- [x] `request_transfer` tool (no execution), Pydantic-validated arguments
- [x] `AgentState` with `pending_transfer` and `approved`
- [x] Nodes `prepare_transfer`, `confirm`, `execute_transfer`, `cancel_transfer`
- [x] Routing function; every tool call answered exactly once
- [x] Idempotency key generated in `prepare_transfer`
- [x] Postgres checkpointer in schema `agent` (verify `search_path` behavior)
- [x] `agent.threads` ownership table
- [x] `POST /chat` and `POST /chat/{thread_id}/confirm` with JWT validation, ownership check, `409` cases
- [x] Console client: y/n confirmation
- [x] Tests (fake model, stubbed wallet-api): no execution without approval, decline path, wallet rejection surfaced, ambiguous/unknown contact, foreign thread returns 404, confirm without pending interrupt returns 409, resume after checkpointer reload
- [x] Verify: restart `agent-service` mid-confirmation, then confirm
- [x] Commit: `feat(agent): add transfer flow with human approval`

## Stage 5 — Frontend

- [x] Streamlit app: login, chat, Confirm/Cancel on `pending_confirmation`, logout
- [x] Handle `401` by returning to login
- [x] Dockerfile and compose entry
- [x] Verify the full flow in a browser
- [x] Commit: `feat(frontend): add Streamlit chat client`

## Stage 6 — Observability and evals

- [x] LangSmith enabled only through environment variables; verify startup without them
- [x] Eval harness: login, reset/inspect wallet state, call the agent, retry with backoff on rate limits
- [x] Cases: no transfer without confirmation; balance matches wallet-api; other-user data refused; prompt injection; over-limit rejection reported; unknown contact; ambiguous contact
- [x] Additional cases to reach 10–15 (decline path, repeated confirm, non-positive amount, thread of another user)
- [x] Assertions against wallet-api state where possible
- [x] Document how to run subsets and how to compare providers
- [x] Run with Gemini (17/17 passing on `gemini-3.1-flash-lite`, including the stage 7 FAQ cases); Anthropic run not possible without a key (see open decisions)
- [x] Commit: `feat(agent): add tracing and evaluation suite`

## Stage 7 — Retrieval

- [x] 2–3 markdown documents in `agent-service/knowledge/`
- [x] Embeddings through `init_embeddings`; pgvector table in schema `agent`
- [x] Idempotent indexing at startup
- [x] `search_knowledge` tool wired into `read_tools`
- [x] 2–3 FAQ eval cases
- [x] Commit: `feat(agent): add knowledge retrieval`

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
- **Default model is `gemini-3.5-flash`**: `gemini-2.5-flash` is no longer available to new free-tier keys.
- **Stage 3 exposes a minimal `/chat`** (in-memory checkpointer, no thread ownership) so the console client has something to talk to; persistence and ownership arrive in stage 4.
- **The user token is passed as `SecretStr` in `configurable`.** LangGraph copies every plain string in `configurable` into persisted checkpoint metadata, which would have stored the token in Postgres; a test asserts it does not appear in checkpoints.
- **Mixed tool calls:** when one model turn contains `request_transfer` together with other tool calls, the transfer branch runs alone and the other calls are answered with "not executed" so each call gets exactly one result.
- **Thread ids are server-generated.** A client-supplied `thread_id` that does not exist is `404`, identical to a foreign one, so ids cannot be probed or pre-claimed.
- **Agent tests that touch Postgres** (restart persistence) are skipped unless `TEST_DATABASE_URL` is set; CI sets it. On Windows the suite switches to the selector event loop because psycopg's async driver cannot use the default one.
- **Anthropic comparison not run:** no `ANTHROPIC_API_KEY` was available. The suite is provider-neutral; running it with `LLM_PROVIDER=anthropic` is the only step left.
- **Gemini free-tier quota:** `gemini-3.5-flash` allows 20 requests per day, which a full eval run exceeds, so the default is `gemini-3.1-flash-lite`.
- **Knowledge documents are in Spanish** because the assistant retrieves and answers in Spanish.
- **Indexing failures at startup are logged, not fatal**, so the wallet features keep working if the embeddings provider is down.
