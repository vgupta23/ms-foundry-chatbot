import io
import uuid
from pathlib import Path

from langchain_core.documents import Document
from langchain_postgres import PGVector
from pypdf import PdfReader

from .chunking import smart_split
from .config import settings

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}


class EmptyDocumentError(ValueError):
    pass


def load(filename: str, data: bytes) -> list[Document]:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        return [
            Document(page_content=text, metadata={"page": i})
            for i, page in enumerate(reader.pages, start=1)
            if (text := (page.extract_text() or "").strip())
        ]
    text = data.decode("utf-8", errors="replace").strip()
    return [Document(page_content=text)] if text else []


def ingest(vectorstore: PGVector, filename: str, data: bytes) -> tuple[str, int]:
    """Load, split and embed a file. Returns (document_id, chunk_count). Blocking; run in a threadpool."""
    pages = load(filename, data)
    chunks = smart_split(
        pages,
        is_pdf=Path(filename).suffix.lower() == ".pdf",
        max_chars=settings.chunk_size,
        min_chars=settings.chunk_min_size,
        breakpoint_percentile=settings.chunk_breakpoint_percentile,
        embeddings=vectorstore.embeddings,
    )
    if not chunks:
        raise EmptyDocumentError(
            f"No text could be extracted from '{filename}'. Scanned PDFs without a text layer are not supported."
        )

    document_id = str(uuid.uuid4())
    for chunk in chunks:
        chunk.metadata.update(document_id=document_id, source=filename)
    ids = [f"{document_id}:{i}" for i in range(len(chunks))]
    vectorstore.add_documents(chunks, ids=ids)
    return document_id, len(chunks)
