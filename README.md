# Option Desk

A personal, semi-automated options trading desk for Indian index options.
Fyers today, multi-broker later.

It is built around the positions you already hold rather than around order
entry: what they are worth, what they would be worth if the index moved, which
short is being tested, and what is scheduled to happen before they expire.

> **This connects to a live brokerage account.** The desk reads real positions
> and real funds. The order-placement UI has been removed for now, but
> `POST /api/orders/place` still exists and places real orders with real money.
> Treat the credentials in `.env` accordingly — see [Security](#security).

## What is on the screen

| | |
|---|---|
| **Top bar** | Spot and day change, the next scheduled event, booked / marked-to-market / net P&L, and a feed indicator that says when data is stale or the broker is rate limiting |
| **Market context** | Futures and carry, put–call ratio, support and resistance, max pain, ATM straddle, IV against historical vol, skew |
| **Market watch** | The five index underlyings plus India VIX; picking one switches the desk |
| **Positions** | Open contracts from the broker, with per-leg P&L |
| **P&L history** | A line of recorded snapshots, taken only while the exchange is trading |
| **Open structures** | Your positions grouped into named structures, each with a payoff curve, greeks, breakevens and per-leg detail |
| **Alerts** | Edge-triggered warnings with editable thresholds — a short being tested, buildup against you, an event before expiry, a limit breached. Raised by the backend, so they fire with no browser open, and delivered to Telegram when configured |
| **News / Calendar** | Market headlines and the economic calendar, with events that land before one of your expiries marked |
| **Option chain** | Collapsed by default. Expiry selection, open-interest buildup, greeks |

## Running it

### With Docker

One image, two processes: the desk, and a loop that backs up the database.

```bash
cp .env.example .env     # then fill it in — see Configuration
docker compose up -d --build
```

Open <http://127.0.0.1:8000>. The API and the frontend are the same origin, so
there is no CORS to configure.

```bash
docker compose logs -f desk      # follow
docker compose down              # stop
```

The port is bound to `127.0.0.1` on purpose. This holds live broker
credentials; do not publish it to `0.0.0.0` without putting authentication in
front of it first.

### Locally, for development

Two processes, with Vite serving the frontend so edits reload.

```bash
uv venv .venv --python 3.12 && uv sync
cd frontend && npm install && cd ..

uv run uvicorn api.app:app --port 8000     # terminal 1
cd frontend && npm run dev                 # terminal 2
```

Open whatever port Vite prints (usually 5173). The backend allows any
localhost origin in development.

## Configuration

Everything lives in `.env`, which is gitignored and excluded from the Docker
build context. `cp .env.example .env` and fill in:

| Variable | |
|---|---|
| `FYERS_CLIENT_ID`, `FYERS_SECRET_KEY` | From <https://myapi.fyers.in/dashboard> |
| `FYERS_REDIRECT_URI` | Any URL you control; it need not resolve |
| `FYERS_ACCESS_TOKEN` | Written by the login script; leave blank initially |
| `FYERS_USERNAME`, `FYERS_TOTP_KEY`, `FYERS_PIN` | Optional, for auto-login |
| `SHARK_API_KEY`, `SHARK_API_SECRET` | Optional, for the perpetuals desk |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Optional, for alert delivery |

Then log in once:

```bash
uv run python scripts/fyers_login.py
uv run python scripts/verify_login.py      # must print "Login OK"
```

Fyers access tokens expire at 06:00 IST daily and there is no refresh grant.
With the three optional variables set, `broker/token_store.py` notices the
expired token and logs in again by itself, so you never run the login script
again. Without them you will need to re-run it each trading day.

## Data and backups

SQLite, at `data/trading.db`. One writer, a few hundred rows a day — Postgres
would add a container, a credential and a rewrite of the storage layer to
solve problems this workload does not have.

The `backup` service copies the database every four hours into `data/backups/`
and keeps the newest 24 (four days). It uses SQLite's online backup API, which
is safe against a database the desk is actively writing to — `cp` is not.

```bash
uv run python scripts/backup_db.py             # one backup now
uv run python scripts/backup_db.py --loop      # what the container runs
```

Tune with `BACKUP_EVERY_HOURS` and `BACKUP_KEEP` in `docker-compose.yml`, and
point the desk at another file with `DB_PATH`.
Restoring is a file copy: stop the desk, replace `data/trading.db`, start it.

## How it is laid out

Dependencies point downward; nothing below knows about anything above it.

```
api/         HTTP surface. app.py composes; routers/ holds the endpoints.
alerting/    The alert engine: rules, edge triggering, the watcher loop.
notify/      Getting an alert to someone who is not at the screen.
venues/      What can be traded and where. No credentials, no I/O.
feeds/       Economic calendar and news. Parsing kept apart from fetching.
execution/   Portfolio and basket status.
strategies/  Strike selection and structure construction.
risk/        Pre-trade checks and limits.
analytics/   Payoff, Black-Scholes, market context. Pure functions.
storage/     SQLite persistence.
broker/      The broker seam: capability protocols, one adapter per venue.
```

`broker/base.py` holds one protocol per capability - prices, trading, option
chains, streaming - so an adapter implements what its venue actually does
instead of raising for the rest, and adding a venue is a new file rather than
a change everywhere. `broker/cache.py` sits in
front of it and holds reads for a few seconds — without it, four panels polling
together breach Fyers' ten-per-second limit and the desk silently goes stale.

## Alerts

The engine lives in `alerting/` and runs as a background task for as long as the
app is up, judging the book about once a minute. It is edge-triggered: an alert
is written when a condition becomes true and not again while it stays true, with
a release band on thresholds so a delta either side of a limit does not announce
itself repeatedly, and a fifteen-minute floor per alert behind that.

State is in SQLite, not the browser, which is what lets an alert fire with
nothing open and reach Telegram. Set `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` to have them delivered; leave them blank and alerts are still
recorded and shown, just not sent. A batch that fires together is sent as one
message, and an alert stays queued until a send succeeds, so an unreachable
Telegram means a late message rather than a lost one.

Besides the thresholds it applies to every structure, you can ask about levels
of your own — "tell me if NIFTY goes above 24,000", or if net P&L drops through
a number. Add them in the alerts panel, or `POST /api/alerts/watches`. A level
can be paused without losing it, and moving one lets it fire again rather than
counting as already-announced. Only symbols something is actually watching are
quoted, so watching nothing costs nothing.

`GET /api/alerts` returns the log, the conditions currently true, the thresholds,
the levels being watched, and whether the watcher is actually running — an empty
list means "nothing is wrong" only if something is looking. Each alert carries
whether it was delivered, so the desk can show what actually left the building.

## Checks

```bash
uv run pytest        # 316 tests
uv run mypy .        # strict
uv run ruff check .

cd frontend && npm test && npm run lint && npm run build
```

Tests never touch the network: the broker has a fake, the feed parsers run
against saved fixtures in `tests/feeds/fixtures/`, and the alert watcher is
handed a callable for its inputs rather than a broker.

`tests/alerting/test_format.py` and `frontend/src/format.test.ts` assert the same
table of money strings. Both exist because either side can word an alert now, and
one situation reading as two is worse than a wrong figure.

## Security

- `.env` is gitignored and listed in `.dockerignore`, so it is not in the
  repository and not in any image layer.
- The container runs as an unprivileged user; the only writable path is the
  mounted data volume.
- `fyersRequests.log` records API traffic. It is gitignored — redact it before
  sharing it for debugging.
- `FYERS_TOTP_KEY` is more sensitive than the API credentials: on its own it is
  a permanent 2FA bypass. Only set it if you accept that.

## Known limits

- **No websocket.** Everything polls; `subscribe_ticks` raises. Prices are a
  few seconds behind, which is immaterial for defined-risk positions held for
  weeks and would matter if you were trading intraday.
- **The economic calendar is scraped**, so it will break when the source page
  changes. It reports "reachable but unreadable" rather than showing an empty
  calendar, but fixing it means fixing the parser.
- **Calendars and diagonals get no payoff curve.** The maths assumes one
  expiry, and applying it across two reports the whole debit as a certain loss,
  so it is suppressed rather than shown wrong.
- **Greeks come from the broker**, which publishes one implied vol per strike.
  Skew is therefore read across strikes rather than between a call and a put.
- **`fyers-apiv3` pulls in the AWS SDK** — around 30MB of `botocore`, `boto3`
  and X-Ray that nothing here uses. It is most of the image's Python weight.

See `docs/SETUP.md` for setup detail, `docs/ARCHITECTURE.md` for the layering,
and `docs/PHASES.md` for what was built when.
