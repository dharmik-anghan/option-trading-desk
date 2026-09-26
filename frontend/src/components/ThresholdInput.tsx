import { useEffect, useRef, useState } from "react";

interface Props {
  label: string;
  value: number;
  step: number;
  /** Called once, with a valid number, when the edit is finished. */
  onCommit: (value: number) => void;
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
export function ThresholdInput({ label, value, step, onCommit }: Props) {
  const [draft, setDraft] = useState(String(value));
  const editing = useRef(false);

  // Adopt a new value from above only when it is not being typed over. A poll
  // every twenty seconds against a field mid-edit is how "3" became "30" became
  // "3" again.
  useEffect(() => {
    if (!editing.current) setDraft(String(value));
  }, [value]);

  const commit = () => {
    editing.current = false;
    const parsed = Number(draft);
    // A blank or unparseable field means "leave it alone", not "set it to zero" -
    // and zero is refused by the backend anyway, since a threshold of zero would
    // silently never fire.
    if (!draft.trim() || !Number.isFinite(parsed) || parsed <= 0) {
      setDraft(String(value));
      return;
    }
    if (parsed !== value) onCommit(Math.abs(parsed));
  };

  return (
    <input
      type="number"
      step={step}
      min={0}
      value={draft}
      aria-label={label}
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
          setDraft(String(value));
          e.currentTarget.blur();
        }
      }}
    />
  );
}
