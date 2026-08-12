import { cloneElement, isValidElement, useEffect, useId, useRef } from "react";
import type { ReactElement, ReactNode } from "react";

export function DialogShell({
  open, title, description, tone = "default", className = "", children, footer, onClose,
}: {
  open: boolean; title: string; description?: string; tone?: "default" | "danger";
  className?: string; children: ReactNode; footer: ReactNode; onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const returnFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!open || dialog === null) return;
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    const focusTimer = window.setTimeout(() => {
      const initial = dialog.querySelector<HTMLElement>("[data-dialog-initial-focus]")
        ?? dialog.querySelector<HTMLElement>("input:not([disabled]), textarea:not([disabled]), select:not([disabled])")
        ?? dialog.querySelector<HTMLElement>("button:not([disabled])");
      initial?.focus();
    });
    return () => {
      window.clearTimeout(focusTimer);
      if (dialog.open) dialog.close();
      returnFocus.current?.focus();
    };
  }, [open]);
  return (
    <dialog
      ref={ref}
      className={`dialog-shell is-${tone} ${className}`.trim()}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
    >
      <DialogHeader title={title} titleId={titleId} description={description} descriptionId={descriptionId} onClose={onClose} />
      <DialogBody>{children}</DialogBody>
      <DialogFooter>{footer}</DialogFooter>
    </dialog>
  );
}

export function DialogHeader({ title, titleId, description, descriptionId, onClose }: {
  title: string; titleId: string; description?: string; descriptionId: string; onClose: () => void;
}) {
  return <header className="dialog-header"><div><h2 id={titleId}>{title}</h2>{description ? <p id={descriptionId}>{description}</p> : null}</div><IconButton label="Dialog schließen" onClick={onClose}>×</IconButton></header>;
}
export function DialogBody({ children }: { children: ReactNode }) { return <div className="dialog-body">{children}</div>; }
export function DialogFooter({ children }: { children: ReactNode }) { return <footer className="dialog-footer">{children}</footer>; }

export function FormField({ label, description, error, children }: {
  label: string; description?: string; error?: string | null; children: ReactNode;
}) {
  const generatedId = useId();
  const descriptionId = `${generatedId}-description`;
  const errorId = `${generatedId}-error`;
  const child = isValidElement(children)
    ? children as ReactElement<{ id?: string; "aria-describedby"?: string; "aria-invalid"?: boolean }>
    : null;
  const controlId = child?.props.id ?? generatedId;
  const describedBy = [child?.props["aria-describedby"], description ? descriptionId : null, error ? errorId : null]
    .filter(Boolean).join(" ") || undefined;
  const control = child === null ? children : cloneElement(child, {
    id: controlId,
    "aria-describedby": describedBy,
    "aria-invalid": error ? true : undefined,
  });
  return <div className="form-field"><label className="field-label" htmlFor={controlId}>{label}</label>{control}{description ? <FieldDescription id={descriptionId}>{description}</FieldDescription> : null}{error ? <span id={errorId} className="field-error" role="alert">{error}</span> : null}</div>;
}
export function FieldDescription({ children, id }: { children: ReactNode; id?: string }) { return <small id={id} className="field-description">{children}</small>; }
export function ChoiceCard({ checked, label, description, onChange }: { checked: boolean; label: string; description: string; onChange: () => void }) {
  return <label className={`choice-card ${checked ? "is-selected" : ""}`}><input type="radio" name="choice-card" aria-label={label} checked={checked} onChange={onChange} /><span><strong>{label}</strong><small>{description}</small></span></label>;
}
export function CheckboxRow({ checked, label, onChange }: { checked: boolean; label: string; onChange: (checked: boolean) => void }) {
  return <label className="checkbox-row"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><span>{label}</span></label>;
}
export function StatusBadge({ state, children }: { state: string; children: ReactNode }) { return <span className={`status-badge is-${state}`}>{children}</span>; }
export function DefinitionList({ values }: { values: Array<{ label: string; value: ReactNode }> }) { return <dl className="definition-list">{values.map((value) => <div key={value.label}><dt>{value.label}</dt><dd>{value.value}</dd></div>)}</dl>; }
export function InlineAlert({ tone = "info", children }: { tone?: "info" | "warning" | "danger"; children: ReactNode }) { return <div className={`inline-alert is-${tone}`} role={tone === "danger" ? "alert" : "status"}>{children}</div>; }
export function EmptyState({ children }: { children: ReactNode }) { return <p className="empty-state">{children}</p>; }
export function PrimaryButton(props: React.ButtonHTMLAttributes<HTMLButtonElement>) { return <button type="button" className="primary-action" {...props} />; }
export function SecondaryButton(props: React.ButtonHTMLAttributes<HTMLButtonElement>) { return <button type="button" className="secondary-action" {...props} />; }
export function DangerButton(props: React.ButtonHTMLAttributes<HTMLButtonElement>) { return <button type="button" className="danger-action" {...props} />; }
export function IconButton({ label, children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) { return <button type="button" className="icon-button" aria-label={label} {...props}>{children}</button>; }
