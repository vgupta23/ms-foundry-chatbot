from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool

from ..deps import get_pool

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request):
    return {
        "status": "ok",
        "provider": request.app.state.provider,
        "rag_enabled": request.app.state.vectorstore is not None,
    }


@router.get("/readyz")
async def readyz(pool: AsyncConnectionPool = Depends(get_pool)):
    try:
        async with pool.connection(timeout=3) as conn:
            await conn.execute("SELECT 1")
    except Exception as e:
        return JSONResponse({"status": "unavailable", "detail": type(e).__name__}, status_code=503)
    return {"status": "ready"}
