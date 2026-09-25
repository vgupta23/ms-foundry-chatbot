import { ComponentPropsWithoutRef, useRef, useState } from "react";
import ReactMarkdown, { ExtraProps } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { ChatMessage } from "../api/client";

function CodeBlock({ node: _node, ...props }: ComponentPropsWithoutRef<"pre"> & ExtraProps) {
  const pre = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(pre.current?.innerText ?? "");
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="code-block">
      <button className="copy-btn" onClick={copy}>
        {copied ? "Copied" : "Copy"}
      </button>
      <pre ref={pre} {...props} />
    </div>
  );
}

interface Props {
  message: ChatMessage;
  pending: boolean;
}

export default function MessageBubble({ message, pending }: Props) {
  if (message.role === "user") {
    return <div className="msg user">{message.content}</div>;
  }
  return (
    <div className="msg assistant">
      {pending && <span className="typing" aria-label="Assistant is typing"><i /><i /><i /></span>}
      {/* Raw HTML is intentionally not rendered (no rehype-raw). */}
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={{ pre: CodeBlock }}>
        {message.content}
      </ReactMarkdown>
      {message.error && <div className="msg-error">⚠ {message.error}</div>}
    </div>
  );
}
