"""The only module that knows about model providers."""

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings, ChatOpenAI, OpenAIEmbeddings

from .config import settings


def get_chat_model() -> BaseChatModel:
    if settings.provider == "azure":
        return AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_openai_chat_deployment,
            streaming=True,
        )
    return ChatOpenAI(model=settings.openai_chat_model, api_key=settings.openai_api_key, streaming=True)


def get_embeddings() -> Embeddings:
    if settings.provider == "azure":
        return AzureOpenAIEmbeddings(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_openai_embedding_deployment,
        )
    return OpenAIEmbeddings(model=settings.openai_embedding_model, api_key=settings.openai_api_key)
