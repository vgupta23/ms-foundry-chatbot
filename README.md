# ms-foundry-chatbot

Chatbot with a React UI, a FastAPI + LangGraph API (OpenAI or Azure AI Foundry), and Postgres/pgvector for RAG.
All three run as containers via docker compose.

## Quick start (Docker Desktop)

```bash
cp .env.example .env      # then fill in OPENAI_API_KEY or the AZURE_OPENAI_* values
docker compose up --build -d
```

- UI: http://localhost:3000
- API docs: http://localhost:8000/docs
- Postgres: localhost:5432 (credentials from `.env`)
- pgAdmin: http://localhost:5050 (no login; the "chatbot (pgvector)" server is pre-registered, localhost only)

## Choosing the model provider

Set `LLM_PROVIDER=openai` or `LLM_PROVIDER=azure` in `.env`. If it is empty, the API uses Azure AI Foundry
when `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` are set, otherwise OpenAI. The API refuses to start
(see `docker compose logs api`) if the selected provider is missing required settings.

After editing `.env`, recreate the API container: `docker compose up -d api`.

## Data persistence

Postgres data (vectors, chat history) lives in the named volume `pgdata`, mounted at
`/var/lib/postgresql/data` in the `db` container. It survives `docker compose down` and rebuilds;
`docker compose down -v` deletes it.

## Services

| Service | Port | Image |
|---|---|---|
| ui  | 3000 → 8080 | built from `ui/` (node:22-alpine → nginx-unprivileged) |
| api | 8000 | built from `api/` (python:3.12-slim) |
| db  | 5432 | pgvector/pgvector:pg16 |
| pgadmin | 127.0.0.1:5050 → 80 | dpage/pgadmin4:9.18 |

## Local development without Docker

```bash
docker compose up -d db
cd api && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
DATABASE_URL=postgresql://chatbot:change-me@localhost:5432/chatbot uvicorn app.main:app --reload
cd ui && npm ci && npm run dev    # http://localhost:5173, proxies /api to :8000
```
