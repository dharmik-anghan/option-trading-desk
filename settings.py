"""Typed access to configuration loaded from the environment / .env file."""

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


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
