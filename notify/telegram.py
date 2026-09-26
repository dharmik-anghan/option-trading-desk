"""Telegram delivery.

Uses `requests`, already a dependency via the broker SDK - the Bot API is one
POST, so a Telegram library would be a dependency for nothing.

Deliberately quiet about failures. A notifier that raises would take down the
alert loop that called it, which means one unreachable Telegram would stop the
desk watching the market. It reports success as a bool and leaves the retry
decision to the caller, which simply tries again on the next pass because the
alert stays marked undelivered.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from alerting.models import Alert, Severity

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
TIMEOUT = 10.0

#: A leading mark per severity, so a glance at a phone says how much it matters.
_MARK = {
    Severity.RISK: "\U0001f534",  # red circle
    Severity.WARN: "\U0001f7e1",  # yellow circle
    Severity.TARGET: "\U0001f7e2",  # green circle
    Severity.INFO: "⚪",  # white circle
}


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


def format_alert(alert: Alert) -> str:
    """One alert as a single line.

    Subject first when there is one, because on a phone the structure's name is
    what tells you whether to care before you have read the rest.
    """
    mark = _MARK.get(alert.severity, "")
    if alert.subject:
        return f"{mark} {alert.subject}: {alert.message}"
    return f"{mark} {alert.message}"


class Telegram:
    """Sends messages, or reports that it could not."""

    def __init__(self, config: TelegramConfig, session: requests.Session | None = None) -> None:
        self._config = config
        self._session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return self._config.configured

    def send(self, text: str) -> bool:
        """Deliver one message. False means it did not go, for any reason."""
        if not self._config.configured:
            return False
        url = f"{API}/bot{self._config.bot_token}/sendMessage"
        try:
            response = self._session.post(
                url,
                json={
                    "chat_id": self._config.chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            # The token is in the URL, so never log the URL or the response body.
            log.warning("telegram send failed: %s", type(exc).__name__)
            return False
        if response.status_code != 200:
            log.warning("telegram refused the message: HTTP %s", response.status_code)
            return False
        return True

    def send_alerts(self, alerts: list[Alert]) -> bool:
        """Deliver a batch as one message.

        One message rather than several: six alerts firing together is one
        situation, and six notifications for it is how people mute a bot.
        """
        if not alerts:
            return True
        return self.send("\n".join(format_alert(a) for a in alerts))
