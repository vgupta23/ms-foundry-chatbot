from langchain_postgres import PGVector

from .config import settings
from .llm import get_embeddings

# Table names created by langchain-postgres; used for document listing/deletion by metadata.
EMBEDDING_TABLE = "langchain_pg_embedding"
COLLECTION_TABLE = "langchain_pg_collection"


def get_vectorstore() -> PGVector:
    # Sync mode creates the pgvector extension, tables and collection on construction.
    return PGVector(
        embeddings=get_embeddings(),
        collection_name=settings.vector_collection,
        connection=settings.sqlalchemy_url,
        use_jsonb=True,
    )
