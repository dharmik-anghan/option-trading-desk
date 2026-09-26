"""Signing a Shark request.

HMAC-SHA256 with the API secret, hex-encoded, sent as the `signature` header
beside `api-key`. What gets signed depends on the method, which is the part worth
writing down because getting it wrong looks like an authentication failure rather
than a signing one:

- GET: the query string, exactly as sent, including the timestamp.
- POST and friends: the JSON body, exactly as sent.

"Exactly as sent" is not enough, because the venue does not hash the bytes it
received. It parses the JSON and re-serialises it with JavaScript before hashing,
so the body has to be written the way JavaScript would write it. Two differences
matter, and both produce "Access denied: Signature mismatch" - an error that reads
like a bad key and is nothing of the kind.

Separators. `JSON.stringify` is compact; Python's `json.dumps` defaults to `", "`
and `": "`. A body signed with the defaults is signed over a string the venue
never computes.

Whole numbers. JavaScript has one number type and renders 1000000.0 as
"1000000"; Python renders it as "1000000.0". Pydantic turns every number in a
request into a float, so a price of 1,000,000 or a quantity of 1 breaks the
signature while 0.001 does not - which is why this survived a probe using integer
literals and failed on every real order.

Proven against the live venue: "price":1000000 was accepted and processed,
"price":1000000.0 was refused for a signature mismatch, same key, same moment.

The reference implementation this was ported from had both right without saying
so, because JavaScript gave it both for free.
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


def as_javascript_would(value: Any) -> Any:
    """The same value, rendered the way JavaScript renders numbers.

    Only whole floats need changing: JavaScript has a single number type, so 1.0
    and 1 are one value and print as "1". Fractional floats already agree, because
    both languages print the shortest string that round-trips.

    Recursive, since the numbers that matter are inside lists of orders rather
    than at the top level.
    """
    if isinstance(value, bool):
        # Before the int branch: a bool is an int in Python and is not a number
        # to JSON.
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: as_javascript_would(v) for k, v in value.items()}
    if isinstance(value, list):
        return [as_javascript_would(v) for v in value]
    return value


def canonical_json(body: dict[str, Any]) -> str:
    """The body as the venue will re-serialise it before hashing.

    Compact separators, JavaScript's numbers, and no ASCII escaping - all three
    because `JSON.stringify` does it that way and the venue's signature is
    computed over its own rendering, not over what arrived.
    """
    return json.dumps(as_javascript_would(body), separators=(",", ":"), ensure_ascii=False)


def signed_body(secret: str, body: dict[str, Any]) -> tuple[str, str]:
    """A JSON body and its signature, both from one canonical rendering."""
    merged = {**body}
    merged.setdefault("timestamp", timestamp_ms())
    text = canonical_json(merged)
    return text, sign(secret, text)


def headers(api_key: str, signature: str, *, json_body: bool = False) -> dict[str, str]:
    out = {"api-key": api_key, "signature": signature}
    if json_body:
        out["Content-Type"] = "application/json"
    return out
