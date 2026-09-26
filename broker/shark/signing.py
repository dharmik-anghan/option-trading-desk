"""Signing a Shark request.

HMAC-SHA256 with the API secret, hex-encoded, sent as the `signature` header
beside `api-key`. What gets signed depends on the method, which is the part worth
writing down because getting it wrong looks like an authentication failure rather
than a signing one:

- GET: the query string, exactly as sent, including the timestamp.
- POST and friends: the JSON body, exactly as sent.

"Exactly as sent" is the whole trick. The signature covers a specific string of
bytes, so the body must be serialised once and both signed and transmitted - a
second `json.dumps` with different separators or key order produces a valid
signature for a different request.

Verified against the live API: a signed GET of /v1/positions/OPEN returns 200.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode


def timestamp_ms() -> int:
    """Shark wants milliseconds, and rejects a request that omits them."""
    return int(time.time() * 1000)


def sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def signed_query(secret: str, params: dict[str, Any] | None = None) -> tuple[str, str]:
    """A query string and its signature, with a timestamp added.

    Returns both so the caller sends the same string that was signed.
    """
    merged = {**(params or {})}
    merged.setdefault("timestamp", timestamp_ms())
    query = urlencode(merged)
    return query, sign(secret, query)


def signed_body(secret: str, body: dict[str, Any]) -> tuple[str, str]:
    """A JSON body and its signature.

    Serialised here, once, for exactly the reason in the module docstring.
    """
    merged = {**body}
    merged.setdefault("timestamp", timestamp_ms())
    text = json.dumps(merged)
    return text, sign(secret, text)


def headers(api_key: str, signature: str, *, json_body: bool = False) -> dict[str, str]:
    out = {"api-key": api_key, "signature": signature}
    if json_body:
        out["Content-Type"] = "application/json"
    return out
