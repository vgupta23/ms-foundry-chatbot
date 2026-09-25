import uuid

from fastapi import APIRouter, Depends, HTTPException
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from ..deps import get_checkpointer, get_graph, get_pool
from .chat import text_of

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
async def list_conversations(pool: AsyncConnectionPool = Depends(get_pool)):
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
        )
        return await cur.fetchall()


@router.get("/{conversation_id}/messages")
async def get_messages(conversation_id: uuid.UUID, graph=Depends(get_graph), pool: AsyncConnectionPool = Depends(get_pool)):
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT 1 FROM conversations WHERE id = %s", (conversation_id,))
        if not await cur.fetchone():
            raise HTTPException(404, "Conversation not found")

    state = await graph.aget_state({"configurable": {"thread_id": str(conversation_id)}})
    roles = {"human": "user", "ai": "assistant"}
    return [
        {"role": roles[m.type], "content": text}
        for m in state.values.get("messages", [])
        if m.type in roles and (text := text_of(m.content))
    ]


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    pool: AsyncConnectionPool = Depends(get_pool),
    checkpointer: AsyncPostgresSaver = Depends(get_checkpointer),
):
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,))
    await checkpointer.adelete_thread(str(conversation_id))
