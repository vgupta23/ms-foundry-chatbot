# API (FastAPI + LangGraph)

## Dependencies (`api/requirements.txt`)

Resolve the latest stable versions when you create the file, then pin them with `==`:

```
fastapi
uvicorn[standard]
pydantic-settings
python-multipart          # file uploads
langchain-core
langchain-openai          # ChatOpenAI, AzureChatOpenAI, OpenAIEmbeddings, AzureOpenAIEmbeddings
langchain-text-splitters  # sentence-level fallback inside chunking.py
langchain-postgres        # PGVector (psycopg3)
langgraph
langgraph-checkpoint-postgres
psycopg[binary,pool]
pypdf
```

After a successful build, write the resolved versions back into `requirements.txt` (for example from `pip freeze` output for the top-level packages).

## Config (`app/config.py`)

Use `pydantic-settings`. Every setting is an env var. Compose injects `.env`, and `env_file=".env"` also makes `uvicorn` work when run locally outside Docker.

```python
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)  # `LLM_PROVIDER=` means unset

    llm_provider: Literal["openai", "azure"] | None = None

    openai_api_key: str | None = None
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    azure_openai_endpoint: str | None = None      # https://<resource>.openai.azure.com/ (Foundry resource)
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str | None = None
    azure_openai_embedding_deployment: str | None = None   # deployment NAME, never a URL
    # Optional: only when the embedding deployment lives on a different resource than the chat deployment.
    azure_openai_embedding_endpoint: str | None = None
    azure_openai_embedding_api_key: str | None = None

    database_url: str                              # postgresql://user:pass@db:5432/chatbot
    vector_collection: str = "documents"
    # Smart chunking (see chunking.py)
    chunk_size: int = 1000                         # max characters per chunk
    chunk_min_size: int = 200                      # don't close a chunk on a topic shift before this size
    chunk_breakpoint_percentile: float = 90        # adjacent-paragraph distance above this percentile = new topic
    retrieval_k: int = 4
    max_upload_mb: int = 20

    @property
    def provider(self) -> Literal["openai", "azure"]:
        if self.llm_provider:
            return self.llm_provider
        if self.azure_openai_endpoint and self.azure_openai_api_key:
            return "azure"
        if self.openai_api_key:
            return "openai"
        raise RuntimeError(
            "No model provider configured. Set LLM_PROVIDER and either OPENAI_API_KEY, "
            "or AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + AZURE_OPENAI_CHAT_DEPLOYMENT."
        )

    @property
    def sqlalchemy_url(self) -> str:               # PGVector needs the psycopg3 driver prefix
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

settings = Settings()
```

At startup, validate that the chosen provider has all of its required variables. For Azure that means the endpoint, key and chat deployment. The embedding deployment is **optional**: without it, the app skips creating the vector store, gives the agent no retrieval tool, has upload return 503 with a clear message, and reports `rag_enabled: false` from `/api/healthz`, which the UI uses to hide the upload button. Also accept a Foundry *project* endpoint (`https://<res>.services.ai.azure.com/api/projects/<proj>`) by normalizing it to the resource root in a field validator, applied to both `azure_openai_endpoint` and `azure_openai_embedding_endpoint`. The OpenAI-compatible client needs the root. Add a validator that rejects a value containing `://` in `azure_openai_embedding_deployment`, with a message saying to put the name there and a different resource's URL in `AZURE_OPENAI_EMBEDDING_ENDPOINT`. If any are missing, raise an error that names them. Never print their values.

## Provider switch (`app/llm.py`)

This is the only module that knows about providers. Everything else calls `get_chat_model()` and `get_embeddings()`.

```python
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings, ChatOpenAI, OpenAIEmbeddings
from .config import settings

def get_chat_model():
    if settings.provider == "azure":
        return AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_openai_chat_deployment,
            streaming=True,
        )
    return ChatOpenAI(model=settings.openai_chat_model, api_key=settings.openai_api_key, streaming=True)

def get_embeddings():
    if settings.provider == "azure":
        return AzureOpenAIEmbeddings(
            # Embeddings may be deployed on a different resource; fall back to the chat resource.
            azure_endpoint=settings.azure_openai_embedding_endpoint or settings.azure_openai_endpoint,
            api_key=settings.azure_openai_embedding_api_key or settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_openai_embedding_deployment,
        )
    return OpenAIEmbeddings(model=settings.openai_embedding_model, api_key=settings.openai_api_key)
```

Azure AI Foundry note: this code targets models deployed in a Foundry or Azure OpenAI resource through its OpenAI-compatible endpoint. If the user needs non-OpenAI Foundry models (for example Llama or Mistral served through the Foundry model inference endpoint), add `langchain-azure-ai` and branch inside `llm.py`. Don't spread provider logic elsewhere.

## Vector store (`app/vectorstore.py`)

```python
from langchain_postgres import PGVector
from .config import settings
from .llm import get_embeddings

def get_vectorstore() -> PGVector:
    return PGVector(
        embeddings=get_embeddings(),
        collection_name=settings.vector_collection,
        connection=settings.sqlalchemy_url,
        use_jsonb=True,
    )
```

Create it once during lifespan and store it on `app.state`. Store `source` (the filename) and `document_id` in each chunk's metadata, so a document can be listed and deleted as a unit.

## Agent graph (`app/graph.py`)

- Use `langgraph.prebuilt.create_react_agent(model, tools=[retrieve_documents], prompt=SYSTEM_PROMPT, checkpointer=checkpointer)`.
- `retrieve_documents(query: str) -> str` is a `@tool` that runs a similarity search with `k=settings.retrieval_k` and returns each chunk under a label `[source: <file>, page N]` (or `page N-M` when the chunk has `page_end`). The docstring tells the model to use the tool when a question may relate to uploaded documents.
- The system prompt tells the model to cite source filenames (and pages for PDFs, e.g. `(source: report.pdf, page 3)`) when it uses retrieved content, and to answer normally when no documents are relevant.
- **Checkpointer:** use `AsyncPostgresSaver` from `langgraph.checkpoint.postgres.aio` with a dedicated `psycopg_pool.AsyncConnectionPool` whose connection kwargs are `autocommit=True, prepare_threshold=0, row_factory=dict_row`. Call `await checkpointer.setup()` once in lifespan. Check the current `langgraph-checkpoint-postgres` docs if the API differs.
- The conversation's `thread_id` is the conversation UUID, and it provides message memory across turns automatically.

## Conversations table

LangGraph checkpoints store messages but not a listable index of conversations. Create one table at startup (or in `db/init`):

```sql
CREATE TABLE IF NOT EXISTS conversations (
  id UUID PRIMARY KEY,
  title TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The title is the first 60 characters of the first user message.

## Endpoints

All routes are prefixed with `/api`.

| Method | Path | Behaviour |
|---|---|---|
| GET | `/api/healthz` | Liveness check, with no external calls. Returns `{"status":"ok","provider":...}` |
| GET | `/api/readyz` | Runs `SELECT 1` against the DB. Returns 503 on failure |
| POST | `/api/chat` | Body `{message, conversation_id?}`. Creates a conversation when the ID is absent. Streams SSE |
| GET | `/api/conversations` | List ordered by `updated_at desc` |
| GET | `/api/conversations/{id}/messages` | Reads from `graph.aget_state({"configurable":{"thread_id":id}}).values["messages"]`, returning only human and AI messages that have non-empty text content, as `{role, content}` |
| DELETE | `/api/conversations/{id}` | Deletes the row and the checkpoints (`checkpointer.adelete_thread(id)`) |
| POST | `/api/documents` | Multipart `file`. Validates the extension (.pdf/.txt/.md) and size. Ingests, then returns `{document_id, source, chunks}` |
| GET | `/api/documents` | Distinct `document_id` and `source` values from the collection's metadata |
| DELETE | `/api/documents/{document_id}` | Deletes all chunks with that `document_id` |

### SSE contract for `POST /api/chat`

The UI depends on this contract, so keep it stable. Each event is one line, `data: <json>\n\n`:

```
data: {"type":"meta","conversation_id":"<uuid>"}
data: {"type":"token","content":"Hel"}
data: {"type":"token","content":"lo"}
data: {"type":"done"}
data: {"type":"error","message":"..."}         # sent instead of done on failure
```

Implementation:

```python
async def event_stream():
    yield sse({"type": "meta", "conversation_id": conv_id})
    try:
        async for chunk, meta in graph.astream(
            {"messages": [("user", req.message)]},
            config={"configurable": {"thread_id": conv_id}},
            stream_mode="messages",
        ):
            if isinstance(chunk, AIMessageChunk) and chunk.content and meta.get("langgraph_node") == "agent":
                yield sse({"type": "token", "content": chunk.content})
        yield sse({"type": "done"})
    except Exception as e:
        logger.exception("chat failed")
        yield sse({"type": "error", "message": str(e)})

return StreamingResponse(event_stream(), media_type="text/event-stream",
                         headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

Filter to the `agent` node so tool output isn't streamed to the user. Update `conversations.updated_at` after each turn.

## Ingestion (`app/ingest.py`)

1. Load the file. For PDFs, use `pypdf.PdfReader` and extract text page by page (1-based), keeping `page` in the metadata and skipping empty pages. For .txt and .md files, decode as UTF-8 with `errors="replace"`.
2. Split with `smart_split(pages, is_pdf=..., max_chars=chunk_size, min_chars=chunk_min_size, breakpoint_percentile=chunk_breakpoint_percentile, embeddings=vectorstore.embeddings)` from `app/chunking.py` (below).
3. Add `document_id` and `source` to every chunk's metadata and call `vectorstore.add_documents(chunks, ids=[f"{document_id}:{i}", ...])` in a threadpool (it is blocking).
4. Reject empty extractions, for example scanned PDFs with no text layer, by returning 422 with a clear message.

### Smart chunking (`app/chunking.py`)

Fixed-size splitting cuts sentences and mixes topics, so chunks are built from document structure and meaning instead. No overlap is needed because chunks always end on a boundary.

1. **Paragraph units.** Text files: split on blank lines. PDFs: pypdf returns one line per visual line with few blank lines, so rejoin lines, ending a paragraph at a blank line or at a line that ends a sentence and is visibly shorter than a full line. Keep markdown headings (`#`…`######`) and fenced code blocks (```` ``` ````/`~~~`) as their own units; list items stay separate. Each unit records its `page` and the current heading path.
2. **Fit.** Break any unit longer than `max_chars` at sentence boundaries, falling back to `RecursiveCharacterTextSplitter` for text with no sentence ends.
3. **Topic breaks.** Embed all units in one `embed_documents` call, compute the cosine distance between neighbours, and mark a break where the distance is above the `breakpoint_percentile` of all distances in the document. Skip this with fewer than 3 units. If embedding fails, log a warning and continue with structure only; never fail the upload because of it.
4. **Group.** Walk the units and start a new chunk when the next unit would exceed `max_chars`, at a heading (unless the chunk so far holds only headings), or at a topic break once the chunk has at least `min_chars`. Drop chunks that contain only headings.
5. **Context.** Prefix a chunk with `[Heading > Subheading]` when it doesn't start with its own heading, and set metadata `section` (the heading path), `page` (first page) and `page_end` (last page, only when it differs).

Unit tests live in `api/tests/test_chunking.py` (excluded from the image by `.dockerignore`). Use a deterministic fake `Embeddings` (one dimension per keyword topic) to test topic splits, plus a failing one to test the structure-only fallback. Cover heading splits, max size, PDF line rejoining and page ranges. Run them in the API image:

```bash
docker compose run --rm --no-deps -T -v ./api/tests:/app/tests -e DATABASE_URL=postgresql://x@db/x api \
  sh -c "pip install -q --user pytest && python -m pytest -q -p no:cacheprovider tests"
```

Changing chunking settings only affects new uploads. Re-upload documents to re-chunk them.

## Local dev without Docker (optional)

`cd api && python -m venv .venv && pip install -r requirements.txt && uvicorn app.main:app --reload`. On Windows, psycopg's async mode needs a selector event loop. Prefer running inside the container, and only add the `WindowsSelectorEventLoopPolicy` fix when running natively on Windows.
