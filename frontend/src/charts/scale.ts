/** The arithmetic every chart here shares: map a value onto pixels, join points into a path. */

/** A linear map from `[d0, d1]` onto `[r0, r1]`. A flat domain maps to its midpoint. */
export function linear(d0: number, d1: number, r0: number, r1: number): (v: number) => number {
  const span = d1 - d0;
  if (span === 0) return () => (r0 + r1) / 2;
  return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

/** An SVG path through the points, in order. */
export function linePath(xs: readonly number[], ys: readonly number[]): string {
  return xs.map((x, i) => `${i ? "L" : "M"}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
}

export interface Pad {
  top: number;
  right: number;
  bottom: number;
  left: number;
}
