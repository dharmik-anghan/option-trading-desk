import type { Comparison, Compare, Group, Operand } from "../../api";
import { OperandPicker } from "./OperandPicker";

interface Props {
  label: string;
  hint: string;
  value: Group | null;
  onChange: (next: Group | null) => void;
  intervals: readonly string[];
  traded: string;
}

const OPS: { id: Comparison; name: string }[] = [
  { id: "crosses_above", name: "crosses above" },
  { id: "crosses_below", name: "crosses below" },
  { id: "above", name: "is above" },
  { id: "below", name: "is below" },
  { id: "equals", name: "equals" },
];

function rowsOf(group: Group | null): Compare[] {
  if (!group) return [];
  return group.all ?? group.any ?? [];
}

function joinerOf(group: Group | null): "all" | "any" {
  return group && group.any ? "any" : "all";
}

/**
 * A list of comparisons, joined one way or the other.
 *
 * One level deep rather than an arbitrary tree. Nesting is what makes a builder
 * unusable, and "all of these" or "any of these" covers nearly everything anyone
 * writes - the exception is worth typing out by hand rather than paying for on
 * every screen.
 *
 * An empty list means the side is off, not that it always fires. Leaving the
 * short entry empty is how you build a long-only strategy.
 */
export function ConditionList({ label, hint, value, onChange, intervals, traded }: Props) {
  const rows = rowsOf(value);
  const joiner = joinerOf(value);

  const write = (next: Compare[], as: "all" | "any" = joiner) => {
    if (!next.length) return onChange(null);
    onChange(as === "all" ? { all: next } : { any: next });
  };

  const add = () =>
    write([
      ...rows,
      {
        left: { kind: "indicator", name: "ema", length: 9 },
        op: "crosses_above",
        right: { kind: "indicator", name: "ema", length: 21 },
      },
    ]);

  const replace = (at: number, row: Compare) =>
    write(rows.map((r, i) => (i === at ? row : r)));

  return (
    <section className="conds">
      <header>
        <h3>{label}</h3>
        {rows.length > 1 && (
          <select
            value={joiner}
            onChange={(e) => write(rows, e.target.value as "all" | "any")}
            title="Whether every line has to be true, or any one of them"
          >
            <option value="all">all of</option>
            <option value="any">any of</option>
          </select>
        )}
        <span className="sp" />
        <button onClick={add}>Add</button>
      </header>

      {rows.length === 0 ? (
        <p className="off">{hint}</p>
      ) : (
        <ol>
          {rows.map((row, i) => (
            <li key={i}>
              <OperandPicker
                value={row.left}
                onChange={(left: Operand) => replace(i, { ...row, left })}
                intervals={intervals}
                traded={traded}
              />
              <select
                value={row.op}
                onChange={(e) => replace(i, { ...row, op: e.target.value as Comparison })}
              >
                {OPS.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
              <OperandPicker
                value={row.right}
                onChange={(right: Operand) => replace(i, { ...row, right })}
                intervals={intervals}
                traded={traded}
              />
              <button
                className="drop"
                onClick={() => write(rows.filter((_, k) => k !== i))}
                title="Remove this line"
              >
                ×
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
