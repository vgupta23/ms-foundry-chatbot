import { KeyboardEvent, useState } from "react";

interface Props {
  streaming: boolean;
  onSend(text: string): void;
  onStop(): void;
}

export default function Composer({ streaming, onSend, onStop }: Props) {
  const [text, setText] = useState("");

  function submit() {
    const value = text.trim();
    if (!value || streaming) return;
    onSend(value);
    setText("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="composer">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Message the assistant…  (Shift+Enter for a new line)"
        rows={1}
        disabled={streaming}
        aria-label="Message"
      />
      {streaming ? (
        <button className="send stop" onClick={onStop}>
          Stop
        </button>
      ) : (
        <button className="send" onClick={submit} disabled={!text.trim()}>
          Send
        </button>
      )}
    </div>
  );
}
