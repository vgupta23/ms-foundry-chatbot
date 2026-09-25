import { useEffect, useRef } from "react";
import { ChatMessage } from "../api/client";
import MessageBubble from "./MessageBubble";

interface Props {
  messages: ChatMessage[];
  streaming: boolean;
}

export default function ChatWindow({ messages, streaming }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  // Keep following new tokens unless the user has scrolled up.
  useEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  function onScroll() {
    const el = scroller.current;
    if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  if (messages.length === 0) {
    return (
      <div className="chat empty">
        <div>
          <h2>How can I help?</h2>
          <p className="muted">Ask anything, or upload documents in the sidebar and ask questions about them.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="chat" ref={scroller} onScroll={onScroll}>
      <div className="chat-inner">
        {messages.map((m, i) => (
          <MessageBubble
            key={i}
            message={m}
            pending={streaming && i === messages.length - 1 && m.role === "assistant" && !m.content}
          />
        ))}
      </div>
    </div>
  );
}
