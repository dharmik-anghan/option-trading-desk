from pathlib import Path

import pytest
from pydantic import ValidationError

from settings import MissingSettingsError, Settings, load_settings


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FYERS_CLIENT_ID", "ABC123-100")
    monkeypatch.setenv("FYERS_SECRET_KEY", "supersecret")
    monkeypatch.setenv("FYERS_REDIRECT_URI", "https://127.0.0.1")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.fyers_client_id == "ABC123-100"
    assert settings.fyers_secret_key == "supersecret"
    assert settings.fyers_redirect_uri == "https://127.0.0.1"
    assert settings.fyers_access_token == ""
    assert settings.has_auto_login_credentials is False


def test_has_auto_login_credentials_requires_all_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FYERS_CLIENT_ID", "ABC123-100")
    monkeypatch.setenv("FYERS_SECRET_KEY", "supersecret")
    monkeypatch.setenv("FYERS_REDIRECT_URI", "https://127.0.0.1")
    monkeypatch.setenv("FYERS_USERNAME", "XY12345")
    monkeypatch.setenv("FYERS_TOTP_KEY", "JBSWY3DPEHPK3PXP")
    monkeypatch.delenv("FYERS_PIN", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.has_auto_login_credentials is False

    monkeypatch.setenv("FYERS_PIN", "1234")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.has_auto_login_credentials is True


def test_settings_missing_required_field_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FYERS_CLIENT_ID", raising=False)
    monkeypatch.delenv("FYERS_SECRET_KEY", raising=False)
    monkeypatch.delenv("FYERS_REDIRECT_URI", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_load_settings_exits_cleanly_with_helpful_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("FYERS_CLIENT_ID", raising=False)
    monkeypatch.delenv("FYERS_SECRET_KEY", raising=False)
    monkeypatch.delenv("FYERS_REDIRECT_URI", raising=False)
    monkeypatch.chdir(tmp_path)  # avoid picking up a real project .env

    with pytest.raises(MissingSettingsError):
        load_settings()

    err = capsys.readouterr().err
    assert "fyers_client_id" in err
    assert "docs/SETUP.md" in err
