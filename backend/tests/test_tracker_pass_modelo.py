"""The pass table. It's the ONLY stateful part of tracker access: the wager window keeps getting
recalculated on every query and stores nothing."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from app.db import init_db, make_session_factory
from app.models import TrackerPass

AHORA = datetime.now(timezone.utc)


def _sf():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    return make_session_factory(engine)


def test_the_table_creates_itself_and_stores_a_pass():
    # It's a NEW table, so `create_all` creates it. No need to touch `_ENSURE_COLUMNS`.
    sf = _sf()
    with sf() as s:
        s.add(TrackerPass(id="p1", wallet="W", days=7, price_base_units=10_000_000,
                          status="pending", starts_at=AHORA, ends_at=AHORA + timedelta(days=7)))
        s.commit()
    with sf() as s:
        p = s.scalars(select(TrackerPass).where(TrackerPass.wallet == "W")).one()
        assert p.days == 7
        assert p.status == "pending"
        assert p.tx_signature is None       # hasn't been charged yet


def test_the_signature_can_be_saved_before_activating():
    # It's what makes the gap between charging and activating reconcilable: `pending` WITH a
    # signature means exactly "this was charged and not activated".
    sf = _sf()
    with sf() as s:
        s.add(TrackerPass(id="p2", wallet="W", days=30, price_base_units=30_000_000,
                          status="pending", starts_at=AHORA, ends_at=AHORA + timedelta(days=30),
                          tx_signature="5xFirma"))
        s.commit()
    with sf() as s:
        p = s.get(TrackerPass, "p2")
        assert p.status == "pending" and p.tx_signature == "5xFirma"
