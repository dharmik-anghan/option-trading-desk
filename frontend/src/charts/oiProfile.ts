import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";

/** Open interest at one strike, both sides, now and at the previous close. */
export interface OiRow {
  strike: number;
  call: number;
  callPrev: number;
  put: number;
  putPrev: number;
}

export interface OiColours {
  /** Ink for the bars, faded here. */
  ink: string;
  /** The heaviest strike on each side: the wall. */
  wall: string;
}

type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/** Widest the profile gets, in pixels, and its share of the pane at most. */
const MAX_W = 180;
const SHARE = 0.24;
/** Room left of the price axis: the wall lines put their short titles here. */
const INSET = 34;
/** Thinnest a row may be before neighbouring strikes are merged. */
const MIN_ROW = 5;

/**
 * Open interest by strike, drawn against the price axis it belongs to.
 *
 * Calls and puts sit either side of a spine rather than in two colours: the
 * desk tells the sides apart by position, never by hue. Each bar is today's
 * OI; the part added since yesterday's close is darker, and the part taken off
 * is left as an outline where the bar used to reach - so a wall being built
 * and one being unwound look different at a glance.
 *
 * Drawn behind the candles, because the newest bars share its corner and a
 * candle hidden under a profile is a price nobody can read.
 */
export class OiProfile implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private readonly view: IPrimitivePaneView;
  private readonly rows: readonly OiRow[];
  private readonly colours: OiColours;

  constructor(rows: readonly OiRow[], colours: OiColours) {
    this.rows = rows;
    this.colours = colours;
    this.view = {
      zOrder: () => "bottom",
      renderer: () => ({ draw: (target: Target) => this.draw(target) }),
    };
  }

  attached({ series }: SeriesAttachedParameter<Time>): void {
    this.series = series;
  }

  detached(): void {
    this.series = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  private draw(target: Target): void {
    const series = this.series;
    if (series === null || !this.rows.length) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      const width = Math.min(MAX_W, mediaSize.width * SHARE);
      const spine = mediaSize.width - INSET - width / 2;
      const half = width / 2 - 2;
      const rows = this.grouped(series);
      const most = Math.max(1, ...rows.flatMap((r) => [r.call, r.callPrev, r.put, r.putPrev]));
      const scale = (oi: number) => (oi / most) * half;

      const ys = rows.map((r) => series.priceToCoordinate(r.strike));
      // Bars as thick as the gap between rows allows, and no thicker.
      let gap = Infinity;
      for (let i = 1; i < ys.length; i += 1) {
        const a = ys[i - 1];
        const b = ys[i];
        if (a !== null && b !== null) gap = Math.min(gap, Math.abs(a - b));
      }
      const thick = Math.max(2, Math.min(14, Number.isFinite(gap) ? gap * 0.75 : 6));

      const heaviestCall = Math.max(...rows.map((r) => r.call));
      const heaviestPut = Math.max(...rows.map((r) => r.put));

      ctx.save();
      rows.forEach((row, i) => {
        const y = ys[i];
        if (y === null || y < -thick || y > mediaSize.height + thick) return;
        const top = y - thick / 2;
        // Puts to the left of the spine, calls to the right.
        this.bar(ctx, spine, -1, top, thick, scale(row.put), scale(row.putPrev), row.put === heaviestPut);
        this.bar(ctx, spine, 1, top, thick, scale(row.call), scale(row.callPrev), row.call === heaviestCall);
      });

      ctx.strokeStyle = fade(this.colours.ink, 0.35);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(Math.round(spine) + 0.5, 0);
      ctx.lineTo(Math.round(spine) + 0.5, mediaSize.height);
      ctx.stroke();

      ctx.fillStyle = fade(this.colours.ink, 0.6);
      ctx.font = "9px system-ui, sans-serif";
      ctx.textBaseline = "top";
      ctx.textAlign = "right";
      ctx.fillText("PE", spine - 4, 4);
      ctx.textAlign = "left";
      ctx.fillText("CE", spine + 4, 4);
      ctx.restore();
    });
  }

  /**
   * The rows, merged into wider bands when strikes sit too close to tell apart.
   *
   * On a daily chart fifty points is a pixel or two, and a profile of hairlines
   * says nothing. Neighbouring strikes are summed into bands of a whole number
   * of strikes until each band is at least `MIN_ROW` pixels tall.
   */
  private grouped(series: ISeriesApi<SeriesType, Time>): OiRow[] {
    const rows = this.rows;
    let step = Infinity;
    for (let i = 1; i < rows.length; i += 1) {
      step = Math.min(step, rows[i].strike - rows[i - 1].strike);
    }
    if (!Number.isFinite(step) || step <= 0) return [...rows];
    const a = series.priceToCoordinate(rows[0].strike);
    const b = series.priceToCoordinate(rows[0].strike + step);
    if (a === null || b === null) return [...rows];
    const n = Math.max(1, Math.ceil(MIN_ROW / Math.max(0.01, Math.abs(a - b))));
    if (n === 1) return [...rows];
    const band = step * n;
    const bands = new Map<number, OiRow>();
    for (const r of rows) {
      const at = Math.round(r.strike / band) * band;
      const sum = bands.get(at) ?? { strike: at, call: 0, callPrev: 0, put: 0, putPrev: 0 };
      sum.call += r.call;
      sum.callPrev += r.callPrev;
      sum.put += r.put;
      sum.putPrev += r.putPrev;
      bands.set(at, sum);
    }
    return [...bands.values()].sort((x, y) => x.strike - y.strike);
  }

  /** One side of one strike. `dir` is -1 for left of the spine, 1 for right. */
  private bar(
    ctx: CanvasRenderingContext2D,
    spine: number,
    dir: 1 | -1,
    top: number,
    thick: number,
    now: number,
    prev: number,
    wall: boolean,
  ): void {
    const base = wall ? fade(this.colours.wall, 0.6) : fade(this.colours.ink, 0.3);
    const kept = Math.min(now, prev);
    const span = (from: number, to: number) => {
      const a = spine + dir * from;
      const b = spine + dir * to;
      return [Math.min(a, b), Math.abs(b - a)] as const;
    };

    const [x, w] = span(0, kept);
    ctx.fillStyle = base;
    ctx.fillRect(x, top, w, thick);
    if (now > prev) {
      const [ax, aw] = span(prev, now);
      ctx.fillStyle = wall ? fade(this.colours.wall, 0.95) : fade(this.colours.ink, 0.55);
      ctx.fillRect(ax, top, aw, thick);
    } else if (prev > now) {
      const [rx, rw] = span(now, prev);
      ctx.strokeStyle = fade(this.colours.ink, 0.5);
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 2]);
      ctx.strokeRect(rx + 0.5, top + 0.5, Math.max(0, rw - 1), Math.max(0, thick - 1));
      ctx.setLineDash([]);
    }
  }
}

/** A hex colour at some opacity. Anything else is returned as it came. */
export function fade(colour: string, alpha: number): string {
  if (/^#[0-9a-f]{6}$/i.test(colour)) {
    return colour + Math.round(alpha * 255).toString(16).padStart(2, "0");
  }
  return colour;
}
