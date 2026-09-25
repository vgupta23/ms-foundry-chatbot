import { useRef, useState } from "react";

interface Props {
  onUpload(file: File): Promise<void>;
  onError(message: string): void;
}

export default function UploadButton({ onUpload, onError }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<string>();

  async function handle(file: File | undefined) {
    if (!file) return;
    setBusy(file.name);
    try {
      await onUpload(file);
    } catch (e) {
      onError(`Upload failed: ${(e as Error).message}`);
    } finally {
      setBusy(undefined);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <>
      <input
        ref={input}
        type="file"
        accept=".pdf,.txt,.md"
        hidden
        onChange={(e) => handle(e.target.files?.[0])}
      />
      <button className="upload" disabled={!!busy} onClick={() => input.current?.click()}>
        {busy ? (
          <>
            <span className="spinner" /> Indexing {busy}…
          </>
        ) : (
          "Upload document"
        )}
      </button>
    </>
  );
}
