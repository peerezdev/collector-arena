"""The Machine Tracker's payment pass: how much it costs and how long it's valid for.

No network, no charges, and no hidden `datetime.now()`: `ahora` can be passed in, which is what
makes these tests reliable at any hour. The charge itself lives in the endpoint, because it mixes
signature, RPC and database and can't be tested without doubles.

TWO RULES RUN THIS FILE, and both are decisions, not details:

  · ONLY `active` GRANTS ACCESS. A `pending` is a half-finished purchase and a `failed` is a
    purchase that never happened. Counting either would give away the tracker to whoever
    couldn't pay.

  · BUYING WHILE ALREADY INSIDE ADDS AT THE END. If buying early removed days, people would learn
    to wait for the pass to expire, which is worse for everyone.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TrackerPass

#: The durations we sell. Any other one has no price and the purchase gets rejected.
DIAS_VALIDOS: Tuple[int, int] = (7, 30)

USDC = 1_000_000


def precio_base_units(days: int, s7: float, s30: float) -> Optional[int]:
    """What that duration costs, in USDC base units. `None` if it isn't sold.

    Returns base units and not dollars because that's how all of the application's money moves;
    converting on every call would end with someone forgetting to and charging a million.

    Zero is OFF, not free: it's the switch that lets this ship before a price is decided.
    """
    if days not in DIAS_VALIDOS:
        return None
    precio = s7 if days == 7 else s30
    if precio <= 0:
        return None
    # `round` and not `int`: 12.99 * 1e6 in floating point is 12989999.999..., and truncating
    # would lose a cent on every purchase with decimals.
    return int(round(precio * USDC))


def _con_zona(d: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns dates WITHOUT a timezone even when they're saved with one. Without this,
    comparing them to a timezone-aware `ahora` raises TypeError, and only in production."""
    if d is None:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=timezone.utc)


def pase_vigente(session: Session, wallet: str, ahora: Optional[datetime] = None) -> Optional[datetime]:
    """Until when that wallet has a pass, or `None` if it has none currently active."""
    ahora = ahora or datetime.now(timezone.utc)
    filas = session.scalars(
        select(TrackerPass).where(TrackerPass.wallet == wallet, TrackerPass.status == "active")
    ).all()
    fines = [f for f in (_con_zona(x.ends_at) for x in filas) if f is not None and f > ahora]
    return max(fines) if fines else None


def periodo(session: Session, wallet: str, days: int,
            ahora: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """From and until when a pass bought RIGHT NOW would be valid.

    If one is already active, the new one starts where that one ends. The wager does NOT enter
    into this calculation: it's a rolling window that changes on its own, so accounting for it
    would force guessing how long the access you already have is going to last.
    """
    ahora = ahora or datetime.now(timezone.utc)
    desde = pase_vigente(session, wallet, ahora=ahora) or ahora
    return desde, desde + timedelta(days=days)
