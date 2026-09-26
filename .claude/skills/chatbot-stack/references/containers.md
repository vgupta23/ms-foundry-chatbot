# Containers and docker compose

All base images are official images from Docker Hub with pinned tags. Docker Desktop pulls them automatically on `docker compose up --build`. To pre-pull them, run `docker compose pull db`; the `api` and `ui` base images are pulled during the build.

## `api/Dockerfile`

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/healthz').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
```

`api/.dockerignore`: `.env`, `.venv`, `__pycache__`, `*.pyc`, `.pytest_cache`, `tests`.

## `ui/Dockerfile`

This is a multi-stage build: Node builds the static bundle, and unprivileged nginx serves it on port 8080, as a non-root user, which suits a cluster.

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:stable-alpine
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
ENV API_UPSTREAM=http://api:8000
EXPOSE 8080
```

`ui/.dockerignore`: `node_modules`, `dist`, `.env`, `.env.*`.

## `ui/nginx/default.conf.template`

When the nginx image starts, it runs `envsubst` on the files in `/etc/nginx/templates/`, substituting only variables that are defined in the environment. That means `$uri` stays intact while `${API_UPSTREAM}` is replaced. The same image therefore works in compose and in k8s: only `API_UPSTREAM` changes.

```nginx
server {
    listen 8080;
    root /usr/share/nginx/html;
    index index.html;

    location /api/ {
        proxy_pass ${API_UPSTREAM};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_buffering off;            # required for SSE streaming
        proxy_cache off;
        proxy_read_timeout 300s;
        client_max_body_size 25m;       # keep ≥ MAX_UPLOAD_MB
    }

    location / {
        try_files $uri $uri/ /index.html;   # SPA fallback
    }
}
```

## `db/init/01-extensions.sql`

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

This runs only when the `pgdata` volume is first initialized. The API must still be idempotent: `CREATE TABLE IF NOT EXISTS` and `checkpointer.setup()`.

## `docker-compose.yml`

```yaml
name: ms-foundry-chatbot

services:
  db:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-chatbot}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: ${POSTGRES_DB:-chatbot}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d:ro
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 20

  api:
    build: ./api
    image: ms-foundry-chatbot-api:local
    restart: unless-stopped
    env_file: .env
    environment:
      DATABASE_URL: postgresql://${POSTGRES_USER:-chatbot}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB:-chatbot}
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8000:8000"

  ui:
    build: ./ui
    image: ms-foundry-chatbot-ui:local
    restart: unless-stopped
    environment:
      API_UPSTREAM: http://api:8000
    depends_on:
      api:
        condition: service_healthy
    ports:
      - "3000:8080"

  # Web UI for browsing Postgres/pgvector data. Local dev tool only: bound to 127.0.0.1,
  # runs in desktop mode (no pgAdmin login), and the "db" server is pre-registered.
  pgadmin:
    image: dpage/pgadmin4:9.18
    restart: unless-stopped
    environment:
      PGADMIN_DEFAULT_EMAIL: ${PGADMIN_DEFAULT_EMAIL:-admin@example.com}
      PGADMIN_DEFAULT_PASSWORD: ${PGADMIN_DEFAULT_PASSWORD:-admin}
      PGADMIN_CONFIG_SERVER_MODE: "False"
      PGADMIN_CONFIG_MASTER_PASSWORD_REQUIRED: "False"
      PGADMIN_SERVER_JSON_FILE: /pgadmin4/servers.json
      PGPASS_LINE: db:5432:*:${POSTGRES_USER:-chatbot}:${POSTGRES_PASSWORD}
    # Write a pgpass from .env so the pre-registered server connects without a prompt.
    entrypoint: ["/bin/sh", "-c", "printf '%s\\n' \"$$PGPASS_LINE\" > /var/lib/pgadmin/pgpass && chmod 600 /var/lib/pgadmin/pgpass && exec /entrypoint.sh"]
    volumes:
      - pgadmin-data:/var/lib/pgadmin
      - ./pgadmin/servers.json:/pgadmin4/servers.json:ro
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "127.0.0.1:5050:80"

volumes:
  pgdata:
  pgadmin-data:
```

## `pgadmin/servers.json`

```json
{
  "Servers": {
    "1": {
      "Name": "chatbot (pgvector)",
      "Group": "ms-foundry-chatbot",
      "Host": "db",
      "Port": 5432,
      "MaintenanceDB": "postgres",
      "Username": "chatbot",
      "SSLMode": "prefer",
      "PassFile": "/var/lib/pgadmin/pgpass"
    }
  }
}
```

`Username` must match `POSTGRES_USER`. pgAdmin imports this file only on the first start of a fresh `pgadmin-data` volume, so after changing it run `docker compose rm -sf pgadmin && docker volume rm ms-foundry-chatbot_pgadmin-data`, then `docker compose up -d pgadmin`.

Notes:
- The `api` service gets its health check from its Dockerfile `HEALTHCHECK`, which `condition: service_healthy` relies on.
- `DATABASE_URL` is composed here so the password is defined only once in `.env`. The explicit `environment:` value overrides any `DATABASE_URL` set in `.env`, so the API always reaches `db` inside the compose network.
- `db` is exposed on host port 5432 for local tools such as psql. If port 5432 is already in use on the host, change the left-hand side (for example `"5433:5432"`).
- pgAdmin is at http://localhost:5050 with no login. It reaches Postgres over the compose network (`db:5432`), not the host port. The password never goes into the repo: the entrypoint writes it from `.env` into a `0600` pgpass file inside the volume. Desktop mode with no login is acceptable only because the port is bound to `127.0.0.1`; don't publish it on `0.0.0.0` or carry it into a cluster without turning server mode and login back on.
- The app services use host ports 3000, 8000 and 5432, and pgAdmin uses 5050. Before changing one, check that the new host port is free (`docker ps`, `netstat.exe -ano | grep LISTENING` from WSL).
- Open the app at http://localhost:3000, with API docs at http://localhost:8000/docs.

## `.env.example` (commit this; real values go in `.env`)

```dotenv
# ---- Model provider ----
# openai | azure. Leave empty to auto-detect (azure if its endpoint+key are set, else openai).
LLM_PROVIDER=

# OpenAI
OPENAI_API_KEY=
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Azure AI Foundry (Azure OpenAI-compatible deployment)
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_CHAT_DEPLOYMENT=
# Deployment NAME (not a URL), e.g. text-embedding-3-small. Empty = document upload/RAG disabled.
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=
# Optional: only if the embedding deployment is on a different resource (defaults to the values above).
AZURE_OPENAI_EMBEDDING_ENDPOINT=
AZURE_OPENAI_EMBEDDING_API_KEY=

# ---- Database ----
POSTGRES_USER=chatbot
POSTGRES_PASSWORD=change-me
POSTGRES_DB=chatbot

# ---- RAG tuning ----
VECTOR_COLLECTION=documents
# Smart chunking: chunks end on paragraph boundaries and split where the topic shifts.
# CHUNK_SIZE = max characters per chunk; CHUNK_MIN_SIZE = smallest chunk closed on a topic shift;
# CHUNK_BREAKPOINT_PERCENTILE = paragraph-to-paragraph embedding distance above this percentile starts a new chunk.
CHUNK_SIZE=1000
CHUNK_MIN_SIZE=200
CHUNK_BREAKPOINT_PERCENTILE=90
RETRIEVAL_K=4
MAX_UPLOAD_MB=20
```

In the example file, leave `AZURE_OPENAI_ENDPOINT` as the placeholder shown, or blank it. If the placeholder is non-empty while the key is empty, auto-detect must still skip Azure, because detection requires **both** values.

## Common operations

```bash
docker compose up --build -d          # build and start
docker compose logs -f api            # follow API logs
docker compose up -d api              # recreate the API to pick up .env changes
docker compose down                   # stop (keeps data)
docker compose down -v                # stop and WIPE the vector DB and history
docker compose exec db psql -U chatbot -d chatbot -c "\dx"   # confirm pgvector is installed
curl -s localhost:5050/misc/ping      # pgAdmin is up (UI at http://localhost:5050)
```

Changes to `.env` require recreating the container, which `docker compose up -d` does. A restart alone is not enough.

## Troubleshooting

- **The UI shows the whole response at once instead of streaming:** check that `proxy_buffering off` is set and that the API sends `X-Accel-Buffering: no`.
- **`api` is unhealthy with "No model provider configured":** `.env` is missing or its keys are empty. Run `docker compose config` to see the resolved values, and redact them before showing them to anyone.
- **`relation "langchain_pg_embedding" ... dimension` errors after changing the embedding model:** re-ingest the documents into a new `VECTOR_COLLECTION`, or run `docker compose down -v` in dev.
- **413 on upload:** raise both `client_max_body_size` and `MAX_UPLOAD_MB`.
- **Port already in use on Windows:** change the host side of the port mapping.
- **`docker` in WSL says it can't connect to `/var/run/docker.sock`:** Docker Desktop's WSL integration is off for this distro. Use `docker.exe` (same CLI, talks to Docker Desktop) or enable the integration under Settings → Resources → WSL integration.
- **pgAdmin asks for the DB password or can't connect:** check that `POSTGRES_PASSWORD` is set in `.env`, then `docker compose up -d pgadmin` to recreate it, which rewrites the pgpass. `docker compose logs pgadmin` should show `Added 1 Server Group(s) and 1 Server(s)` on the first start.
