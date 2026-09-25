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

    database_url: str
    vector_collection: str = "documents"
    chunk_size: int = 1000
    chunk_overlap: int = 150
    retrieval_k: int = 4
    max_upload_mb: int = 20

    @field_validator("azure_openai_endpoint")
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
