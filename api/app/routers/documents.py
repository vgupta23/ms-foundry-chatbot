from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from langchain_postgres import PGVector
from psycopg_pool import AsyncConnectionPool

from ..config import settings
from ..deps import get_pool, get_vectorstore
from ..ingest import ALLOWED_EXTENSIONS, EmptyDocumentError, ingest
from ..vectorstore import COLLECTION_TABLE, EMBEDDING_TABLE

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", status_code=201)
async def upload_document(file: UploadFile, vectorstore: PGVector = Depends(get_vectorstore)):
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"File exceeds the {settings.max_upload_mb} MB limit")

    try:
        document_id, chunks = await run_in_threadpool(ingest, vectorstore, filename, data)
    except EmptyDocumentError as e:
        raise HTTPException(422, str(e))
    return {"document_id": document_id, "source": filename, "chunks": chunks}


@router.get("")
async def list_documents(request: Request, pool: AsyncConnectionPool = Depends(get_pool)):
    if request.app.state.vectorstore is None:
        return []  # vector tables are not created when RAG is disabled
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"""
            SELECT e.cmetadata->>'document_id' AS document_id,
                   e.cmetadata->>'source'      AS source,
                   count(*)                    AS chunks
            FROM {EMBEDDING_TABLE} e
            JOIN {COLLECTION_TABLE} c ON e.collection_id = c.uuid
            WHERE c.name = %s AND e.cmetadata ? 'document_id'
            GROUP BY 1, 2
            ORDER BY 2
            """,
            (settings.vector_collection,),
        )
        return await cur.fetchall()


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    pool: AsyncConnectionPool = Depends(get_pool),
    _: PGVector = Depends(get_vectorstore),
):
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"""
            DELETE FROM {EMBEDDING_TABLE} e
            USING {COLLECTION_TABLE} c
            WHERE e.collection_id = c.uuid AND c.name = %s AND e.cmetadata->>'document_id' = %s
            """,
            (settings.vector_collection, document_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Document not found")
