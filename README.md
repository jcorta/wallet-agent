# wallet-agent

Reference implementation of a conversational assistant for a digital wallet, in which sensitive actions are gated by code and architecture rather than by prompts.

An authenticated user chats with an LLM agent that can read account data and request transfers. The agent never reads the wallet database, never sees or chooses the user's identity, and cannot execute a transfer without explicit human approval enforced by the graph.

> Work in progress. Architecture, setup and design decisions are documented in the final revision of this file. Specification and plan: [`docs/`](docs/).

## Services

| Service | Role |
|---|---|
| `idp` | Toy identity provider: login and JWKS |
| `wallet-api` | Accounts, contacts and transfers; enforces all controls |
| `agent-service` | LangGraph agent exposed over HTTP |
| `frontend` | Streamlit chat client |
| `postgres` | One schema per service (`wallet`, `agent`) |

## License

MIT
