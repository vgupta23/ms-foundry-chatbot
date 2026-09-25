// All calls use the relative /api base; nginx (or the Vite dev proxy) forwards them to the API.
const BASE = "/api";

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  error?: string;
}

export interface DocumentInfo {
  document_id: string;
  source: string;
  chunks: number;
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* not JSON */
  }
  return `HTTP ${res.status}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) throw new Error(await errorMessage(res));
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export interface Health {
  status: string;
  provider: string;
  rag_enabled: boolean;
}

export const getHealth = () => request<Health>("/healthz");
export const listConversations =() => request<Conversation[]>("/conversations");
export const getMessages = (id: string) => request<ChatMessage[]>(`/conversations/${id}/messages`);
export const deleteConversation = (id: string) => request<void>(`/conversations/${id}`, { method: "DELETE" });

export const listDocuments = () => request<DocumentInfo[]>("/documents");
export const deleteDocument = (id: string) => request<void>(`/documents/${id}`, { method: "DELETE" });
export function uploadDocument(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<DocumentInfo>("/documents", { method: "POST", body: form });
}

export interface StreamHandlers {
  meta(conversationId: string): void;
  token(text: string): void;
  done(): void;
  error(message: string): void;
}

// POST + SSE: EventSource only supports GET, so read the fetch body stream and parse `data:` events.
export async function streamChat(
  message: string,
  conversationId: string | undefined,
  on: StreamHandlers,
  signal?: AbortSignal,
) {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  });
  if (!res.ok || !res.body) return on.error(await errorMessage(res));

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
