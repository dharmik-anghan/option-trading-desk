import type { Route } from "../useRoute";

interface Props {
  onGo: (route: Route) => void;
  /** Whether the perpetuals venue is configured, so a dead card is not offered. */
  cryptoReady: boolean;
}

/**
 * Where to start.
 *
 * Three desks rather than one screen with a switch, because they are three different
 * jobs: watching structures you hold, trading a leveraged book, and asking what a
 * rule would have done. The first two are live and the third reads the same bars
 * they leave behind.
 *
 * The artwork is drawn here rather than downloaded. A stock photograph of a trading
 * floor would be somebody else's work with an unclear licence, baked into this
 * repository and fetched over the network - and it would say nothing. These say what
 * each desk is: a payoff kink, a candle series, an equity curve.
 */
export function Home({ onGo, cryptoReady }: Props) {
  return (
    <main className="home">
      <header>
        <h1>Desk</h1>
        <p>
          Four places to work. Two watch live positions, one asks what a rule would have
          done with the bars they left behind, and one shows which way the market's parts
          are turning.
        </p>
      </header>

      <div className="cards">
        <button className="card" onClick={() => onGo("options")}>
          <PayoffMark />
          <h2>Options</h2>
          <p>
            NIFTY, BANKNIFTY and the rest through Fyers. Structures you hold, their
            greeks and payoff, the chain, and the calendar.
          </p>
          <span className="go">Open the options desk</span>
        </button>

        <button className="card" onClick={() => onGo("crypto")} disabled={!cryptoReady}>
          <CandlesMark />
          <h2>Crypto &amp; commodities</h2>
          <p>
            Bitcoin, gold and crude as perpetuals on Shark. Streamed prices, positions
            with their liquidation distance, and orders.
          </p>
          <span className="go">
            {cryptoReady ? "Open the crypto desk" : "Needs SHARK_API_KEY in .env"}
          </span>
        </button>

        <button className="card" onClick={() => onGo("rotation")}>
          <RotationMark />
          <h2>Rotation</h2>
          <p>
            Which sectors are leading, improving, weakening and lagging against the
            index — and which way each one is travelling.
          </p>
          <span className="go">Open the rotation graph</span>
        </button>

        <button className="card" onClick={() => onGo("backtesting")}>
          <EquityMark />
          <h2>Backtesting</h2>
          <p>
            What a rule would have done. Reads the bars the desks have stored, so a run
            can only cover history that actually exists.
          </p>
          <span className="go">Open backtesting</span>
        </button>
      </div>
    </main>
  );
}

/** A payoff kink: the shape an options structure is read by. */
function PayoffMark() {
  return (
    <svg viewBox="0 0 120 56" className="mark" aria-hidden="true">
      <line x1="0" y1="40" x2="120" y2="40" className="markgrid" />
      <path d="M2 50 L38 50 L62 14 L86 14 L118 34" className="markline you" />
      <circle cx="62" cy="14" r="2.5" className="markdot" />
    </svg>
  );
}

/** Candles: what a leveraged book is watched on. */
function CandlesMark() {
  const bars: [number, number, number, number][] = [
    // x, top, bottom, rising
    [10, 18, 40, 1],
    [28, 12, 30, 1],
    [46, 22, 44, 0],
    [64, 8, 26, 1],
    [82, 16, 38, 0],
    [100, 6, 22, 1],
  ];
  return (
    <svg viewBox="0 0 120 56" className="mark" aria-hidden="true">
      {bars.map(([x, top, bottom, rising]) => (
        <g key={x} className={rising ? "up" : "dn"}>
          <line x1={x} x2={x} y1={top - 5} y2={bottom + 5} className="markwick" />
          <rect x={x - 4} y={top} width="8" height={bottom - top} className="markbody" />
        </g>
      ))}
    </svg>
  );
}

/** Four quadrants and something rotating through them. */
function RotationMark() {
  return (
    <svg viewBox="0 0 120 56" className="mark" aria-hidden="true">
      <line x1="60" y1="4" x2="60" y2="52" className="markgrid" />
      <line x1="10" y1="28" x2="110" y2="28" className="markgrid" />
      <path d="M30 42 C44 40, 52 34, 58 24" className="markline mkt" />
      <circle cx="58" cy="24" r="3" className="markdot" />
      <path d="M72 14 C84 18, 90 24, 94 34" className="markline you" />
      <circle cx="94" cy="34" r="3" className="markdot" />
    </svg>
  );
}

/** An equity curve, drawdown and all: what a backtest produces. */
function EquityMark() {
  return (
    <svg viewBox="0 0 120 56" className="mark" aria-hidden="true">
      <line x1="0" y1="44" x2="120" y2="44" className="markgrid" />
      <path
        d="M2 44 L16 38 L28 41 L42 30 L56 33 L70 20 L84 26 L98 14 L118 10"
        className="markline mkt"
      />
    </svg>
  );
}
