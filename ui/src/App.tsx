import { useCallback, useEffect, useRef, useState } from "react";
import {
  ChatMessage,
  Conversation,
  DocumentInfo,
  deleteConversation,
  deleteDocument,
  getHealth,
  getMessages,
  listConversations,
  listDocuments,
  streamChat,
  uploadDocument,
} from "./api/client";
import Sidebar from "./components/Sidebar";
import ChatWindow from "./components/ChatWindow";
import Composer from "./components/Composer";

export default function App() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [activeId, setActiveId] = useState<string>();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [toast, setToast] = useState<string>();
  const [ragEnabled, setRagEnabled] = useState(true);
  const abortRef = useRef<AbortController>();

  const notify = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(undefined), 4000);
  }, []);

  const refreshConversations = useCallback(
    () => listConversations().then(setConversations).catch((e) => notify(e.message)),
    [notify],
  );
  const refreshDocuments = useCallback(
    () => listDocuments().then(setDocuments).catch((e) => notify(e.message)),
    [notify],
  );

  useEffect(() => {
    getHealth().then((h) => setRagEnabled(h.rag_enabled)).catch(() => undefined);
    refreshConversations();
    refreshDocuments();
  }, [refreshConversations, refreshDocuments]);

  const updateLastAssistant = (fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((prev) => [...prev.slice(0, -1), fn(prev[prev.length - 1])]);

  async function send(text: string) {
    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    setMessages((prev) => [...prev, { role: "user", content: text }, { role: "assistant", content: "" }]);
    try {
      await streamChat(
        text,
        activeId,
        {
          meta: (id) => {
            if (id !== activeId) setActiveId(id);
            refreshConversations();
          },
          token: (t) => updateLastAssistant((m) => ({ ...m, content: m.content + t })),
          done: () => refreshConversations(),
          error: (msg) => updateLastAssistant((m) => ({ ...m, error: msg })),
        },
        controller.signal,
      );
    } catch (e) {
      if (!controller.signal.aborted) {
        updateLastAssistant((m) => ({ ...m, error: (e as Error).message }));
      }
    } finally {
      setStreaming(false);
      abortRef.current = undefined;
    }
  }

  async function selectConversation(id: string) {
    if (streaming) return;
    setActiveId(id);
    setSidebarOpen(false);
    try {
      setMessages(await getMessages(id));
    } catch (e) {
      notify((e as Error).message);
    }
  }

  function newChat() {
    if (streaming) return;
    setActiveId(undefined);
    setMessages([]);
    setSidebarOpen(false);
  }

  async function removeConversation(id: string) {
    if (!window.confirm("Delete this conversation?")) return;
    try {
      await deleteConversation(id);
      if (id === activeId) newChat();
      refreshConversations();
    } catch (e) {
      notify((e as Error).message);
    }
  }

  async function upload(file: File) {
    const doc = await uploadDocument(file);
    notify(`Uploaded ${doc.source} (${doc.chunks} chunks)`);
    refreshDocuments();
  }

  async function removeDocument(doc: DocumentInfo) {
    if (!window.confirm(`Delete ${doc.source} from the knowledge base?`)) return;
    try {
      await deleteDocument(doc.document_id);
      refreshDocuments();
    } catch (e) {
      notify((e as Error).message);
    }
  }

  return (
    <div className={`app ${sidebarOpen ? "sidebar-open" : ""}`}>
      <Sidebar
        conversations={conversations}
        documents={documents}
        activeId={activeId}
        ragEnabled={ragEnabled}
        onNewChat={newChat}
        onSelect={selectConversation}
        onDelete={removeConversation}
        onUpload={upload}
        onDeleteDocument={removeDocument}
        onUploadError={notify}
      />
      <div className="scrim" onClick={() => setSidebarOpen(false)} />
      <main className="main">
        <header className="topbar">
          <button className="icon-btn menu-btn" aria-label="Toggle sidebar" onClick={() => setSidebarOpen((o) => !o)}>
            ☰
          </button>
          <h1>{conversations.find((c) => c.id === activeId)?.title ?? "New chat"}</h1>
        </header>
        <ChatWindow messages={messages} streaming={streaming} />
        <Composer streaming={streaming} onSend={send} onStop={() => abortRef.current?.abort()} />
      </main>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
