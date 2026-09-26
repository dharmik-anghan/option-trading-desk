"""Liveness, and what this desk can trade.

`/api/venues` is how a client learns which desks exist and what each supports,
rather than having the list hardcoded in the frontend. Capabilities are what
panels should key off: an option-chain panel means nothing on a perpetuals
venue, and asking beats assuming when a second desk arrives.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from api.deps import BrokerDep
from venues import listed

router = APIRouter()


@router.get("/api/health")
def health(broker: BrokerDep) -> dict[str, object]:
    """Liveness, plus whether broker reads are currently degraded.

    Polled rarely; the per-request status codes above are what the desk reacts
    to. This is for the case where cached values are being served over a rate
    limit and every request still looks like a success.
    """
    since = getattr(broker, "seconds_since_rate_limited", None)
    recently = since is not None and since < 60
    return {"status": "ok", "rate_limited": recently}


class VenueResponse(BaseModel):
    id: str
    name: str
    asset_class: str
    quote_currency: str
    session: str
    capabilities: list[str]


@router.get("/api/venues", response_model=list[VenueResponse])
def venues() -> list[VenueResponse]:
    """Every venue the desk knows about, in the order a switcher should show them."""
    return [
        VenueResponse(
            id=spec.id,
            name=spec.name,
            asset_class=str(spec.asset_class),
            quote_currency=spec.quote_currency,
            session=str(spec.session),
            # sorted, so the response is stable rather than set-ordered
            capabilities=sorted(str(c) for c in spec.capabilities),
        )
        for spec in listed()
    ]
