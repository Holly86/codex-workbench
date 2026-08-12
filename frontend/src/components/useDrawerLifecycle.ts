import { useEffect, useRef, useState } from "react";

const MOBILE_QUERY = "(max-width: 760px)";
const FOCUSABLE = [
  "a[href]", "button:not([disabled])", "input:not([disabled])", "select:not([disabled])",
  "textarea:not([disabled])", "[tabindex]:not([tabindex='-1'])",
].join(",");

export function useDrawerLifecycle(open: boolean, onClose: () => void) {
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const closeHandlerRef = useRef(onClose);
  const [mobile, setMobile] = useState(() =>
    typeof window.matchMedia === "function" && window.matchMedia(MOBILE_QUERY).matches,
  );

  useEffect(() => { closeHandlerRef.current = onClose; }, [onClose]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(MOBILE_QUERY);
    const changed = () => setMobile(media.matches);
    changed();
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    queueMicrotask(() => closeRef.current?.focus());
    return () => { if (opener?.isConnected) opener.focus(); };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const appShell = document.querySelector<HTMLElement>(".app-shell");
    if (mobile && appShell !== null) appShell.inert = true;
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeHandlerRef.current();
        return;
      }
      if (!mobile || event.key !== "Tab" || drawerRef.current === null) return;
      const focusable = [...drawerRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
        .filter((item) => !item.hasAttribute("disabled") && item.getAttribute("aria-hidden") !== "true");
      if (focusable.length === 0) { event.preventDefault(); drawerRef.current.focus(); return; }
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first?.focus();
      }
    };
    window.addEventListener("keydown", keydown);
    return () => {
      window.removeEventListener("keydown", keydown);
      if (appShell !== null) appShell.inert = false;
    };
  }, [mobile, open]);

  return { mobile, drawerRef, closeRef };
}
