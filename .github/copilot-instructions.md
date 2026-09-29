# Copilot instructions for ms-foundry-chatbot

## Repository overview

This repo is a three-container chatbot stack:

- UI: React 18 + TypeScript + Vite, served by nginx on port 3000 (container port 8080)
- API: FastAPI + LangGraph + LangChain, running on Python 3.12 and exposing `/api/*`
- Database: Postgres 16 with pgvector for chat history and document embeddings

The app is designed to run with `docker compose up --build -d` from a fresh clone after copying `.env.example` to `.env`, setting a unique database password, and filling in provider credentials. The app's published ports bind to loopback for local development. The browser should only reach the API through the relative `/api` path; nginx handles the upstream proxy.

## Build, test, and validation commands

### Local compose workflow

```bash
cp .env.example .env
# set a unique POSTGRES_PASSWORD and fill in OPENAI_API_KEY or AZURE_OPENAI_* values

docker compose config -q
docker compose up --build -d
docker compose ps
docker compose logs api --tail 50
```

### Health checks

```bash
curl -s localhost:8000/api/healthz
curl -s localhost:3000/api/healthz
curl -s localhost:5050/misc/ping
```

The API health response includes the provider name and whether RAG is enabled.

### Single-test execution

There is no broad JS test runner in this repo; the concrete automated test target is the Python chunking suite:

```bash
docker compose run --rm --no-deps -T -v ./api/tests:/app/tests -e DATABASE_URL=postgresql://x@db/x api \
  sh -c "pip install -q --user pytest && python -m pytest -q -p no:cacheprovider tests/test_chunking.py"
```

You can also run a single test from that file by name:

```bash
docker compose run --rm --no-deps -T -v ./api/tests:/app/tests -e DATABASE_URL=postgresql://x@db/x api \
  sh -c "pip install -q --user pytest && python -m pytest -q -p no:cacheprovider tests/test_chunking.py -k topic_shift"
```

### UI build

```bash
cd ui && npm ci && npm run build
```

This is the project’s TypeScript/Vite verification step; `npm run build` includes `tsc --noEmit` before the Vite bundle.

### API startup locally

```bash
docker compose up -d db
cd api && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
DATABASE_URL=postgresql://chatbot:change-me@localhost:5432/chatbot uvicorn app.main:app --reload
```

### Runtime smoke checks

```bash
curl -N -X POST localhost:8000/api/chat -H "Content-Type: application/json" \
  -d '{"message":"hello"}'
```

The chat endpoint streams SSE tokens; successful output includes a sequence of `data:` lines followed by a `done` event.

## High-level architecture

### UI layer

The UI is deliberately simple: `App.tsx` owns conversation state and passes it down to components. It uses relative `/api` fetch calls and parses a streamed fetch response in `src/api/client.ts` instead of relying on `EventSource` (which only supports GET).

Key points:

- `App.tsx` manages conversations, messages, documents, upload state, and streaming status
- `src/components/Sidebar.tsx` lists conversations and uploaded docs
- `src/components/ChatWindow.tsx` renders the live message timeline
- `src/components/MessageBubble.tsx` renders Markdown with GFM and syntax highlighting
- `nginx/default.conf.template` proxies `/api/` to `API_UPSTREAM` and disables buffering so streaming works correctly

### API layer

The API startup path in `api/app/main.py` does the important initialization work:

- validates provider configuration (`settings.validate_provider()`)
- opens the Postgres connection pool and creates the LangGraph checkpoint tables
- creates the `conversations` table if needed
- initializes the vector store only when embeddings are configured (`settings.rag_enabled`)
- builds the shared LangGraph app once and stores it on `app.state`

The important modules are:

- `app/config.py`: reads `.env` through `pydantic-settings`, resolves `LLM_PROVIDER`, and normalizes Azure endpoints
- `app/llm.py`: centralizes Azure/OpenAI chat and embedding model creation
- `app/graph.py`: creates the ReAct agent with retrieval tool and system prompt
- `app/vectorstore.py`: constructs the pgvector document store
- `app/ingest.py` + `app/chunking.py`: PDF/TXT/MD ingestion and smart chunking
- `app/deps.py`: intended single dependency injection point for future auth or shared request state

### Data and RAG flow

The app stores all persistent state in Postgres:

- `conversations`: chat titles and metadata
- LangGraph checkpoint tables: message state and memory per conversation
- `langchain_pg_collection` / `langchain_pg_embedding`: document chunks and vectors

Document ingestion is structured around PDF/TXT/MD upload and chunking that respects headings and paragraph boundaries. The retrieval tool searches the vector store and returns source-aware chunk text to the model.

### Provider selection

The API is intentionally configured through environment variables only; no keys are stored in code. The provider logic is:

1. `LLM_PROVIDER=openai|azure` wins when set
2. If unset, use Azure when `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_API_KEY` are present
3. Otherwise use OpenAI when `OPENAI_API_KEY` is present
4. Otherwise fail at startup with a clear error naming the missing variables

This logic is intentionally centralized in `app/config.py` and should remain there when adjusting provider behavior.

## Key repository conventions

- Keep secrets in `.env` and never bake them into Docker images or source control. `.env` is excluded from Docker builds via both `.dockerignore` files.
- Keep development ports bound to `127.0.0.1`; do not expose the unauthenticated app or database to the network.
- Use Docker service names (`db`, `api`, `ui`) inside Compose; do not hardcode `localhost` between containers.
- Keep routing under `/api` and use nginx as the only browser-facing proxy to the FastAPI app.
- Preserve the single-source-of-truth configuration pattern: env vars in `.env.example` and `app/config.py`, not scattered config code.
- Keep model/provider behavior and DB creation logic in the app startup path rather than per-route logic; `main.py` initializes shared state once.
- Pin dependency versions (`requirements.txt` and exact npm versions in `package.json` / lockfile) instead of using floating ranges.
- The app is intentionally localhost-focused: no auth, no multi-tenancy, and no k8s manifests in scope right now.
- Use the existing chunking test as the canonical validation target for document-splitting behavior; changes there are likely to affect retrieval quality and should be tested with a targeted pytest run.

## Notes for future Copilot sessions

When making fixes or feature work in this repo:

- Prefer editing the existing FastAPI/LangGraph modules and the UI state flow rather than inventing new abstractions.
- If you need to add config, document it in `.env.example` and enforce it in `app/config.py`.
- If you change the embedding model or deployment, assume the vector collection may need re-ingestion because the embedding dimension must stay consistent.
- If you add a feature that will affect the browser pathing or streaming, check both the nginx config and the UI SSE client before considering it complete.
