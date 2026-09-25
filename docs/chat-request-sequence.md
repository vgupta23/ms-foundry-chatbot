# Chat request sequence — function-level touchpoints

What happens, function by function, from the moment a user presses Enter in the UI until the streamed answer is complete. Source: [`chat-request-sequence.mmd`](diagrams/chat-request-sequence.mmd) · static image: [`chat-request-sequence.svg`](diagrams/chat-request-sequence.svg).

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    box rgb(238,240,255) ui container (React bundle + nginx)
        participant CMP as Composer.tsx
        participant APP as App.tsx
        participant CL as api/client.ts
        participant MB as ChatWindow.tsx /<br/>MessageBubble.tsx
        participant NG as nginx<br/>location /api/
    end
    box rgb(255,244,229) api container (FastAPI + LangGraph)
        participant CR as routers/chat.py<br/>chat() + event_stream()
        participant AG as graph.py<br/>create_react_agent graph
        participant CP as AsyncPostgresSaver<br/>(checkpointer)
        participant TL as retrieve_documents<br/>(@tool)
        participant VS as PGVector<br/>(vectorstore.py)
        participant LLM as llm.py<br/>AzureChatOpenAI /<br/>AzureOpenAIEmbeddings
    end
    participant DB as Postgres 16<br/>+ pgvector
    participant AZ as Azure AI Foundry<br/>(or OpenAI API)

    U->>CMP: types a message, presses Enter
    CMP->>CMP: onKeyDown() → submit()
    CMP->>APP: onSend(text) → send(text)
    APP->>APP: new AbortController(), setStreaming(true)
    APP->>MB: setMessages(+ user msg, + empty assistant msg)
    APP->>CL: streamChat(text, activeId, handlers, signal)
    CL->>NG: fetch POST /api/chat {message, conversation_id?}
    NG->>CR: proxy_pass http://api:8000 (proxy_buffering off)
    CR->>CR: validate ChatRequest (pydantic), Depends(get_graph), Depends(get_pool)
    CR->>CR: conv_id = conversation_id or uuid4(), title = first 60 chars
    CR->>DB: INSERT INTO conversations (id, title) ON CONFLICT DO NOTHING
    CR-->>NG: StreamingResponse(event_stream(), text/event-stream, X-Accel-Buffering no)
    CR-->>NG: sse() data: {"type":"meta","conversation_id":...}
    NG-->>CL: forwarded unbuffered
    CL->>APP: on.meta(id) → setActiveId(id), refreshConversations()
    Note over APP,NG: refreshConversations() → GET /api/conversations → list_conversations() → SELECT ... ORDER BY updated_at DESC

    CR->>AG: graph.astream({messages: [user msg]}, thread_id = conv_id, stream_mode = "messages")
    AG->>CP: aget_tuple(thread_id)
    CP->>DB: SELECT FROM checkpoints, checkpoint_blobs
    DB-->>CP: latest checkpoint
    CP-->>AG: prior messages of this conversation
    AG->>AG: agent node: SYSTEM_PROMPT + history + new user msg
    AG->>LLM: model.bind_tools([retrieve_documents]).astream(messages)
    LLM->>AZ: POST {root}/openai/deployments/{chat}/chat/completions?api-version=2024-10-21<br/>header api-key, stream=true, tools=[retrieve_documents]
    Note over LLM,AZ: OpenAI provider: ChatOpenAI → POST api.openai.com/v1/chat/completions, Authorization Bearer

    alt model decides the question needs the uploaded documents
        AZ-->>LLM: streamed tool_call retrieve_documents(query)
        LLM-->>AG: AIMessageChunk with tool_call_chunks (no text)
        AG-->>CR: (chunk, {langgraph_node: "agent"}) → text_of() is empty, nothing sent
        AG->>CP: aput() / aput_writes() step checkpoint
        CP->>DB: INSERT checkpoints, checkpoint_writes
        AG->>TL: tools node (ToolNode) → retrieve_documents(query) in worker thread
        TL->>VS: similarity_search(query, k = RETRIEVAL_K = 4)
        VS->>LLM: embeddings.embed_query(query)
        LLM->>AZ: POST {root}/openai/deployments/{embedding}/embeddings<br/>(OpenAI: POST /v1/embeddings)
        AZ-->>LLM: query embedding (1536 floats for text-embedding-3-small)
        LLM-->>VS: query vector
        VS->>DB: SELECT document, cmetadata FROM langchain_pg_embedding<br/>JOIN langchain_pg_collection (name = 'documents')<br/>ORDER BY cosine distance to query vector LIMIT 4
        DB-->>VS: top 4 chunks + metadata (source, page, document_id)
        VS-->>TL: list of Document
        TL-->>AG: ToolMessage "[source: file, page n] chunk text ..."
        AG->>CP: aput() checkpoint with tool result
        CP->>DB: INSERT checkpoints, checkpoint_writes
        AG->>AG: agent node again: history + tool call + ToolMessage
        AG->>LLM: astream(messages)
        LLM->>AZ: POST .../chat/completions (stream=true)
    else model answers directly, or RAG disabled (no embedding deployment, no tools bound)
        Note over AG,AZ: no tool call, the answer streams from the first completion request
    end

    loop for every streamed delta
        AZ-->>LLM: data: choices[0].delta.content
        LLM-->>AG: AIMessageChunk
        AG-->>CR: (chunk, {langgraph_node: "agent"})
        CR->>CR: isinstance AIMessageChunk, node == AGENT_NODE, text_of(content)
        CR-->>NG: sse() data: {"type":"token","content":"..."}
        NG-->>CL: forwarded unbuffered
        CL->>CL: reader.read() → TextDecoder → split on blank line → JSON.parse
        CL->>APP: on.token(text)
        APP->>MB: updateLastAssistant(content + text)
        MB-->>U: ReactMarkdown + remark-gfm + rehype-highlight re-render, auto-scroll
    end

    AZ-->>LLM: finish_reason = stop
    AG->>CP: aput() final state (user msg + AI answer)
    CP->>DB: INSERT checkpoints, checkpoint_blobs
    AG-->>CR: astream() completes
    CR-->>NG: sse() data: {"type":"done"}
    NG-->>CL: forwarded
    CL->>APP: on.done() → refreshConversations()
    CR->>DB: finally: UPDATE conversations SET updated_at = now()
    APP->>APP: finally: setStreaming(false), Composer re-enabled

    Note over CR,CL: On any exception: logger.exception() and data: {"type":"error","message":...} instead of done, then on.error() shows it under the assistant message
```

## Reading the diagram

| Phase | Steps | What happens |
| --- | --- | --- |
| UI send | 1–7 | `Composer` hands the text to `App.send()`, which adds the user message plus an empty assistant bubble and calls `streamChat()`. `fetch` POSTs `/api/chat` to nginx. |
| Request setup | 8–15 | nginx proxies to the API with buffering off. `chat()` validates the body, creates the conversation row if new, opens the SSE response and sends the `meta` event so the UI learns the conversation id. |
| Load memory | 16–20 | `graph.astream()` asks `AsyncPostgresSaver` for the thread's latest checkpoint, so the model sees the whole conversation. |
| First model call | 21–23 | The agent node sends system prompt + history + new message to Azure (`chat/completions`, `stream=true`) with the `retrieve_documents` tool definition attached. |
| Retrieval (alt) | 24–43 | If the model calls the tool: the query is embedded (Azure `embeddings`), pgvector returns the 4 nearest chunks by cosine distance, and the formatted chunks go back to the model as a `ToolMessage` for a second completion call. Tool-call chunks carry no text, so nothing reaches the UI in this phase. |
| Streaming | loop | Each `delta.content` becomes an `AIMessageChunk`. `event_stream()` forwards only `agent`-node text as `token` events. `client.ts` parses them and `App` appends to the last bubble, which `ReactMarkdown` re-renders. |
| Finish | last steps | The final state is checkpointed, `done` is sent, the sidebar refreshes, `updated_at` is bumped and the composer re-enables. |

Notes:

- **RAG disabled** (no `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, today's setup): the agent has no tools, so there is exactly one completion call and steps in the `alt` branch never occur.
- **OpenAI provider** (`LLM_PROVIDER=openai`): the same functions run; `llm.py` returns `ChatOpenAI` / `OpenAIEmbeddings`, which call `api.openai.com/v1/chat/completions` and `/v1/embeddings` with a Bearer token instead of the `api-key` header.
- **Stop button**: `AbortController.abort()` cancels the `fetch`; the API's `finally` block still updates `updated_at`.
