# UI (React + Vite + TypeScript)

## Setup

Scaffold with `npm create vite@latest ui -- --template react-ts`. Add these runtime dependencies:

```
react-markdown remark-gfm rehype-highlight highlight.js
```

Don't add a UI component library or global state library unless asked. Plain CSS and React state/hooks are enough for this scope.

`vite.config.ts` includes a dev proxy so `npm run dev` behaves the same way as the nginx container:

```ts
server: { proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } } }
```

## API client (`src/api/client.ts`)

- Every call uses the **relative** base `/api`. There is no `VITE_API_URL` and no absolute URLs.
- `POST /api/chat` is a POST request, so **`EventSource` can't be used**, because it only supports GET. Use `fetch` and read `response.body` with a `ReadableStream` reader and a `TextDecoder`. Split the buffer on `\n\n`, parse each `data: ` line as JSON, and keep any incomplete trailing fragment in the buffer:

```ts
export async function streamChat(
  message: string,
  conversationId: string | undefined,
  on: { meta(id: string): void; token(t: string): void; done(): void; error(m: string): void },
  signal?: AbortSignal,
) {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  });
  if (!res.ok || !res.body) return on.error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const events = buf.split("\n\n");
    buf = events.pop() ?? "";
    for (const ev of events) {
      const line = ev.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const msg = JSON.parse(line.slice(6));
      if (msg.type === "meta") on.meta(msg.conversation_id);
      else if (msg.type === "token") on.token(msg.content);
      else if (msg.type === "done") on.done();
      else if (msg.type === "error") on.error(msg.message);
    }
  }
}
```

Other wrappers are `listConversations`, `getMessages(id)`, `deleteConversation(id)`, `uploadDocument(file)` (a `FormData` with the field `file`), `listDocuments` and `deleteDocument(id)`.

## Layout and behaviour

- **Sidebar (left):** a "New chat" button and a list of conversations (title plus relative time) with the active one highlighted. Each has a delete control that asks for confirmation. Below the list is a "Documents" section listing the uploaded files, each with a delete control, and an Upload button that accepts `.pdf,.txt,.md`.
- **Chat window:** a scrollable message list that auto-scrolls to the bottom while tokens stream in, unless the user has scrolled up. It shows an empty state when there are no messages.
- **Message bubble:** user messages are shown as plain text. Assistant messages are rendered with `<ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>`, and a highlight.js theme is imported once. Code blocks get a copy button. Raw HTML is **not** rendered: don't enable `rehype-raw`.
- **Composer:** a textarea where Enter sends and Shift+Enter adds a newline. It is disabled while a response streams, and a **Stop** button aborts the stream through `AbortController`.
- **Streaming flow:** append the user message and an empty assistant message to the list. Each `token` event appends to the last assistant message. On `meta`, if this was a new chat, set the active conversation ID and refresh the sidebar. On `error`, show the error inline on that assistant message.
- **Switching conversations:** load the messages with `getMessages(id)`.
- **Upload flow:** show progress and busy state, and on success refresh the documents list and show a brief toast with the chunk count. Show the error message the server returns (for example a 413 or 422).
- Keep the layout responsive: the sidebar collapses behind a toggle below 768px. Support light and dark mode with `prefers-color-scheme`.

## Scripts

`package.json` keeps the Vite defaults (`dev`, `build`, `preview`). `npm run build` must pass the type check (`tsc --noEmit && vite build`). Commit `package-lock.json`.
