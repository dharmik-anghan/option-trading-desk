import { useEffect, useRef, useState } from "react";

interface Props {
  label: string;
  value: number | null;
  step: number;
  /** Allow a blank field, committed as null - "no level set". Without this a
      blank means "leave it alone", which is right for a threshold that must
      always have a value and wrong for a level you want to remove. */
  nullable?: boolean;
  /** Allow a negative number. A stop is a loss and reads as one. */
  signed?: boolean;
  placeholder?: string;
  /** Called once, when the edit is finished. */
  onCommit: (value: number | null) => void;
}

/**
 * A number you can actually type into.
 *
 * The obvious version - value from the server, onChange straight to a PUT - is
 * unusable, and was: a request per keystroke, an empty field parsed as zero and
 * saved as zero, a poll landing mid-edit and snapping the field back to what the
 * server still had, and the backend rejecting zero with a 422 that only reached
 * the console. Between them you could not change a threshold at all.
 *
 * So the draft is local and the commit is deliberate: on blur or Enter, once,
 * with a parsed and checked number. Escape abandons it. The value from above is
 * only adopted while you are not the one editing - otherwise the poll is fighting
 * the keyboard, and the keyboard should win.
 */
export function ThresholdInput({
  label,
  value,
  step,
  nullable = false,
  signed = false,
  placeholder,
  onCommit,
}: Props) {
  const text = (v: number | null) => (v === null ? "" : String(v));
  const [draft, setDraft] = useState(text(value));
  const editing = useRef(false);

  // Adopt a new value from above only when it is not being typed over. A poll
  // every twenty seconds against a field mid-edit is how "3" became "30" became
  // "3" again.
  useEffect(() => {
    if (!editing.current) setDraft(text(value));
    // `text` is recreated each render and is a pure formatter, so it is not a
    // dependency worth tracking.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const commit = () => {
    editing.current = false;
    const blank = !draft.trim();
    if (blank) {
      // Clearing a level removes it; clearing a threshold that must have a value
      // means "leave it alone", because zero would silently never fire.
      if (nullable && value !== null) onCommit(null);
      else setDraft(text(value));
      return;
    }
    const parsed = Number(draft);
    // Zero is refused either way: indistinguishable from no level, and it would
    // fire the moment a figure ticked past break-even.
    if (!Number.isFinite(parsed) || parsed === 0) {
      setDraft(text(value));
      return;
    }
    const next = signed ? parsed : Math.abs(parsed);
    if (next !== value) onCommit(next);
  };

  return (
    <input
      type="number"
      step={step}
      min={signed ? undefined : 0}
      value={draft}
      aria-label={label}
      placeholder={placeholder}
      onFocus={() => {
        editing.current = true;
      }}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          commit();
          e.currentTarget.blur();
        }
        if (e.key === "Escape") {
          editing.current = false;
          setDraft(text(value));
          e.currentTarget.blur();
        }
      }}
    />
  );
}
