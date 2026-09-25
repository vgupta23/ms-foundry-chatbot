"""Shared request dependencies.

All routers get app-wide resources through these functions. When authentication is added,
put the current-user dependency here and attach it to the routers in main.py.
"""

from fastapi import HTTPException, Request
from langchain_postgres import PGVector
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool


def get_pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool


def get_graph(request: Request):
    return request.app.state.graph


def get_checkpointer(request: Request) -> AsyncPostgresSaver:
    return request.app.state.checkpointer


def get_vectorstore(request: Request) -> PGVector:
    vectorstore = request.app.state.vectorstore
    if vectorstore is None:
        raise HTTPException(503, "Document upload is disabled: set AZURE_OPENAI_EMBEDDING_DEPLOYMENT in .env")
    return vectorstore
