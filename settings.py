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
