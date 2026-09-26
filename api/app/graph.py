from langchain_core.tools import tool
from langchain_postgres import PGVector
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.prebuilt import create_react_agent

from .config import settings
from .llm import get_chat_model

# Name of the model node in create_react_agent; chat streaming filters on it.
AGENT_NODE = "agent"

SYSTEM_PROMPT = """You are a helpful assistant.
The user may have uploaded documents. When a question could relate to those documents,
call the `retrieve_documents` tool first. When you use retrieved content, cite the source
filename(s) in your answer, e.g. (source: report.pdf, page 3). If nothing relevant is found,
answer from general knowledge and say that the documents did not cover it.
Format answers in Markdown."""

SYSTEM_PROMPT_NO_RAG = "You are a helpful assistant. Format answers in Markdown."


def build_graph(vectorstore: PGVector | None, checkpointer: BaseCheckpointSaver):
    @tool
    def retrieve_documents(query: str) -> str:
        """Search the user's uploaded documents (PDF/TXT/MD) for passages relevant to the query.
        Use this whenever the question may be answered by the uploaded documents."""
        docs = vectorstore.similarity_search(query, k=settings.retrieval_k)
        if not docs:
            return "No uploaded documents matched this query."
        parts = []
        for d in docs:
            label = d.metadata.get("source", "unknown")
            if d.metadata.get("page"):
                label += f", page {d.metadata['page']}"
                if d.metadata.get("page_end"):
                    label += f"-{d.metadata['page_end']}"
            parts.append(f"[source: {label}]\n{d.page_content}")
        return "\n\n---\n\n".join(parts)

    return create_react_agent(
        get_chat_model(),
        tools=[retrieve_documents] if vectorstore is not None else [],
        prompt=SYSTEM_PROMPT if vectorstore is not None else SYSTEM_PROMPT_NO_RAG,
        checkpointer=checkpointer,
    )
