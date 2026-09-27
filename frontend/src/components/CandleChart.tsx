import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Candle } from "../api";

interface Props {
  candles: readonly Candle[];
  /** Identifies the series. The pan/zoom window resets when this changes and
      survives when it does not - so a refresh that adds a bar leaves the view
      where you put it, while switching symbol or timeframe starts fresh. */
  seriesId: string;
  /** Live price, drawn as the current level so the chart agrees with the tile. */
  last: number | null;
  /** Decimal places the venue quotes in, so the axis invents no precision. */
  dp: number;
  height?: number;
  /** Things drawn on top of the price: a backtest's entry and exit, its stop and
      target, and the stretch of time the position was held. Optional, because a
      live chart has none of them. */
  overlay?: Overlay;
  /** Told the window on screen and the bar under the cursor, so a panel drawn
      underneath can show the same bars and the same moment. Without it an
      oscillator draws the whole series while the candles are zoomed into a
      corner of it, and the two disagree about which bar is which. */
  onView?: (view: { start: number; end: number; hovered: number | null }) => void;
}

export interface Overlay {
  /** Horizontal lines at a price, each with a short label on the axis. */
  levels?: { price: number; label: string; kind: "entry" | "exit" | "stop" | "target" }[];
  /** A point in time and price: where a position was opened or closed. */
  marks?: { at: string; price: number; kind: "entry" | "exit"; side: "long" | "short" }[];
  /** A level that existed between two moments rather than across the chart —
      a broken swing runs from where it was set to where it was taken, and
      drawing it full width states it at times it had not happened. */
  segments?: {
    from: string;
    to: string;
    price: number;
    label: string;
    kind: "entry" | "exit" | "stop" | "target";
  }[];
  /** The stretch a position was held over, shaded. */
  band?: { from: string; to: string };
  /** Indicator lines, one value per candle, null where not yet defined. */
  lines?: { label: string; values: (number | null)[] }[];
}

const PAD = { top: 8, right: 54, bottom: 18, left: 6 };

/** Fewest bars worth showing. Below this the chart is a magnifying glass. */
const MIN_BARS = 12;

/** Empty space past the last bar, as a fraction of the window.
 *
 * Every trading chart leaves some. Without it the newest bar is jammed against
 * the price axis, there is nowhere to draw a level ahead of price, and - the
 * thing that actually gets noticed - the chart cannot be dragged at all when it
 * is showing the whole series, because there is nothing either side to drag it
 * towards. */
const RIGHT_MARGIN = 0.3;

/** How far the price axis may be stretched or squeezed by hand, as a multiple
    of the range that fits the bars on screen. */
const PRICE_ZOOM = { min: 0.15, max: 8 };

/**
 * Candles, drawn as candles.
 *
 * A line would be smaller and less honest: the range within a bar is the part a
 * level-watcher cares about, because a wick through 24,000 is a test of 24,000
 * whether or not the bar closed there.
 *
 * Direction uses the same two hues as P&L. That is the one place the desk's
 * colour law bends, and deliberately: up and down mean the same thing to a
 * reader here as they do on a position, and inventing a third pair for price
 * would make the screen say that they are different ideas.
 */
/** A time worth putting under a crosshair: the day and the minute, no more. */
function when(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function Reading({
  label,
  value,
  dp,
  tone,
}: {
  label: string;
  value: number;
  dp: number;
  tone?: "up" | "dn";
}) {
  return (
    <span className="ohlc">
      <em>{label}</em>
      <b className={tone}>{value.toFixed(dp)}</b>
    </span>
  );
}

export function CandleChart({
  candles,
  seriesId,
  last,
  dp,
  height = 260,
  overlay,
  onView,
}: Props) {
  // The window, as a count of bars and where it ends. Held as an end index so
  // that new bars arriving keep the view pinned to the right, which is what
  // anyone watching a live chart expects - anchoring on the start would have the
  // latest price walk off the edge.
  const [bars, setBars] = useState<number | null>(null);
  const [end, setEnd] = useState<number | null>(null);
  const drag = useRef<{ x: number; end: number } | null>(null);
  // How much of the price axis to show, as a multiple of the range the bars on
  // screen need. One is the range itself; larger flattens the chart and smaller
  // magnifies the moves. Horizontal zoom answers "how much history", and this
  // answers "how big is a move" - two different questions, and a chart that
  // only ever auto-fits the price can answer neither, because every window
  // looks equally volatile when it is always scaled to its own extremes.
  const [priceZoom, setPriceZoom] = useState(1);
  const scaling = useRef<{ y: number; zoom: number } | null>(null);
  // The drawing area, measured rather than assumed. The viewBox is fixed and
  // the element scales to fit it, so a box taller than the viewBox's aspect
  // letterboxes: the candles sit in a band with empty space above and below,
  // which is exactly what the structure panel looked like. Measuring lets the
  // viewBox match the box, so the chart fills whatever it is given.
  const box = useRef<SVGSVGElement | null>(null);
  const [measured, setMeasured] = useState<number | null>(null);
  // The bar under the cursor, as an index into the whole series, and where the
  // cursor sits vertically as a fraction of the plot. Both null when the pointer
  // is elsewhere, which is what hides the crosshair.
  const [hovered, setHovered] = useState<number | null>(null);
  const [cursorRatio, setCursorRatio] = useState<number | null>(null);
  // Mirrored in state because the cursor depends on it and a ref must not be
  // read during render.
  const [dragging, setDragging] = useState(false);

  // Geometry first, because the pointer handlers need it to say which bar is
  // under the cursor. The viewBox is fixed and the element is scaled to fit, so
  // everything here is a fraction of the element rather than a pixel of it.
  const W = 1000;
  // Scaled to the viewBox's own units: the element is W units wide however many
  // pixels that is, so a box of 780x500 pixels is 1000x641 units.
  const H = measured ?? height;
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  const total = candles.length;
  const showing = Math.max(MIN_BARS, Math.min(bars ?? total, total));
  // How far past the last bar the window may be pushed. The window keeps its
  // width at both ends rather than shrinking into the edge, so panning to the
  // start of the series shows a full screen of bars beginning at the first.
  const margin = Math.round(showing * RIGHT_MARGIN);
  const endIndex = Math.max(
    Math.min(showing, total),
    Math.min(end ?? total, total + margin),
  );
  const startIndex = Math.max(0, endIndex - showing);
  // Only the bars that exist. The window can reach past them, which is what
  // leaves the empty space on the right.
  const shown = useMemo(
    () => candles.slice(startIndex, Math.min(endIndex, total)),
    [candles, startIndex, endIndex, total],
  );
  const fitted = bars === null && end === null;

  // A changed series - new symbol, new timeframe - resets the window: keeping a
  // 20-bar window across a switch from 1d to 5m shows twenty of the wrong bars.
  // Adjusted during render rather than in an effect, so there is no frame drawn
  // with the old window against the new data. Keyed on the series and not on the
  // bar count, because the count changes every time a bar closes and that would
  // throw away a pan the moment the chart refreshed.
  const [shownSeries, setShownSeries] = useState(seriesId);
  if (shownSeries !== seriesId) {
    setShownSeries(seriesId);
    setBars(null);
    setEnd(null);
  }

  const zoom = useCallback(
    (factor: number, anchorRatio = 1) => {
      setBars((current) => {
        const from = current ?? total;
        const next = Math.max(MIN_BARS, Math.min(total, Math.round(from * factor)));
        // Keep the bar under the cursor where it is, so zooming reads as moving
        // closer rather than jumping somewhere else.
        setEnd((currentEnd) => {
          const e = currentEnd ?? total;
          const anchor = e - from + from * anchorRatio;
          const room = total + Math.round(next * RIGHT_MARGIN);
          return Math.max(
            Math.min(next, total),
            Math.min(room, Math.round(anchor + next * (1 - anchorRatio))),
          );
        });
        return next;
      });
    },
    [total],
  );

  /** True when the pointer is over the price axis rather than the plot. */
  const overAxis = (event: { clientX: number }, box: DOMRect) =>
    box.width > 0 && (event.clientX - box.left) / box.width > (PAD.left + plotW) / W;

  const stretch = (factor: number) =>
    setPriceZoom((z) => Math.min(PRICE_ZOOM.max, Math.max(PRICE_ZOOM.min, z * factor)));

  const onWheel = (event: React.WheelEvent<SVGSVGElement>) => {
    if (!total) return;
    event.preventDefault();
    const box = event.currentTarget.getBoundingClientRect();
    // Over the axis, the wheel is about price rather than about history. That
    // is where every charting package puts it, and it is the only place on the
    // chart where the gesture is unambiguous.
    if (overAxis(event, box)) {
      stretch(event.deltaY > 0 ? 1.15 : 1 / 1.15);
      return;
    }
    const ratio = box.width ? (event.clientX - box.left) / box.width : 1;
    zoom(event.deltaY > 0 ? 1.25 : 0.8, Math.min(1, Math.max(0, ratio)));
  };

  const onPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!total) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    if (overAxis(event, event.currentTarget.getBoundingClientRect())) {
      scaling.current = { y: event.clientY, zoom: priceZoom };
      return;
    }
    drag.current = { x: event.clientX, end: endIndex };
    setDragging(true);
  };

  /** Back to the range the bars need. Double-click, as everywhere else. */
  const onDoubleClick = (event: React.MouseEvent<SVGSVGElement>) => {
    if (overAxis(event, event.currentTarget.getBoundingClientRect())) setPriceZoom(1);
  };

  const onPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const held = drag.current;
    const box = event.currentTarget.getBoundingClientRect();

    const stretching = scaling.current;
    if (stretching !== null) {
      if (!box.height) return;
      // Down squeezes the axis and up magnifies it, which is the direction the
      // hand expects: dragging the scale down pulls the extremes in towards the
      // middle of the chart.
      const moved = (event.clientY - stretching.y) / box.height;
      setPriceZoom(
        Math.min(PRICE_ZOOM.max, Math.max(PRICE_ZOOM.min, stretching.zoom * Math.exp(moved * 2))),
      );
      return;
    }

    if (held === null) {
      // Not dragging: track the cursor. Measured against the plot rather than the
      // whole element, so the bar under the pointer is the bar the pointer looks
      // like it is over rather than one offset by the axis.
      if (!box.width || !box.height || !shown.length) return;
      const acrossPlot =
        ((event.clientX - box.left) / box.width - PAD.left / W) / (plotW / W);
      const bar = Math.floor(acrossPlot * showing);
      setHovered(bar >= 0 && startIndex + bar < total ? startIndex + bar : null);
      const downPlot =
        ((event.clientY - box.top) / box.height - PAD.top / H) / (plotH / H);
      setCursorRatio(downPlot >= 0 && downPlot <= 1 ? downPlot : null);
      return;
    }

    if (!box.width) return;
    // Bars per pixel, so a drag moves the chart by what is under the finger
    // rather than by an arbitrary step.
    const moved = ((held.x - event.clientX) / box.width) * showing;
    setBars(showing);
    setEnd(
      Math.max(
        Math.min(showing, total),
        Math.min(total + margin, Math.round(held.end + moved)),
      ),
    );
  };

  const onPointerLeave = () => {
    setHovered(null);
    setCursorRatio(null);
  };

  const endDrag = (event: React.PointerEvent<SVGSVGElement>) => {
    drag.current = null;
    scaling.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  useEffect(() => {
    const element = box.current;
    if (element === null || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(() => {
      const rect = element.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const units = Math.round((rect.height / rect.width) * W);
      // Bounded: a collapsed panel would otherwise produce a viewBox of a few
      // units and a chart of solid ink.
      setMeasured(Math.max(120, Math.min(1400, units)));
    });
    watch.observe(element);
    return () => watch.disconnect();
  }, []);

  useEffect(() => {
    onView?.({ start: startIndex, end: endIndex, hovered });
    // `onView` is deliberately not a dependency: a parent that rebuilds the
    // callback each render would otherwise make this fire forever.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startIndex, endIndex, hovered]);

  const view = useMemo(() => {
    if (!shown.length) return null;
    const lows = shown.map((c) => c.low);
    const highs = shown.map((c) => c.high);
    // Indicator values count towards the range. A 200-period average sitting
    // below every bar on screen would otherwise be drawn off the bottom, which
    // reads as the line not being there at all.
    const drawn: number[] = [];
    for (const line of overlay?.lines ?? []) {
      for (let i = startIndex; i < endIndex; i += 1) {
        const v = line.values[i];
        if (typeof v === "number") drawn.push(v);
      }
    }
    let min = Math.min(...lows, ...drawn, last ?? Infinity);
    let max = Math.max(...highs, ...drawn, last ?? -Infinity);
    if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
    if (min === max) {
      // A flat window still needs a band, or every bar collapses onto one line.
      min -= Math.abs(min) * 0.001 || 1;
      max += Math.abs(max) * 0.001 || 1;
    }
    const pad = (max - min) * 0.06;
    // Stretched about the middle, so the bars stay where they are and only the
    // amount of price on either side of them changes.
    const mid = (max + min) / 2;
    const half = (max - min) / 2 + pad;
    return { min: mid - half * priceZoom, max: mid + half * priceZoom };
  }, [shown, last, overlay, startIndex, endIndex, priceZoom]);

  if (view === null) {
    return <p className="empty">No candles yet.</p>;
  }

  const y = (price: number) =>
    PAD.top + plotH - ((price - view.min) / (view.max - view.min)) * plotH;
  // Bars share the width of the *window*, not of the bars that exist in it -
  // which is what turns the unused end of the window into empty space rather
  // than stretching the last few candles across it.
  const step = plotW / showing;
  const bodyW = Math.max(1, step * 0.8);

  // Where a moment falls on the x axis. A timestamp that is not one of the bars
  // on screen is placed at the bar containing it, because a trade is filled at a
  // bar's open and the label belongs on that bar rather than between two.
  const xOf = (at: string): number | null => {
    const want = Date.parse(at);
    if (Number.isNaN(want)) return null;
    let index = -1;
    for (let i = 0; i < shown.length; i += 1) {
      if (Date.parse(shown[i].at) <= want) index = i;
      else break;
    }
    if (index < 0) return null;
    return PAD.left + index * step + step / 2;
  };

  const marks = (overlay?.marks ?? [])
    .map((mark) => ({ ...mark, x: xOf(mark.at) }))
    .filter((mark): mark is typeof mark & { x: number } => mark.x !== null);

  const band = (() => {
    if (!overlay?.band) return null;
    const from = xOf(overlay.band.from);
    const to = xOf(overlay.band.to);
    if (from === null) return null;
    const right = to ?? PAD.left + plotW;
    return { x: from, width: Math.max(1, right - from) };
  })();

  // Four gridlines: enough to read a level off, few enough not to be a net.
  const ticks = [0, 1, 2, 3, 4].map((i) => view.min + ((view.max - view.min) * i) / 4);

  // The bar under the cursor, as an offset into what is on screen, and the price
  // the cursor is level with. Both only exist while the pointer is over the plot.
  const hoverAt = hovered !== null ? hovered - startIndex : null;
  const onBar = hoverAt !== null && hoverAt >= 0 && hoverAt < shown.length ? shown[hoverAt] : null;
  const hoverX = hoverAt !== null ? PAD.left + hoverAt * step + step / 2 : null;
  const cursorPrice =
    cursorRatio === null ? null : view.max - cursorRatio * (view.max - view.min);

  return (
    <div className="candlewrap">
      <div className="reading">
        {onBar ? (
          <>
            <span className="when">{when(onBar.at)}</span>
            <Reading label="O" value={onBar.open} dp={dp} />
            <Reading label="H" value={onBar.high} dp={dp} />
            <Reading label="L" value={onBar.low} dp={dp} />
            <Reading
              label="C"
              value={onBar.close}
              dp={dp}
              tone={onBar.close >= onBar.open ? "up" : "dn"}
            />
            {(overlay?.lines ?? []).map((line, n) => {
              // Indexed by where the bar sits in the series, not by where it
              // sits on screen. `line.values` runs the whole series, so reading
              // it at the window offset showed the value from the first bars of
              // the series whenever the chart had been panned or zoomed.
              const value = hovered === null ? null : line.values[hovered];
              return typeof value === "number" ? (
                <span key={line.label} className={`ind i${n % 5}`}>
                  {line.label} {value.toFixed(dp)}
                </span>
              ) : null;
            })}
          </>
        ) : (
          <span className="dim">Hover a candle to read it.</span>
        )}
      </div>

      <div className="zoom">
        <button
          className="xbtn"
          onClick={() => zoom(1.25)}
          disabled={showing >= total}
          aria-label="Show more bars"
          title="Show more bars"
        >
          &minus;
        </button>
        <button
          className="xbtn"
          onClick={() => zoom(0.8)}
          disabled={showing <= MIN_BARS}
          aria-label="Show fewer bars"
          title="Show fewer bars"
        >
          +
        </button>
        <button
          className="xbtn"
          onClick={() => {
            setBars(null);
            setEnd(null);
            setPriceZoom(1);
          }}
          disabled={fitted && priceZoom === 1}
        >
          Fit
        </button>
        <span className="dim">
          {fitted ? `all ${total} bars` : `${showing} of ${total} bars`}
        </span>
        <span className="sp" />
        <span className="dim">
          drag to pan · scroll to zoom · drag the price axis to stretch it
        </span>
      </div>
      <svg
      ref={box}
      className={dragging ? "candles dragging" : "candles"}
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label="Price candles"
      onWheel={onWheel}
      onDoubleClick={onDoubleClick}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onPointerLeave={onPointerLeave}
    >
      {ticks.map((price) => (
        <g key={price}>
          <line x1={PAD.left} x2={PAD.left + plotW} y1={y(price)} y2={y(price)} className="cgrid" />
          <text x={PAD.left + plotW + 5} y={y(price) + 3.5} className="caxis">
            {price.toFixed(dp)}
          </text>
        </g>
      ))}

      {/* Under the candles, so a shaded holding period never hides a bar. */}
      {band !== null && (
        <rect
          x={band.x}
          y={PAD.top}
          width={band.width}
          height={plotH}
          className="cheld"
        />
      )}

      {/* A level that only existed between two moments. Clamped to the plot so
          a break whose origin scrolled off the left still starts at the edge
          rather than vanishing. */}
      {(overlay?.segments ?? []).map((seg, n) => {
        const from = xOf(seg.from) ?? PAD.left;
        const to = xOf(seg.to) ?? PAD.left + plotW;
        return (
          <g key={`${n}-${seg.from}-${seg.to}`} className={`clevel ${seg.kind}`}>
            <line x1={from} x2={to} y1={y(seg.price)} y2={y(seg.price)} />
            <text x={from + 3} y={y(seg.price) - 4} className="clevellabel">
              {seg.label}
            </text>
          </g>
        );
      })}

      {(overlay?.levels ?? []).map((level) => (
        <g key={`${level.kind}-${level.price}`} className={`clevel ${level.kind}`}>
          <line x1={PAD.left} x2={PAD.left + plotW} y1={y(level.price)} y2={y(level.price)} />
          <text x={PAD.left + 4} y={y(level.price) - 4} className="clevellabel">
            {level.label}
          </text>
        </g>
      ))}

      {shown.map((c, i) => {
        const cx = PAD.left + i * step + step / 2;
        const rising = c.close >= c.open;
        const top = y(Math.max(c.open, c.close));
        const bottom = y(Math.min(c.open, c.close));
        return (
          <g key={c.at} className={rising ? "up" : "dn"}>
            <line x1={cx} x2={cx} y1={y(c.high)} y2={y(c.low)} className="cwick" />
            <rect
              x={cx - bodyW / 2}
              y={top}
              width={bodyW}
              // A doji has no body; give it a hairline so the bar still exists.
              height={Math.max(1, bottom - top)}
              className="cbody"
            />
          </g>
        );
      })}

      {/* Over the candles, because a line hidden behind a wick is not a line.
          Slowest first, which `StrategySpec.indicators` already orders them by,
          so a fast line crossing a slow one stays legible. */}
      {(overlay?.lines ?? []).map((line, n) => {
        const path: string[] = [];
        let drawing = false;
        for (let i = 0; i < shown.length; i += 1) {
          // `startIndex + i`, because `values` runs the whole series while `i`
          // runs the window. Drawn at `i` alone, a panned chart plotted the
          // indicator's opening values over its closing bars - invisible at
          // full fit, which is why it survived, and wrong everywhere else.
          const value = line.values[startIndex + i];
          if (typeof value !== "number") {
            // A gap, not a jump to zero: the indicator had no value here.
            drawing = false;
            continue;
          }
          const px = PAD.left + i * step + step / 2;
          path.push(`${drawing ? "L" : "M"}${px.toFixed(1)} ${y(value).toFixed(1)}`);
          drawing = true;
        }
        return (
          <path key={line.label} d={path.join(" ")} className={`cline i${n % 5}`} />
        );
      })}

      {/* Over the candles: the two moments that matter most on this chart. */}
      {marks.map((mark) => (
        <g key={`${mark.kind}-${mark.at}`} className={`cmark ${mark.kind} ${mark.side}`}>
          <circle cx={mark.x} cy={y(mark.price)} r={4.5} />
          <text x={mark.x} y={y(mark.price) + (mark.kind === "entry" ? -9 : 15)}>
            {mark.kind === "entry" ? (mark.side === "long" ? "buy" : "sell") : "close"}
          </text>
        </g>
      ))}

      {/* The crosshair. Under the price label below so the two do not fight, and
          over the candles so it can be followed across them. */}
      {hoverX !== null && (
        <line x1={hoverX} x2={hoverX} y1={PAD.top} y2={PAD.top + plotH} className="cross" />
      )}
      {cursorPrice !== null && (
        <>
          <line
            x1={PAD.left}
            x2={PAD.left + plotW}
            y1={y(cursorPrice)}
            y2={y(cursorPrice)}
            className="cross"
          />
          <rect
            x={PAD.left + plotW + 1}
            y={y(cursorPrice) - 8}
            width={PAD.right - 2}
            height={16}
            className="crossbg"
          />
          <text x={PAD.left + plotW + 5} y={y(cursorPrice) + 3.5} className="crosstext">
            {cursorPrice.toFixed(dp)}
          </text>
        </>
      )}
      {onBar !== null && hoverX !== null && (
        <text
          x={Math.min(Math.max(hoverX, PAD.left + 34), PAD.left + plotW - 34)}
          y={H - 5}
          className="crosswhen"
        >
          {when(onBar.at)}
        </text>
      )}

      {/* The price axis, as something you can grab. Invisible, but it is what
          turns "drag the axis to stretch it" from a line of help text into an
          affordance a cursor announces. */}
      <rect
        x={PAD.left + plotW}
        y={PAD.top}
        width={PAD.right}
        height={plotH}
        className="cscale"
      />

      {last !== null && (
        <g>
          <line
            x1={PAD.left}
            x2={PAD.left + plotW}
            y1={y(last)}
            y2={y(last)}
            className="clast"
          />
          <rect
            x={PAD.left + plotW + 1}
            y={y(last) - 8}
            width={PAD.right - 2}
            height={16}
            className="clastbg"
          />
          <text x={PAD.left + plotW + 5} y={y(last) + 3.5} className="clasttext">
            {last.toFixed(dp)}
          </text>
        </g>
      )}
      </svg>
    </div>
  );
}
