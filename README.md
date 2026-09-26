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
| **News / Calendar** | Market headlines and the economic calendar, with events that land before one of your expiries marked. Headlines filter by topic — India, crypto, metals and oil — and default to whichever desk you are on |
| **Option chain** | Collapsed by default. Expiry selection, open-interest buildup, greeks |

A second desk covers perpetual futures on Shark Exchange - Bitcoin, gold and
crude - reached through `GET /api/perps`. It lists what is open with the figures
leverage makes matter: the margin behind a position, and how far it is from the
price the venue closes it at, as a percentage rather than in points so gold at
4,300 and Bitcoin at 84,000 can be read side by side. A stop or a target can be
handed to the exchange to hold, which is the only kind that fires with this app
closed.

**Orders placed from that desk are real.** Five checks run server-side before
anything leaves, and every attempt is recorded whether it was sent or refused,
because the question after a surprise is what it tried to do.

Two of those checks are the venue's own rules, read from it rather than written
down here: the leverage ceiling for that contract, and the smallest order it will
accept. Both differ sharply per instrument - 150x on BTCUSDT against 50x on oil -
and the size floor moves with the price, because it is usually a notional minimum
rather than a quantity one. BTCUSDT allows 0.001 but demands 115 USDT, so at
84,000 the smallest real order is 0.002, while oil needs 0.07. The ticket shows
both, along with the margin the position will demand, rather than leaving them to
be discovered by a rejection.

The other three are caps on what this program may do by mistake, not opinions
about a good trade. Notional is the one that matters: it is in money, so a single
figure covers every instrument and it catches a fat finger - 0.002 typed as 2 is
168,000 of notional and refused. A quantity cap is available and off, because a
quantity cannot be compared across these contracts: 0.01 BTCUSDT is about 840 USDT
where 0.01 CLUSDT is 94 cents, and a figure tight enough for Bitcoin blocked oil's
smallest legal order. The third is how far a new position would start from
liquidation. Prices there arrive on a stream rather
than being polled: the venue allows 60 requests a minute against Fyers' ~200, and
it pushes. Note that prices are quoted in USDT while the account margins in INR,
so those two figures are deliberately labelled in different units.

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
streaming/   Live prices, held once and shared with whoever is listening.
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

Levels belong to the structure they describe: a profit target, a stop and a delta
limit are set on each open structure rather than as account-wide settings, since
one number shared across a condor and a calendar answers for neither. Blank means
no level, and moving a level lets it fire again.

Besides those, you can ask about levels — "tell me if NIFTY goes above 24,000", or if net P&L drops through
a number. Add them in the alerts panel, or `POST /api/alerts/watches`. A level
can be paused without losing it, and moving one lets it fire again rather than
counting as already-announced. Only symbols something is actually watching are
quoted, so watching nothing costs nothing.

Three thresholds remain as fixed defaults rather than being editable: the
worst-case limit, the delta a short counts as tested at, and how many days before
expiry to warn. They are defaults for the per-structure rules, and the honest end
of this change is for them to move onto the structure too.

`GET /api/alerts` returns the log, the conditions currently true, those defaults,
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

- **Perpetual prices are pushed to the browser**, over server-sent events rather
  than a websocket: the traffic is one-way and a browser reconnects an EventSource
  by itself. The rest of that desk - positions, contract limits - still polls, on a
  slow timer, because it changes on the scale of an order rather than a tick.
- **The options desk polls.** Fyers' `subscribe_ticks` still raises, so those
  prices are a few seconds behind - immaterial for defined-risk positions held
  for weeks, and it would matter intraday. The perpetuals desk streams.
- **`python-engineio` is pinned below 4.11.** `fyers-apiv3` pins `aiohttp==3.9.3`
  exactly, and from 4.11 engineio calls an aiohttp API that version lacks, so a
  newer one installs cleanly and fails at connect time.
- **News is filtered by publisher, not by content.** A source declares what it
  covers and every headline inherits it. Cruder than reading the words in a
  title, and honest: a publisher's beat is a fact, a topic guessed from a
  headline is a guess. The cost is that an Indian source writing about gold is
  filed under India.
- **The economic calendar is scraped**, so it will break when the source page
  changes. It reports "reachable but unreadable" rather than showing an empty
  calendar, but fixing it means fixing the parser.
- **Calendars and diagonals get no payoff curve.** The maths assumes one
  expiry, and applying it across two reports the whole debit as a certain loss,
  so it is suppressed rather than shown wrong.
- **Leverage is a standing per-symbol setting on Shark, not part of an order.**
  Its order endpoint has no leverage field and applies whatever the symbol was last
  configured with, so the desk sets it (`PUT /v1/exchange/update/leverage`) before
  placing and abandons the order if that fails. Before this, an order chosen at 10x
  ran at the account's standing 150x with liquidation 0.42% from entry.
- **Shark signs its own rendering of the body, not the bytes you send.** It parses
  the JSON and re-serialises it with JavaScript before hashing, so the body has to
  be written the way JavaScript writes it: compact separators, and whole numbers
  without a decimal point. Python renders `1000000.0` where JavaScript renders
  `1000000`, and Pydantic makes every number in a request a float - so a price of
  1,000,000 fails where 0.001 works. `broker/shark/signing.py` handles it, and the
  error if it ever regresses is "Access denied: Signature mismatch", which reads
  like a bad key and is nothing of the kind.
- **One shape in the Shark integration is unverified.** An open perpetual
  position's unrealised-P&L field name is guessed, because the account had no
  open position to capture and a closed one reports realised profit instead. When
  the venue reports nothing the desk works the figure out from the price and marks
  it with an asterisk, so a wrong guess shows as a derived number rather than a
  confident zero.
- **Greeks come from the broker**, which publishes one implied vol per strike.
  Skew is therefore read across strikes rather than between a call and a put.
- **`fyers-apiv3` pulls in the AWS SDK** — around 30MB of `botocore`, `boto3`
  and X-Ray that nothing here uses. It is most of the image's Python weight.

See `docs/SETUP.md` for setup detail, `docs/ARCHITECTURE.md` for the layering,
and `docs/PHASES.md` for what was built when.
