// Fyers' unrealized P&L (and sums of it) frequently carry floating-point
// noise (e.g. 942.5000000000017), which is a data artifact of float math,
// not a real number of paise/cents. Round for display everywhere.
export function formatPnl(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return rounded >= 0 ? `+${rounded}` : `${rounded}`;
}

export function formatNumber(value: number): number {
  return Math.round(value * 100) / 100;
}
