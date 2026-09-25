import { Conversation, DocumentInfo } from "../api/client";
import UploadButton from "./UploadButton";

interface Props {
  conversations: Conversation[];
  documents: DocumentInfo[];
  activeId?: string;
  ragEnabled: boolean;
  onNewChat(): void;
  onSelect(id: string): void;
  onDelete(id: string): void;
  onUpload(file: File): Promise<void>;
  onDeleteDocument(doc: DocumentInfo): void;
  onUploadError(message: string): void;
}

function relativeTime(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export default function Sidebar(p: Props) {
  return (
    <aside className="sidebar">
      <button className="new-chat" onClick={p.onNewChat}>
        + New chat
      </button>

      <nav className="conversations" aria-label="Conversations">
        {p.conversations.length === 0 && <p className="muted small">No conversations yet</p>}
        {p.conversations.map((c) => (
          <div key={c.id} className={`conv ${c.id === p.activeId ? "active" : ""}`}>
            <button className="conv-main" onClick={() => p.onSelect(c.id)} title={c.title}>
              <span className="conv-title">{c.title}</span>
              <span className="conv-time">{relativeTime(c.updated_at)}</span>
            </button>
            <button className="icon-btn danger" aria-label="Delete conversation" onClick={() => p.onDelete(c.id)}>
              ×
            </button>
          </div>
        ))}
      </nav>

      <section className="documents">
        <h2>Documents</h2>
        <ul>
          {p.documents.length === 0 && <li className="muted small">None uploaded</li>}
          {p.documents.map((d) => (
            <li key={d.document_id} className="doc">
              <span className="doc-name" title={`${d.source} · ${d.chunks} chunks`}>
                {d.source}
              </span>
              <button className="icon-btn danger" aria-label={`Delete ${d.source}`} onClick={() => p.onDeleteDocument(d)}>
                ×
              </button>
            </li>
          ))}
        </ul>
        {p.ragEnabled ? (
          <UploadButton onUpload={p.onUpload} onError={p.onUploadError} />
        ) : (
          <p className="muted small">Upload disabled: no embedding model is configured on the server.</p>
        )}
      </section>
    </aside>
  );
}
