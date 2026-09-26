import type { Venue } from "../api";

interface Props {
  venues: readonly Venue[];
  selected: string;
  onSelect: (id: string) => void;
}

/** What to call each desk. The venue's own name is what it trades, which is the
    useful label once there is more than one. */
const SHORT: Record<string, string> = {
  index_options: "Options",
  perpetuals: "Crypto",
};

/**
 * Which desk is on screen.
 *
 * Top left, before the brand, because it changes everything to the right of it:
 * the instruments, the panels, and the currency the numbers are in. A control
 * that reframes the whole screen should be the first thing read, not something
 * found later in a toolbar.
 *
 * Nothing is rendered for a single venue — a switch with one position is furniture.
 */
export function VenueSwitch({ venues, selected, onSelect }: Props) {
  if (venues.length < 2) return null;
  return (
    <div className="venuesw" role="tablist" aria-label="Desk">
      {venues.map((v) => (
        <button
          key={v.id}
          role="tab"
          aria-selected={v.id === selected}
          className={v.id === selected ? "on" : undefined}
          title={`${v.name} · priced in ${v.quote_currency}`}
          onClick={() => onSelect(v.id)}
        >
          {SHORT[v.asset_class] ?? v.name}
        </button>
      ))}
    </div>
  );
}
