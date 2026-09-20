"""Typed access to configuration loaded from the environment / .env file."""

import sys

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Required configuration for talking to Fyers.

    Values are read from a `.env` file (see `.env.example`) or real
    environment variables. Missing required fields raise a validation error
    at startup rather than failing later with a confusing broker error.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    fyers_client_id: str
    fyers_secret_key: str
    fyers_redirect_uri: str
    fyers_access_token: str = ""

    # Optional: enables TOTP auto-login (broker/fyers_auth.py) so the daily
    # token refresh doesn't need a manual browser step. All three or none -
    # partial credentials fall back to the manual flow. These are more
    # sensitive than the API key/secret above (your TOTP secret alone gives
    # permanent 2FA-bypass capability if it ever leaked) - never log or
    # print these values.
    fyers_username: str = ""
    fyers_totp_key: str = ""
    fyers_pin: str = ""

    @property
    def has_auto_login_credentials(self) -> bool:
        return bool(self.fyers_username and self.fyers_totp_key and self.fyers_pin)


class MissingSettingsError(SystemExit):
    """Raised (as a clean exit, not a traceback) when required config is absent."""


def load_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        missing = ", ".join(str(error["loc"][0]) for error in exc.errors() if error["loc"])
        print(
            f"Missing required settings: {missing}.\n"
            "Copy .env.example to .env and fill it in — see docs/SETUP.md.",
            file=sys.stderr,
        )
        raise MissingSettingsError(1) from None
