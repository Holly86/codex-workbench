import { useEffect, useRef, useState } from "react";

interface CopyButtonProps {
  text: string;
  label?: string;
  className?: string;
}

export function CopyButton({ text, label = "In Zwischenablage kopieren", className = "" }: CopyButtonProps) {
  const [state, setState] = useState<"idle" | "copied" | "error">("idle");
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
  }, []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("error");
    }
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = window.setTimeout(() => setState("idle"), 1800);
  };

  return (
    <button type="button" className={`copy-action ${className}`.trim()} onClick={() => void copy()}
      aria-label={state === "copied" ? "Kopiert" : state === "error" ? "Kopieren fehlgeschlagen" : label}>
      <span aria-hidden="true">{state === "copied" ? "✓" : state === "error" ? "!" : "⧉"}</span>
      <span className="copy-label" aria-live="polite">{state === "copied" ? "Kopiert" : state === "error" ? "Fehler" : "Kopieren"}</span>
    </button>
  );
}
