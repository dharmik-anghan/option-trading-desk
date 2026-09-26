---
name: dev-setup
description: Bootstrap and verify the option-strategy dev environment (venv, deps, env vars, lint/type/test status). Use this before doing any work in this repo, especially in a fresh session.
---

# Dev environment setup

Run this whenever starting work in this repo so every session (human or
agent) bootstraps the same way.

## Steps

1. Ensure `uv` is installed (`which uv`). If missing, stop and tell the user
   to `brew install uv`.
2. From the repo root:
   ```bash
   uv venv .venv --python 3.12   # only if .venv doesn't exist
   uv sync
   ```
3. Check `.env` exists. If not, copy `.env.example` to `.env` and tell the
   user to fill in `FYERS_CLIENT_ID`, `FYERS_SECRET_KEY`, `FYERS_REDIRECT_URI`
   (see `docs/SETUP.md`) — **never print or log the actual secret values**.
4. Confirm required env vars are present (names only, not values):
   ```bash
   uv run python -c "from settings import load_settings; load_settings(); print('settings OK')"
   ```
   If this fails, report which vars are missing and point at `docs/SETUP.md`
   — do not guess values.
5. Run the checks and report status:
   ```bash
   uv run pytest -q
   uv run mypy .
   uv run ruff check .
   ```

## When Fyers connectivity matters

If the task touches `broker/` or needs a live Fyers session, check the token
with `uv run python scripts/verify_login.py`, which prints the expiry.

Tokens die at 06:00 IST the morning after they're issued. When the three
auto-login secrets are set, `broker/token_store.py` refreshes on demand, so
an expired token is not by itself a problem — anything going through
`get_access_token` recovers on the next call. If `verify_login.py` fails for
any other reason, or auto-login isn't configured, tell the user to run
`uv run python scripts/fyers_login.py` — do not attempt to complete the
interactive login flow yourself.

That script auto-detects TOTP auto-login (see `broker/fyers_auth.py`) if
`FYERS_USERNAME`/`FYERS_TOTP_KEY`/`FYERS_PIN` are all set in `.env`, and
falls back to the manual browser flow otherwise — **never ask the user to
paste their TOTP secret or PIN into chat**; they add those to `.env`
themselves if they want auto-login.

## Reference

- `docs/SETUP.md` — full manual setup walkthrough
- `docs/ARCHITECTURE.md` — codebase layout and the `Broker` interface
- `docs/PHASES.md` — current build status, what's done vs. next
