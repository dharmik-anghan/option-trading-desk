import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getRrg, getRrgOptions } from "../api";
import type { Quadrant, RrgOptions, RrgSnapshot } from "../api";
import { RrgChart } from "./rrg/RrgChart";

interface Props {
  onHome: () => void;
}

const QUADRANTS: { id: Quadrant; name: string; says: string }[] = [
  { id: "leading", name: "Leading", says: "ahead, and still pulling away" },
  { id: "weakening", name: "Weakening", says: "still ahead, but losing the lead" },
  { id: "lagging", name: "Lagging", says: "behind, and still falling back" },
  { id: "improving", name: "Improving", says: "behind, but catching up" },
];

/** How many periods of tail to offer. Five is a week of daily, a month of weekly. */
const TAILS = [3, 5, 8, 12, 20];

/** Milliseconds a replay spends on each period. */
const STEP_MS = 120;

/**
 * Relative rotation.
 *
 * Two numbers per security — is it beating the benchmark, and is that lead
 * growing — plotted against each other. Things travel round the quadrants
 * clockwise: a laggard starts improving, becomes a leader, weakens, lags again.
 *
 * The rotation is the point rather than the position. A sector at 101 and
 * climbing is a different proposition from one at 105 and falling, and a table
 * of relative returns says the same thing about both.
 *
 * Replay is a slider rather than a request per frame: the whole path comes back
 * in one response, so stepping back through it is local.
 */
export function Rrg({ onHome }: Props) {
  const [options, setOptions] = useState<RrgOptions | null>(null);
  const [indexId, setIndexId] = useState("SECTORS");
  const [timeframe, setTimeframe] = useState("daily");
  // Exposed rather than fixed, because they change the answer. The same sector
  // reads leading at a window of 14, improving at 21 and weakening on weekly
  // bars, from identical prices — so a graph that cannot be reconciled with
  // another one is a graph nobody can check.
  const [benchmark, setBenchmark] = useState("NIFTY50");
  const [window_, setWindow] = useState(14);
  const [tail, setTail] = useState(8);
  const [back, setBack] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<RrgSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    void getRrgOptions()
      .then(setOptions)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError(null);
    void getRrg({ index_id: indexId, timeframe, benchmark, window: window_ })
      .then((s) => {
        if (!current) return;
        setSnapshot(s);
        // A new graph starts at the present rather than wherever the last
        // replay was left, which would otherwise look like stale data.
        setBack(0);
        setPlaying(false);
      })
      .catch((e: unknown) => {
        if (!current) return;
        setError(e instanceof Error ? e.message : String(e));
        setSnapshot(null);
      })
      .finally(() => current && setLoading(false));
    return () => {
      current = false;
    };
  }, [indexId, timeframe, benchmark, window_]);

  // How far back replay can go: the shortest path, less the tail, so every
  // series still has something to draw at the far end.
  const furthest = useMemo(() => {
    if (!snapshot?.series.length) return 0;
    const shortest = Math.min(...snapshot.series.map((s) => s.path.length));
    return Math.max(0, shortest - tail - 1);
  }, [snapshot, tail]);

  // Driven by the frame clock rather than a timer. An interval fires whether or
  // not the last frame finished, so a heavy snapshot makes the steps bunch up -
  // which is what made replay feel like it was struggling rather than running.
  const frame = useRef<number | null>(null);
  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    const tick = (now: number) => {
      if (now - last >= STEP_MS) {
        last = now;
        setBack((b) => {
          if (b <= 0) {
            setPlaying(false);
            return 0;
          }
          return b - 1;
        });
      }
      frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [playing]);

  const replay = useCallback(() => {
    setBack(furthest);
    setPlaying(true);
  }, [furthest]);

  const shown = snapshot?.series ?? [];
  const at = useMemo(() => {
    const path = shown[0]?.path;
    if (!path?.length) return null;
    const point = path[Math.max(0, path.length - 1 - back)];
    return point ? point.at.slice(0, 10) : null;
  }, [shown, back]);

  // Grouped by where each one is *now* on screen, which during a replay is
  // where it was then — the whole point of stepping back.
  const grouped = useMemo(() => {
    const out: Record<Quadrant, { label: string; symbol: string }[]> = {
      leading: [],
      weakening: [],
      lagging: [],
      improving: [],
    };
    for (const s of shown) {
      const end = s.path.length - back;
      const head = s.path[end - 1];
      if (!head) continue;
      const where: Quadrant =
        head.ratio >= 100
          ? head.momentum >= 100
            ? "leading"
            : "weakening"
          : head.momentum >= 100
            ? "improving"
            : "lagging";
      out[where].push({ label: s.label, symbol: s.symbol });
    }
    return out;
  }, [shown, back]);

  return (
    <main className="rrgpage">
      <header>
        <button className="uphome" onClick={onHome} title="Back to the three desks">
          ←
        </button>
        <h1>Rotation</h1>
        <p>
          Where each one stands against {snapshot?.benchmark_name ?? "the benchmark"}, and
          which way it is heading. Things travel clockwise: a laggard starts improving,
          becomes a leader, weakens, lags again.
        </p>
      </header>

      {error && <p className="bad">{error}</p>}

      <div className="controls">
        <label>
          Plot
          <select value={indexId} onChange={(e) => setIndexId(e.target.value)}>
            {(options?.indices ?? []).map((i) => (
              <option key={i.id} value={i.id} disabled={i.plots === 0}>
                {i.name}
                {i.plots ? ` (${i.plots})` : " — members not fetched"}
              </option>
            ))}
          </select>
        </label>

        <label>
          Timeframe
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            <option value="daily">daily</option>
            <option value="weekly">weekly</option>
          </select>
        </label>

        <label>
          Against
          <select value={benchmark} onChange={(e) => setBenchmark(e.target.value)}>
            {(options?.benchmarks ?? []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </label>

        <label>
          Window
          <input
            className="win"
            type="number"
            min={options?.window_min ?? 5}
            max={options?.window_max ?? 60}
            value={window_}
            onChange={(e) => {
              const lo = options?.window_min ?? 5;
              const hi = options?.window_max ?? 60;
              setWindow(Math.min(hi, Math.max(lo, Number(e.target.value))));
            }}
            title="Periods each number is normalised over. It moves things between quadrants."
          />
        </label>

        <label>
          Tail
          <select value={tail} onChange={(e) => setTail(Number(e.target.value))}>
            {TAILS.map((t) => (
              <option key={t} value={t}>
                {t} {timeframe === "weekly" ? "weeks" : "days"}
              </option>
            ))}
          </select>
        </label>

        <div className="replay">
          <button onClick={playing ? () => setPlaying(false) : replay} disabled={!furthest}>
            {playing ? "Pause" : "Replay"}
          </button>
          <input
            type="range"
            min={0}
            max={furthest}
            value={furthest - back}
            onChange={(e) => {
              setPlaying(false);
              setBack(furthest - Number(e.target.value));
            }}
            disabled={!furthest}
          />
          <small>{at ?? "—"}</small>
        </div>
      </div>

      {loading && !snapshot && <p className="empty">Reading the store…</p>}

      {snapshot && (
        <div className="rrgbody">
          <RrgChart
            series={shown}
            tail={tail}
            back={back}
            hovered={hovered}
            onHover={setHovered}
          />

          <div className="standings">
            {QUADRANTS.map((q) => (
              <section key={q.id} className={`stand ${q.id}`}>
                <h3>
                  {q.name} <span>{grouped[q.id].length}</span>
                </h3>
                <p className="says">{q.says}</p>
                <ul>
                  {grouped[q.id].map((m) => (
                    <li
                      key={m.symbol}
                      className={hovered === m.symbol ? "on" : ""}
                      onMouseEnter={() => setHovered(m.symbol)}
                      onMouseLeave={() => setHovered(null)}
                    >
                      {m.label}
                    </li>
                  ))}
                  {grouped[q.id].length === 0 && <li className="none">nothing here</li>}
                </ul>
              </section>
            ))}
          </div>
        </div>
      )}

      {snapshot?.caveats.map((c) => (
        <p className="caveat" key={c}>
          {c}
        </p>
      ))}

      <p className="note">
        Both numbers are how far something stands from its own recent normal, in standard
        deviations, recentred on 100 — the ratio measured on relative strength, the momentum
        on the ratio. The original formula is Julius de Kempenaer's and its constants are not
        published, so this is the usual reconstruction: same rotation, same quadrants, not
        the same decimals as a vendor's chart.
      </p>
    </main>
  );
}
