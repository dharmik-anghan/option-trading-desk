/**
 * The horizontal layout every pane under a candle chart shares.
 *
 * An oscillator pane is drawn beneath the candles and must put each bar at the
 * same x, so both read their width and side gutters from here. Two copies of
 * these numbers is how the panes drift out of line with the candles.
 */
export const PANE_WIDTH = 1000;
export const PANE_LEFT = 6;
/** Room for the price axis on the right. */
export const PANE_RIGHT = 54;
