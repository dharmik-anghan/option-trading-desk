"""Asking the venue to hold a stop, without asking it anything.

This writes to a live account, so the request is checked against a recording
session rather than sent. The signature is checked too: it covers the exact bytes
transmitted, so a body serialised twice - once to sign, once to send - is a valid
signature for a different request, and the failure looks like a rejected key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest

from broker.errors import BrokerError
from broker.shark.rest import SharkBroker

KEY = "test-key"
SECRET = "test-secret"


class Recorder:
    """Records requests instead of making them."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._status = status
        self._body = body if body is not None else {"status": "ok"}

    def request(
        self,
        method: str,
        url: str,
        params: Any = None,
        data: Any = None,
        headers: Any = None,
        timeout: float = 0,
    ) -> Any:
        self.calls.append(
            {"method": method, "url": url, "data": data, "headers": headers or {}}
        )
        payload = self._body
        status = self._status

        class Response:
            status_code = status
            content = b"{}"
            text = json.dumps(payload)

            def json(self) -> Any:
                return payload

        return Response()


def _broker(recorder: Recorder) -> SharkBroker:
    return SharkBroker(api_key=KEY, api_secret=SECRET, session=recorder)


class TestTheRequest:
    def test_both_levels_are_sent_as_the_venue_wants_them(self) -> None:
        rec = Recorder()
        _broker(rec).set_protection("p-1", quantity=0.01, take_profit=95000, stop_loss=85000)
        (call,) = rec.calls
        assert call["method"] == "POST"
        assert call["url"].endswith("/v2/order/split-tp-sl")
        body = json.loads(call["data"])
        assert body["positionId"] == "p-1"
        assert body["splitTakeProfitOrders"] == [{"quantity": 0.01, "price": 95000}]
        assert body["splitStopLossOrders"] == [{"quantity": 0.01, "price": 85000}]

    def test_setting_only_a_stop_does_not_mention_a_target(self) -> None:
        # Sent only when given, so setting one does not clear the other by
        # omission - which would quietly remove protection while adding some.
        rec = Recorder()
        _broker(rec).set_protection("p-1", quantity=0.01, stop_loss=85000)
        body = json.loads(rec.calls[0]["data"])
        assert "splitStopLossOrders" in body
        assert "splitTakeProfitOrders" not in body

    def test_setting_only_a_target_does_not_mention_a_stop(self) -> None:
        rec = Recorder()
        _broker(rec).set_protection("p-1", quantity=0.01, take_profit=95000)
        body = json.loads(rec.calls[0]["data"])
        assert "splitTakeProfitOrders" in body
        assert "splitStopLossOrders" not in body

    def test_a_timestamp_is_added(self) -> None:
        rec = Recorder()
        _broker(rec).set_protection("p-1", quantity=0.01, stop_loss=1)
        assert "timestamp" in json.loads(rec.calls[0]["data"])


class TestTheSignature:
    def test_it_covers_the_bytes_actually_sent(self) -> None:
        # The whole trick. Serialising once and signing that string is what makes
        # the signature valid for this request rather than for a different
        # spelling of it.
        rec = Recorder()
        _broker(rec).set_protection("p-1", quantity=0.01, stop_loss=85000)
        call = rec.calls[0]
        expected = hmac.new(SECRET.encode(), call["data"].encode(), hashlib.sha256).hexdigest()
        assert call["headers"]["signature"] == expected

    def test_the_key_is_sent_and_the_secret_is_not(self) -> None:
        rec = Recorder()
        _broker(rec).set_protection("p-1", quantity=0.01, stop_loss=1)
        call = rec.calls[0]
        assert call["headers"]["api-key"] == KEY
        assert SECRET not in json.dumps(call["headers"])
        assert SECRET not in call["data"]


class TestRefusals:
    def test_asking_for_nothing_is_refused_before_a_request(self) -> None:
        rec = Recorder()
        with pytest.raises(BrokerError, match="nothing to set"):
            _broker(rec).set_protection("p-1", quantity=0.01)
        assert rec.calls == [], "a pointless request should not reach the venue"

    def test_a_zero_quantity_is_refused(self) -> None:
        rec = Recorder()
        with pytest.raises(BrokerError, match="quantity"):
            _broker(rec).set_protection("p-1", quantity=0, stop_loss=1)
        assert rec.calls == []

    def test_a_refusal_from_the_venue_is_raised_not_swallowed(self) -> None:
        # Protection that silently failed to attach is worse than none, because
        # you would believe it was there.
        rec = Recorder(status=400, body={"message": "position not found"})
        with pytest.raises(BrokerError, match="position not found"):
            _broker(rec).set_protection("p-1", quantity=0.01, stop_loss=1)


class TestTheOrderBody:
    """What actually goes on the wire for an order. Recorded, never sent."""

    def _order(self, **over: object) -> Any:
        from broker.models import OrderRequest

        fields: dict[str, Any] = {
            "symbol": "BTCUSDT",
            "quantity": 0.002,
            "side": "BUY",
            "order_type": "MARKET",
        }
        fields.update(over)
        return OrderRequest(**fields)

    def test_the_margin_asset_is_a_margin_asset(self) -> None:
        # The shared model's product_type defaults to "MARGIN" - an options concept
        # this venue has never heard of. Sending it produced a refused order, which
        # was the right outcome for the wrong reason.
        rec = Recorder(body={"clientOrderId": "x", "status": "OPEN"})
        _broker(rec).place_order(self._order())
        body = json.loads(rec.calls[0]["data"])
        assert body["marginAsset"] == "INR"

    def test_a_real_margin_asset_is_passed_through(self) -> None:
        rec = Recorder(body={"clientOrderId": "x"})
        _broker(rec).place_order(self._order(product_type="USDT"))
        assert json.loads(rec.calls[0]["data"])["marginAsset"] == "USDT"

    def test_the_venues_own_fields_are_present(self) -> None:
        rec = Recorder(body={"clientOrderId": "x"})
        _broker(rec).place_order(self._order())
        body = json.loads(rec.calls[0]["data"])
        assert body["placeType"] == "ORDER_FORM"
        assert body["side"] == "BUY"
        assert body["type"] == "MARKET"
        assert body["quantity"] == 0.002
        assert body["reduceOnly"] is False

    def test_a_limit_order_carries_its_price(self) -> None:
        rec = Recorder(body={"clientOrderId": "x"})
        _broker(rec).place_order(self._order(order_type="LIMIT", limit_price=80_000))
        assert json.loads(rec.calls[0]["data"])["price"] == 80_000

    def test_a_limit_order_without_a_price_never_reaches_the_venue(self) -> None:
        rec = Recorder()
        with pytest.raises(BrokerError, match="needs a price"):
            _broker(rec).place_order(self._order(order_type="LIMIT"))
        assert rec.calls == []


class TestTheBodyIsWrittenAsJavaScriptWouldWriteIt:
    """The venue parses the JSON and re-serialises it with JavaScript before
    hashing, so the body has to match JavaScript's rendering rather than Python's.

    Both differences below were proven against the live venue with the same key at
    the same moment: the accepted form was processed and failed on a missing
    position, the rejected form came back "Access denied: Signature mismatch" -
    which reads like a bad key and is nothing of the kind.
    """

    def test_no_spaces_go_on_the_wire(self) -> None:
        rec = Recorder()
        _broker(rec).set_protection("p-1", quantity=0.01, stop_loss=85000)
        body = rec.calls[0]["data"]
        assert ", " not in body
        assert '": ' not in body

    def test_the_signature_matches_the_compact_form(self) -> None:
        rec = Recorder()
        _broker(rec).set_protection("p-1", quantity=0.01, stop_loss=85000)
        call = rec.calls[0]
        sent = json.loads(call["data"])
        canonical = json.dumps(sent, separators=(",", ":"))
        expected = hmac.new(SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        assert call["headers"]["signature"] == expected

    def test_a_whole_float_is_written_without_its_point(self) -> None:
        # JavaScript has one number type: 1000000.0 and 1000000 are one value and
        # print as "1000000". Python keeps the ".0", and Pydantic makes every number
        # in a request a float - so a price of 1,000,000 broke every order while
        # 0.001 was fine, which is how this survived a probe using integer literals.
        rec = Recorder()
        _broker(rec).set_protection("p-1", quantity=1.0, stop_loss=1000000.0)
        body = rec.calls[0]["data"]
        assert '"price":1000000' in body
        assert "1000000.0" not in body
        assert '"quantity":1' in body
        assert "1.0" not in body

    def test_a_fractional_float_is_left_alone(self) -> None:
        # Both languages print the shortest string that round-trips, so these
        # already agree and must not be mangled.
        rec = Recorder()
        _broker(rec).set_protection("p-1", quantity=0.001, stop_loss=4287.55)
        body = rec.calls[0]["data"]
        assert '"quantity":0.001' in body
        assert '"price":4287.55' in body

    def test_a_boolean_stays_a_boolean(self) -> None:
        # A bool is an int in Python, and would otherwise be turned into 0 or 1 -
        # which is not what `reduceOnly` means.
        from broker.models import OrderRequest

        rec = Recorder(body={"clientOrderId": "x"})
        _broker(rec).place_order(
            OrderRequest(symbol="BTCUSDT", quantity=0.002, side="BUY", order_type="MARKET")
        )
        assert '"reduceOnly":false' in rec.calls[0]["data"]

    def test_the_signature_covers_that_rendering(self) -> None:
        from broker.shark.signing import canonical_json

        rec = Recorder()
        _broker(rec).set_protection("p-1", quantity=1.0, stop_loss=1000000.0)
        call = rec.calls[0]
        expected = hmac.new(
            SECRET.encode(),
            canonical_json(json.loads(call["data"])).encode(),
            hashlib.sha256,
        ).hexdigest()
        assert call["headers"]["signature"] == expected

    def test_an_order_body_is_compact_too(self) -> None:
        from broker.models import OrderRequest

        rec = Recorder(body={"clientOrderId": "x"})
        _broker(rec).place_order(
            OrderRequest(symbol="BTCUSDT", quantity=0.002, side="BUY", order_type="MARKET")
        )
        assert ", " not in rec.calls[0]["data"]
