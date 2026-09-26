---
name: chatbot-stack
description: Build, extend, or run the ms-foundry-chatbot application — a React chat UI, a FastAPI + LangGraph API that talks to OpenAI or Azure AI Foundry (keys from .env), and a Postgres/pgvector database for RAG — each as a container, orchestrated with docker compose on Docker Desktop. Use whenever creating or modifying any part of this app (UI, API, agent graph, document ingestion, model provider config, Dockerfiles, docker-compose), or when asked to run, debug, or deploy it.
---

# Chatbot stack: React UI + FastAPI/LangGraph API + pgvector

## What we are building

Three app containers plus a pgAdmin dev tool, one `docker-compose.yml`, deployed first to **Docker Desktop** and later to a Kubernetes cluster:

| Service | Tech | Image base (Docker Hub) | Host port → container |
|---|---|---|---|
| `ui` | React 18 + Vite + TypeScript, served by nginx | `node:22-alpine` (build) → `nginxinc/nginx-unprivileged:stable-alpine` | `3000 → 8080` |
| `api` | Python 3.12, FastAPI, LangGraph, LangChain | `python:3.12-slim` | `8000 → 8000` |
| `db` | Postgres 16 + pgvector | `pgvector/pgvector:pg16` | `5432 → 5432` |
| `pgadmin` | pgAdmin 4 (dev tool for browsing the DB) | `dpage/pgadmin4:9.18` | `127.0.0.1:5050 → 80` |

Settled requirements. Don't re-ask about these; change them only if the user asks:

- **Model provider:** OpenAI **or** Azure AI Foundry. Credentials are read from `.env` only. `LLM_PROVIDER=openai|azure` picks the provider explicitly. If it is unset, auto-detect: use Azure when its endpoint and key are set, otherwise OpenAI when its key is set, otherwise **fail at startup** with a clear message listing the missing variables.
- **Agent framework:** LangGraph, using `create_react_agent` with a retrieval tool, plus LangChain for model, embedding and vector-store integrations.
- **RAG:** users upload PDF/TXT/MD files in the UI. The API chunks and embeds them into pgvector, and the agent retrieves from them when that is relevant.
- **Smart chunking:** no fixed-size splitting and no overlap. Chunks end on paragraph (or, for very long paragraphs, sentence) boundaries, a markdown heading always starts a new chunk, and a topic shift (adjacent-paragraph embedding distance above `CHUNK_BREAKPOINT_PERCENTILE`) starts one too. Each chunk is prefixed with its heading path and carries `section`, `page` and `page_end` metadata, so citations show PDF page ranges. See the ingestion section of [references/api.md](references/api.md).
- **Azure embeddings may live on a different resource** from the chat deployment: `AZURE_OPENAI_EMBEDDING_ENDPOINT` / `AZURE_OPENAI_EMBEDDING_API_KEY` override the main endpoint and key for embeddings only. `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` is a deployment *name*; reject a URL there at startup.
- **pgAdmin** runs alongside the stack for inspecting data: localhost-only, desktop mode (no pgAdmin login), with the `db` server pre-registered and its password supplied from `.env`. See [references/containers.md](references/containers.md).
- **UI features:** streamed responses (SSE), conversation history kept in Postgres and listed in a sidebar, and markdown rendering with code highlighting.
- **Auth:** none for now, so the app is open on localhost. Keep all routes under `/api` and put the dependency wiring in one place (`app/deps.py`), so adding auth later is a single change.

## Repository layout

```
ms-foundry-chatbot/
├── docker-compose.yml
├── .env.example          # committed; documents every variable
├── .env                  # NOT committed; real keys
├── .gitignore            # .env, node_modules, __pycache__, dist, .venv
├── README.md             # quick start: cp .env.example .env → docker compose up --build
├── db/init/01-extensions.sql
├── pgadmin/servers.json  # pre-registered "chatbot (pgvector)" server for pgAdmin
├── api/
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── requirements.txt
│   └── app/
│       ├── main.py            # FastAPI app, lifespan (pools, checkpointer, graph), routers
│       ├── config.py          # pydantic-settings Settings + provider resolution
│       ├── llm.py             # get_chat_model(), get_embeddings()
│       ├── vectorstore.py     # PGVector store factory
│       ├── graph.py           # LangGraph agent + retrieve tool
│       ├── ingest.py          # load → smart_split → embed
│       ├── chunking.py        # paragraph/heading/topic-aware chunking (smart_split)
│       ├── deps.py            # shared dependencies (future auth hook)
│       └── routers/
│           ├── chat.py            # POST /api/chat (SSE)
│           ├── conversations.py   # list / get messages / delete
│           ├── documents.py       # upload / list / delete
│           └── health.py          # GET /api/healthz, /api/readyz
│   └── tests/test_chunking.py     # unit tests for chunking (excluded from the image)
└── ui/
    ├── Dockerfile
    ├── .dockerignore
    ├── nginx/default.conf.template
    ├── package.json, vite.config.ts, tsconfig.json, index.html
    └── src/
        ├── main.tsx, App.tsx
        ├── api/client.ts       # fetch wrappers + SSE stream parser
        ├── components/         # Sidebar, ChatWindow, MessageBubble, Composer, UploadButton
        └── styles.css
```

## Workflow

1. **Scaffold** the layout above. If files already exist, read them first and extend them rather than overwriting.
2. **Build the API** by following [references/api.md](references/api.md), which covers config, provider switching, the graph, streaming, ingestion and history.
3. **Build the UI** by following [references/ui.md](references/ui.md), which covers SSE parsing, the sidebar, markdown and upload.
4. **Containerize** by following [references/containers.md](references/containers.md), which covers the Dockerfiles, nginx proxy, compose file and `.env.example`.
5. **Verify.** Don't claim it works until you have checked:
   ```bash
   docker compose config -q                      # compose file is valid
   docker compose up --build -d
   docker compose ps                             # db/api healthy; ui and pgadmin up
   curl -s localhost:8000/api/healthz            # {"status":"ok","provider":"openai|azure"}
   curl -s localhost:3000/api/healthz            # same result through the nginx proxy
   curl -N -X POST localhost:8000/api/chat -H "Content-Type: application/json" \
        -d '{"message":"hello"}'                 # streams data: lines
   curl -s localhost:5050/misc/ping              # pgAdmin answers PING
   docker compose run --rm --no-deps -T -v ./api/tests:/app/tests -e DATABASE_URL=postgresql://x@db/x api \
     sh -c "pip install -q --user pytest && python -m pytest -q -p no:cacheprovider tests"   # chunking tests
   docker compose logs api --tail 50             # on any failure
   ```
   Also test an upload followed by a question about the uploaded file, and check that the answer cites the file (with page or page range for PDFs). If no real API key is available, say so, and verify everything up to the model call (containers healthy, DB reachable, and startup failing with a clear message when keys are missing).

## Non-negotiable rules

- **Secrets:** keys come only from environment variables, which compose populates from `.env`. Never bake `.env` into an image: list it in both `.dockerignore` files. Never log keys. `.env.example` holds placeholders only.
- **Official images only**, pulled from Docker Hub with **pinned major/minor tags** and never `latest`. Pin Python and npm dependencies too: `requirements.txt` with `==`, and a `package-lock.json` installed with `npm ci`.
- **Host ports must not clash.** Before adding a service or changing a port, check what is already bound on the host (`docker ps`, and `netstat.exe -ano` from WSL) and pick a free port. Bind dev-only tools such as pgAdmin to `127.0.0.1`.
- **Cluster-ready from day one:** containers are stateless (all state lives in Postgres), run as non-root, are configured only through env vars, expose health endpoints, log to stdout, and don't hardcode `localhost` between services. Use compose service names (`db`, `api`), which become k8s Service names later.
- **The UI reaches the API only via the relative path `/api`.** nginx proxies it, so there is no CORS setup and no API URL baked into the JS bundle at build time. The upstream comes from the `API_UPSTREAM` env var.
- **Embedding dimension must stay consistent.** Changing the embedding model or deployment means re-ingesting documents (or using a new collection name). Using the same embedding model on both providers (for example `text-embedding-3-small`, 1536 dimensions) lets the provider switch without re-ingesting.
- Keep `docker compose up --build` from a fresh clone (after `cp .env.example .env` and adding keys) as the only command needed to run the app.

## Out of scope for now (don't build unless asked)

Authentication/login, Kubernetes manifests or Helm charts, CI/CD, multi-tenancy. When the user asks for the cluster move, reuse the same images and env var names, move secrets into k8s Secrets, and put Postgres on a managed service or a StatefulSet.
