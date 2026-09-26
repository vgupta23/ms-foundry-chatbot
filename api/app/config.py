from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["openai", "azure"]


class Settings(BaseSettings):
    # env_ignore_empty: `LLM_PROVIDER=` in .env means "not set", not an empty string.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    llm_provider: Provider | None = None

    openai_api_key: str | None = None
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str | None = None
    azure_openai_embedding_deployment: str | None = None
    # Optional: only when the embedding deployment lives on a different resource than the chat deployment.
    azure_openai_embedding_endpoint: str | None = None
    azure_openai_embedding_api_key: str | None = None

    database_url: str
    vector_collection: str = "documents"
    # Smart chunking (see chunking.py): chunks end on paragraph boundaries and break on topic shifts.
    chunk_size: int = 1000  # max characters per chunk
    chunk_min_size: int = 200  # don't close a chunk on a topic shift before it reaches this size
    chunk_breakpoint_percentile: float = 90  # adjacent-paragraph distance above this percentile = new topic
    retrieval_k: int = 4
    max_upload_mb: int = 20

    @field_validator("azure_openai_embedding_deployment")
    @classmethod
    def _deployment_name(cls, v: str | None) -> str | None:
        if v and "://" in v:
            raise ValueError(
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT must be the deployment name (e.g. text-embedding-3-small), "
                "not a URL. Put a different resource's URL in AZURE_OPENAI_EMBEDDING_ENDPOINT instead."
            )
        return v

    @field_validator("azure_openai_endpoint", "azure_openai_embedding_endpoint")
    @classmethod
    def _resource_root(cls, v: str | None) -> str | None:
        # A Foundry project endpoint (https://<res>.services.ai.azure.com/api/projects/<proj>) is not the
        # OpenAI-compatible endpoint; the Azure OpenAI client needs the resource root.
        if v and "/api/projects/" in v:
            parts = urlsplit(v)
            return f"{parts.scheme}://{parts.netloc}/"
        return v

    @property
    def rag_enabled(self) -> bool:
        if self.provider == "azure":
            return bool(self.azure_openai_embedding_deployment)
        return True

    @property
    def provider(self) -> Provider:
        if self.llm_provider:
            return self.llm_provider
        if self.azure_openai_endpoint and self.azure_openai_api_key:
            return "azure"
        if self.openai_api_key:
            return "openai"
        raise RuntimeError(
            "No model provider configured. Set LLM_PROVIDER and either OPENAI_API_KEY, "
            "or AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + AZURE_OPENAI_CHAT_DEPLOYMENT in .env."
        )

    @property
    def sqlalchemy_url(self) -> str:
        # PGVector uses SQLAlchemy and needs the psycopg3 driver prefix.
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    def validate_provider(self) -> Provider:
        """Resolve the provider and make sure all of its variables are set (names only, never values)."""
        provider = self.provider
        required = {
            "openai": {"OPENAI_API_KEY": self.openai_api_key},
            "azure": {
                "AZURE_OPENAI_ENDPOINT": self.azure_openai_endpoint,
                "AZURE_OPENAI_API_KEY": self.azure_openai_api_key,
                "AZURE_OPENAI_CHAT_DEPLOYMENT": self.azure_openai_chat_deployment,
                # AZURE_OPENAI_EMBEDDING_DEPLOYMENT is optional: without it, document upload/RAG is disabled.
            },
        }[provider]
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"LLM provider '{provider}' is missing required settings: {', '.join(missing)}")
        return provider


settings = Settings()
