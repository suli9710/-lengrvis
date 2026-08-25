import { type KeyboardEvent, type ReactNode, type RefObject, useEffect, useRef } from "react";

interface AccessibleDialogProps {
  labelledBy: string;
  describedBy?: string;
  role?: "dialog" | "alertdialog";
  backdropClassName?: string;
  className?: string;
  closeDisabled?: boolean;
  initialFocusRef?: RefObject<HTMLElement | null>;
  returnFocusTo?: HTMLElement | null;
  children: ReactNode;
  onClose: () => void;
}

export function AccessibleDialog({
  labelledBy,
  describedBy,
  role = "dialog",
  backdropClassName = "modal-backdrop",
  className = "modal",
  closeDisabled = false,
  initialFocusRef,
  returnFocusTo,
  children,
  onClose
}: AccessibleDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocusedElement = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previouslyFocusedElement.current = returnFocusTo
      ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    const focusDialog = () => {
      const target = initialFocusRef?.current ?? firstFocusableElement(dialogRef.current) ?? dialogRef.current;
      target?.focus();
    };
    const timerId = window.setTimeout(focusDialog, 0);
    const keepFocusInside = (event: FocusEvent) => {
      const dialog = dialogRef.current;
      if (dialog && event.target instanceof Node && !dialog.contains(event.target)) {
        focusDialog();
      }
    };
    document.addEventListener("focusin", keepFocusInside);

    return () => {
      window.clearTimeout(timerId);
      document.removeEventListener("focusin", keepFocusInside);
      const previous = previouslyFocusedElement.current;
      if (previous?.isConnected) previous.focus();
      previouslyFocusedElement.current = null;
    };
  }, [initialFocusRef, returnFocusTo]);

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (!closeDisabled) onClose();
      return;
    }
    if (event.key === "Tab") trapFocus(event, dialogRef.current);
  };

  return (
    <div className={backdropClassName} role="presentation">
      <div
        className={className}
        role={role}
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        tabIndex={-1}
        ref={dialogRef}
        onKeyDown={handleKeyDown}
      >
        {children}
      </div>
    </div>
  );
}

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "[contenteditable='true']",
  "[tabindex]:not([tabindex='-1'])"
].join(",");

function focusableElements(container: HTMLElement | null): HTMLElement[] {
  if (!container) return [];
  return Array.from(container.querySelectorAll<HTMLElement>(focusableSelector)).filter((element) => {
    if (element.closest("[inert], [aria-hidden='true']")) return false;
    const style = window.getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
  });
}

function firstFocusableElement(container: HTMLElement | null): HTMLElement | null {
  return focusableElements(container)[0] ?? null;
}

function trapFocus(event: KeyboardEvent<HTMLDivElement>, container: HTMLElement | null) {
  const focusables = focusableElements(container);
  if (!focusables.length) {
    event.preventDefault();
    container?.focus();
    return;
  }

  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const active = document.activeElement;
  if (!container?.contains(active) || active === container) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  } else if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}
