import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import settings
from .graph import build_graph
from .routers import chat, conversations, documents, health
from .vectorstore import get_vectorstore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("chatbot")

CONVERSATIONS_DDL = """
CREATE TABLE IF NOT EXISTS conversations (
  id UUID PRIMARY KEY,
  title TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        provider = settings.validate_provider()
    except RuntimeError as e:
        logger.error("Startup aborted: %s", e)
        raise
    logger.info("Using LLM provider: %s", provider)

    # autocommit/prepare_threshold/dict_row are required by the LangGraph Postgres checkpointer.
    pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open(wait=True)
    try:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        async with pool.connection() as conn:
            await conn.execute(CONVERSATIONS_DDL)

        vectorstore = None
        if settings.rag_enabled:
            vectorstore = await run_in_threadpool(get_vectorstore)
        else:
            logger.warning("No embedding deployment configured: document upload and RAG are disabled.")

        app.state.pool = pool
        app.state.checkpointer = checkpointer
        app.state.vectorstore = vectorstore
        app.state.provider = provider
        app.state.graph = build_graph(vectorstore, checkpointer)
        yield
    finally:
        await pool.close()


app = FastAPI(title="ms-foundry-chatbot API", lifespan=lifespan)

for router in (health.router, chat.router, conversations.router, documents.router):
    app.include_router(router, prefix="/api")
