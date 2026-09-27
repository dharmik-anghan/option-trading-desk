interface Props {
  onClick: () => void;
  /** Where it goes, for the tooltip and for anyone listening rather than looking. */
  to?: string;
}

/**
 * Up one level.
 *
 * Not a button in a box. It was one, and the box made the least important
 * control on the desk the heaviest thing in the corner — a bordered square
 * sitting louder than the desk's own name beside it, and reading as a stray
 * form control rather than as a way back.
 *
 * A chevron instead, in the quiet colour, tucked against whatever it precedes so
 * the two read as one cluster: "← Crypto Desk". It nudges left on hover, which
 * is the whole affordance; a border would only say a second time what the arrow
 * already says.
 */
export function BackButton({ onClick, to = "the desks" }: Props) {
  return (
    <button className="uphome" onClick={onClick} title={`Back to ${to}`} aria-label={`Back to ${to}`}>
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M10 3.2 5.2 8l4.8 4.8" />
      </svg>
    </button>
  );
}
