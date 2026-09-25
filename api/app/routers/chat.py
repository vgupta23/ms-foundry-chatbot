import json
import logging
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from ..deps import get_graph, get_pool
from ..graph import AGENT_NODE

router = APIRouter(tags=["chat"])
logger = logging.getLogger("chatbot.chat")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: uuid.UUID | None = None


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def text_of(content) -> str:
    """Message content may be a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "") if isinstance(block, dict) else str(block) for block in content or []
    )


@router.post("/chat")
async def chat(req: ChatRequest, graph=Depends(get_graph), pool: AsyncConnectionPool = Depends(get_pool)):
    conv_id = str(req.conversation_id or uuid.uuid4())
    title = " ".join(req.message.split())[:60]
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO conversations (id, title) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (conv_id, title),
        )

    async def event_stream():
        yield sse({"type": "meta", "conversation_id": conv_id})
        try:
            async for chunk, meta in graph.astream(
                {"messages": [("user", req.message)]},
                config={"configurable": {"thread_id": conv_id}},
                stream_mode="messages",
            ):
                if isinstance(chunk, AIMessageChunk) and meta.get("langgraph_node") == AGENT_NODE:
                    if text := text_of(chunk.content):
                        yield sse({"type": "token", "content": text})
            yield sse({"type": "done"})
        except Exception as e:
            logger.exception("chat failed for conversation %s", conv_id)
            yield sse({"type": "error", "message": str(e)})
        finally:
            async with pool.connection() as conn:
                await conn.execute("UPDATE conversations SET updated_at = now() WHERE id = %s", (conv_id,))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
