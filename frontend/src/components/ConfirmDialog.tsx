import { useEffect, useRef, type ReactNode } from "react";

interface Props {
  title: string;
  go: string;
  /** Blocks the confirm button while the form inside is incomplete. */
  disabled?: boolean;
  children: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * An in-page confirm, not window.confirm — a native dialog blocks the event
 * loop, which stalls the polling that keeps the rest of the desk truthful.
 */
export function ConfirmDialog({ title, go, disabled, children, onConfirm, onCancel }: Props) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Held in a ref so the effects below never need it as a dependency. Callers
  // pass an inline arrow - `onCancel={() => setOpen(false)}` - which is a new
  // function on every render of the parent, and the parent re-renders on every
  // poll. Depending on it re-ran both effects every few seconds.
  const cancelRef = useRef(onCancel);
  useEffect(() => {
    cancelRef.current = onCancel;
  });

  // Once, on open. Re-running this stole focus back to the confirm button
  // every few seconds, which made a dialog containing a text field impossible
  // to type into - you got a word or two in and the caret vanished.
  //
  // A dialog that asks for something opens on the thing it asks for. One that
  // only asks whether opens on Cancel, deliberately: these confirm squaring
  // off a position or deleting a record, and the destructive button should not
  // arrive pre-armed for a stray Enter.
  useEffect(() => {
    const field = bodyRef.current?.querySelector<HTMLElement>(
      "input:not([type=hidden]), select, textarea",
    );
    (field ?? cancelButtonRef.current)?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") cancelRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div
      className="ask"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div className="askbox">
        <h3>{title}</h3>
        <div className="body" ref={bodyRef}>
          {children}
        </div>
        <div className="askrow">
          <button onClick={onCancel} ref={cancelButtonRef}>
            Cancel
          </button>
          <button className="go" onClick={onConfirm} disabled={disabled}>
            {go}
          </button>
        </div>
      </div>
    </div>
  );
}
