import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";

interface AnchoredMenuProps {
  anchorRef: RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  ariaLabel: string;
  className?: string;
  width?: number;
  preferredPlacement?: "top" | "bottom";
  align?: "start" | "end";
  role?: "menu" | "dialog";
}

interface MenuPosition {
  left: number;
  top?: number;
  bottom?: number;
  width: number;
  maxHeight: number;
}

const VIEWPORT_GUTTER = 12;
const ANCHOR_GAP = 8;

export function AnchoredMenu({
  anchorRef,
  open,
  onClose,
  children,
  ariaLabel,
  className = "",
  width = 288,
  preferredPlacement = "bottom",
  align = "start",
  role = "menu",
}: AnchoredMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<MenuPosition | null>(null);

  useLayoutEffect(() => {
    if (!open) {
      return;
    }

    const updatePosition = () => {
      const anchor = anchorRef.current;
      if (anchor === null) return;
      const rect = anchor.getBoundingClientRect();
      const menuWidth = Math.min(width, window.innerWidth - VIEWPORT_GUTTER * 2);
      const idealLeft = align === "end" ? rect.right - menuWidth : rect.left;
      const left = Math.max(
        VIEWPORT_GUTTER,
        Math.min(idealLeft, window.innerWidth - menuWidth - VIEWPORT_GUTTER),
      );
      const availableAbove = rect.top - VIEWPORT_GUTTER - ANCHOR_GAP;
      const availableBelow = window.innerHeight - rect.bottom - VIEWPORT_GUTTER - ANCHOR_GAP;
      const placeAbove = preferredPlacement === "top"
        ? availableAbove >= Math.min(220, availableBelow)
        : availableBelow < 220 && availableAbove > availableBelow;

      setPosition(placeAbove
        ? {
            left,
            bottom: window.innerHeight - rect.top + ANCHOR_GAP,
            width: menuWidth,
            maxHeight: Math.max(120, availableAbove),
          }
        : {
            left,
            top: rect.bottom + ANCHOR_GAP,
            width: menuWidth,
            maxHeight: Math.max(120, availableBelow),
          });
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [align, anchorRef, open, preferredPlacement, width]);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (menuRef.current?.contains(target) || anchorRef.current?.contains(target)) return;
      onClose();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        anchorRef.current?.focus();
        return;
      }
      if (event.key === "Tab") {
        onClose();
        return;
      }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      const items = Array.from(menuRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [role="menuitem"]:not([aria-disabled="true"]), [role="menuitemradio"]:not([aria-disabled="true"]), a[href], input:not([disabled])',
      ) ?? []);
      if (items.length === 0) return;
      event.preventDefault();
      const current = items.indexOf(document.activeElement as HTMLElement);
      let next = 0;
      if (event.key === "End") next = items.length - 1;
      else if (event.key === "ArrowUp") next = current <= 0 ? items.length - 1 : current - 1;
      else if (event.key === "ArrowDown") next = current < 0 || current === items.length - 1 ? 0 : current + 1;
      items[next]?.focus();
    };

    document.addEventListener("pointerdown", handlePointerDown, true);
    document.addEventListener("keydown", handleKeyDown);
    const focusTimer = window.setTimeout(() => {
      const firstItem = menuRef.current?.querySelector<HTMLElement>(
        'button:not([disabled]), [role="menuitem"], [role="menuitemradio"], input:not([disabled])',
      );
      (firstItem ?? menuRef.current)?.focus();
    });
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown, true);
      document.removeEventListener("keydown", handleKeyDown);
      window.clearTimeout(focusTimer);
    };
  }, [anchorRef, onClose, open]);

  if (!open || position === null) return null;
  const style: CSSProperties = {
    left: position.left,
    top: position.top,
    bottom: position.bottom,
    width: position.width,
    maxHeight: position.maxHeight,
  };

  return createPortal(
    <div
      ref={menuRef}
      className={`anchored-menu ${className}`.trim()}
      role={role}
      aria-label={ariaLabel}
      tabIndex={-1}
      style={style}
    >
      {children}
    </div>,
    document.body,
  );
}
