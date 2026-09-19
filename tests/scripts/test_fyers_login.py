from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from fyers_login import jwt_subject  # noqa: E402


def _make_jwt(payload: dict[str, object]) -> str:
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(payload).encode())
    return f"{header}.{body}.fakesignature"


def test_jwt_subject_reads_sub_claim() -> None:
    token = _make_jwt({"sub": "access_token"})
    assert jwt_subject(token) == "access_token"


def test_jwt_subject_distinguishes_auth_code_tokens() -> None:
    token = _make_jwt({"sub": "auth_code"})
    assert jwt_subject(token) == "auth_code"


def test_jwt_subject_returns_none_for_non_jwt_string() -> None:
    assert jwt_subject("not-a-jwt") is None


def test_jwt_subject_returns_none_when_sub_missing() -> None:
    token = _make_jwt({"other": "field"})
    assert jwt_subject(token) is None
