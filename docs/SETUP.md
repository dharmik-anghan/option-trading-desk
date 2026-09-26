# Setup

## Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) for dependency management (`brew install uv`)

## 1. Install dependencies

```bash
uv venv .venv --python 3.12
uv sync
```

## 2. Get Fyers API credentials

1. Go to https://myapi.fyers.in/dashboard and create an app.
2. Note the **App ID** (looks like `ABC123-100`) and **Secret Key**.
3. Set the redirect URI to `https://127.0.0.1` (or any URL you control — it
   doesn't need to resolve, we just read the `auth_code` param off it).

## 3. Configure `.env`

```bash
cp .env.example .env
```

Fill in `FYERS_CLIENT_ID`, `FYERS_SECRET_KEY`, `FYERS_REDIRECT_URI`.
Leave `FYERS_ACCESS_TOKEN` blank — the login script fills it in.

## 4. Log in to Fyers

```bash
uv run python scripts/fyers_login.py
```

Follow the printed URL, log in, and paste back the redirected URL (or just
the `auth_code` param). This writes `FYERS_ACCESS_TOKEN` into `.env`.

**Fyers access tokens expire at 06:00 IST the morning after they're issued**
(there is no refresh grant — a new token means a new login). If you set up
auto-login below, the app handles this for you: `broker/token_store.py`
notices the expired token and logs in again on the next call. Without
auto-login there's no way to renew without a browser, so you'll need to
re-run this script each trading day.

### Optional: skip the manual browser step

Fill in `FYERS_USERNAME` (your Fyers ID, e.g. `XY12345`), `FYERS_TOTP_KEY`
(the base32 secret shown when you set up TOTP 2FA on Fyers — not a 6-digit
code, the underlying key), and `FYERS_PIN` (your trading PIN) in `.env`,
and `scripts/fyers_login.py` will log in automatically with no browser step.

With these set you generally never run the login script again — the app
refreshes the token itself whenever it has expired.

This uses undocumented Fyers endpoints (see `broker/fyers_auth.py`) rather
than Fyers' official OAuth flow, so it could break if Fyers changes them —
if it does, the script automatically falls back to the manual flow.

**These two extra secrets are more sensitive than your API key/secret** —
your TOTP secret alone gives permanent 2FA-bypass capability if `.env` ever
leaked. Only add them if you're comfortable with that tradeoff; leaving any
of the three blank keeps you on the manual flow.

## 5. Verify the login actually works

```bash
uv run python scripts/verify_login.py
```

This is the Phase 0 go/no-go checkpoint — it must print `Login OK` (a real
call to Fyers' profile endpoint) before any broker/market-data code is built.

## Running checks

```bash
uv run pytest      # tests
uv run mypy .       # strict type checking
uv run ruff check . # lint
```

## Running the web dashboard

Positions, P&L, strategy preview, P&L history, **and order placement** —
the dashboard is now the primary way to place orders (the old CLI
confirm-and-place script was removed). Requires Node.js/npm in addition to
the Python setup above.

Placing an order in the dashboard: preview a strategy, review the legs and
the pre-trade risk checks shown below them, then click "Place order" — it's
disabled if any risk check fails. This is a real order with real money the
moment you click it; there's no second confirmation step.

```bash
# Terminal 1: backend API
uv run uvicorn api.app:app --port 8000

# Terminal 2: frontend (first time only: cd frontend && npm install)
cd frontend
npm run dev
```

Open the URL Vite prints (usually http://localhost:5173, but it'll pick a
different port if that one's busy). The backend's CORS is configured to
allow any `localhost`/`127.0.0.1` port for local dev — see `api/app.py`.

See `docs/ARCHITECTURE.md` for how the codebase is laid out and
`docs/PHASES.md` for what's built so far and what's next.
