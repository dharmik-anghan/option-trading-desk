"""Telegram delivery, without touching the network.

The important test is the last class: a notifier that raises would take down the
loop that called it, so one unreachable Telegram would stop the desk watching
the market entirely.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from alerting.models import Alert, Severity
from notify.telegram import Telegram, TelegramConfig, format_alert


class FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class FakeSession:
    """Records calls instead of making them."""

    def __init__(self, status_code: int = 200, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._status = status_code
        self._raises = raises

    def post(self, url: str, json: dict[str, Any], timeout: float) -> FakeResponse:  # noqa: A002
        self.calls.append((url, json))
        if self._raises is not None:
            raise self._raises
        return FakeResponse(self._status)


def _alert(message: str, severity: Severity = Severity.WARN, subject: str | None = None) -> Alert:
    return Alert(key="k", severity=severity, message=message, at=1000, subject=subject)


CONFIG = TelegramConfig(bot_token="123:abc", chat_id="456")


class TestFormatting:
    def test_subject_comes_first(self) -> None:
        text = format_alert(_alert("Short 22,900 PE tested", subject="27 Oct - Iron Condor"))
        assert "27 Oct - Iron Condor: Short 22,900 PE tested" in text

    def test_an_account_wide_alert_has_no_subject_prefix(self) -> None:
        assert format_alert(_alert("Daily loss limit breached")).endswith(
            "Daily loss limit breached"
        )

    def test_severities_are_marked_differently(self) -> None:
        marks = {format_alert(_alert("x", sev))[0] for sev in Severity}
        assert len(marks) == len(Severity)


class TestSending:
    def test_a_message_goes_to_the_right_chat(self) -> None:
        session = FakeSession()
        assert Telegram(CONFIG, session).send("hello") is True
        url, body = session.calls[0]
        assert url == "https://api.telegram.org/bot123:abc/sendMessage"
        assert body["chat_id"] == "456"
        assert body["text"] == "hello"

    def test_a_batch_is_one_message(self) -> None:
        # six alerts firing together is one situation; six notifications for it
        # is how people mute a bot
        session = FakeSession()
        alerts = [_alert(f"thing {i}") for i in range(6)]
        assert Telegram(CONFIG, session).send_alerts(alerts) is True
        assert len(session.calls) == 1
        assert session.calls[0][1]["text"].count("\n") == 5

    def test_an_empty_batch_sends_nothing(self) -> None:
        session = FakeSession()
        assert Telegram(CONFIG, session).send_alerts([]) is True
        assert session.calls == []

    def test_unconfigured_never_calls_out(self) -> None:
        session = FakeSession()
        blank = Telegram(TelegramConfig(bot_token="", chat_id=""), session)
        assert blank.configured is False
        assert blank.send("hello") is False
        assert session.calls == []


class TestFailureIsReportedNotRaised:
    def test_a_network_error_is_a_false_not_an_exception(self) -> None:
        session = FakeSession(raises=requests.ConnectionError("no route"))
        assert Telegram(CONFIG, session).send("hello") is False

    def test_a_timeout_is_a_false(self) -> None:
        session = FakeSession(raises=requests.Timeout("too slow"))
        assert Telegram(CONFIG, session).send("hello") is False

    def test_a_refusal_is_a_false(self) -> None:
        session = FakeSession(status_code=400)
        assert Telegram(CONFIG, session).send("hello") is False

    def test_the_token_never_reaches_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # the bot token is in the URL, and anyone holding it controls the bot
        session = FakeSession(raises=requests.ConnectionError("no route"))
        with caplog.at_level("WARNING"):
            Telegram(CONFIG, session).send("hello")
        assert "123:abc" not in caplog.text
