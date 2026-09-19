import pytest
from pydantic import ValidationError

from settings import Settings


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setenv("FYERS_CLIENT_ID", "ABC123-100")
    monkeypatch.setenv("FYERS_SECRET_KEY", "supersecret")
    monkeypatch.setenv("FYERS_REDIRECT_URI", "https://127.0.0.1")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.fyers_client_id == "ABC123-100"
    assert settings.fyers_secret_key == "supersecret"
    assert settings.fyers_redirect_uri == "https://127.0.0.1"
    assert settings.fyers_access_token == ""


def test_settings_missing_required_field_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FYERS_CLIENT_ID", raising=False)
    monkeypatch.delenv("FYERS_SECRET_KEY", raising=False)
    monkeypatch.delenv("FYERS_REDIRECT_URI", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
