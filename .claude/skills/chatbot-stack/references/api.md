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
langchain-text-splitters
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
    azure_openai_embedding_deployment: str | None = None

    database_url: str                              # postgresql://user:pass@db:5432/chatbot
    vector_collection: str = "documents"
    chunk_size: int = 1000
    chunk_overlap: int = 150
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

At startup, validate that the chosen provider has all of its required variables. For Azure that means the endpoint, key and chat deployment. The embedding deployment is **optional**: without it, the app skips creating the vector store, gives the agent no retrieval tool, has upload return 503 with a clear message, and reports `rag_enabled: false` from `/api/healthz`, which the UI uses to hide the upload button. Also accept a Foundry *project* endpoint (`https://<res>.services.ai.azure.com/api/projects/<proj>`) by normalizing it to the resource root in a field validator. The OpenAI-compatible client needs the root. If any are missing, raise an error that names them. Never print their values.

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
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
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
- `retrieve_documents(query: str) -> str` is a `@tool` that runs a similarity search with `k=settings.retrieval_k` and returns the chunks formatted with their `source`. The docstring tells the model to use the tool when a question may relate to uploaded documents.
- The system prompt tells the model to cite source filenames when it uses retrieved content, and to answer normally when no documents are relevant.
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

1. Load the file. For PDFs, use `pypdf.PdfReader` and extract text page by page, keeping `page` in the metadata. For .txt and .md files, decode as UTF-8 with `errors="replace"`.
2. Split with `RecursiveCharacterTextSplitter(chunk_size, chunk_overlap)`.
3. Call `vectorstore.aadd_documents(docs)`, or run the sync version in a threadpool, with the metadata `{document_id, source, page?}`.
4. Reject empty extractions, for example scanned PDFs with no text layer, by returning 422 with a clear message.

## Local dev without Docker (optional)

`cd api && python -m venv .venv && pip install -r requirements.txt && uvicorn app.main:app --reload`. On Windows, psycopg's async mode needs a selector event loop. Prefer running inside the container, and only add the `WindowsSelectorEventLoopPolicy` fix when running natively on Windows.
