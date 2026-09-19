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

**Fyers access tokens expire daily** — re-run this script each trading day.

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

See `docs/ARCHITECTURE.md` for how the codebase is laid out and
`docs/PHASES.md` for what's built so far and what's next.
