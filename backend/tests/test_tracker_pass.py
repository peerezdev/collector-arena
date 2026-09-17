"""Price and period of the tracker pass, without touching network or charges."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.db import init_db, make_session_factory
from app.models import TrackerPass
from app.services.tracker_pass import (DIAS_VALIDOS, pase_vigente, periodo, precio_base_units)

AHORA = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def sf():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    return make_session_factory(engine)


def _pase(s, wallet, dias, *, status, desde, hasta):
    s.add(TrackerPass(id=f"{wallet}-{desde.isoformat()}-{status}", wallet=wallet, days=dias,
                      price_base_units=1, status=status, starts_at=desde, ends_at=hasta))
    s.commit()


# ── Price ────────────────────────────────────────────────────────────────────

def test_the_price_arrives_in_BASE_units_not_dollars():
    # All of the application's money moves in base units of 6 decimals. Returning dollars here
    # would force converting on every call, and the first one that forgot would charge a million.
    assert precio_base_units(7, 10.0, 30.0) == 10_000_000
    assert precio_base_units(30, 10.0, 30.0) == 30_000_000


def test_a_price_of_ZERO_means_off_not_free():
    assert precio_base_units(7, 0.0, 30.0) is None
    assert precio_base_units(30, 10.0, 0.0) is None


def test_a_duration_we_do_not_sell_has_no_price():
    assert precio_base_units(1, 10.0, 30.0) is None
    assert precio_base_units(365, 10.0, 30.0) is None
    assert DIAS_VALIDOS == (7, 30)


def test_cents_are_not_lost_when_converting():
    # 2.01 is exactly 2_010_000, but 2.01 * 1_000_000 in floating point gives 2009999.9999999998:
    # truncating with `int()` would lose a cent here. It didn't happen with 12.99 (that one lands
    # right on an exactly representable value), so it didn't serve to tell rounding apart from
    # truncating.
    assert precio_base_units(7, 2.01, 30.0) == 2_010_000


# ── Active pass ──────────────────────────────────────────────────────────────

def test_without_passes_nothing_is_active(sf):
    with sf() as s:
        assert pase_vigente(s, "W", ahora=AHORA) is None


def test_an_ACTIVE_pass_within_its_dates_is_valid(sf):
    with sf() as s:
        _pase(s, "W", 7, status="active", desde=AHORA - timedelta(days=1),
              hasta=AHORA + timedelta(days=6))
        assert pase_vigente(s, "W", ahora=AHORA) == AHORA + timedelta(days=6)


def test_a_PENDING_pass_does_not_grant_access(sf):
    # It's what keeps a failed charge from giving away anything: the row is written before charging.
    with sf() as s:
        _pase(s, "W", 7, status="pending", desde=AHORA, hasta=AHORA + timedelta(days=7))
        assert pase_vigente(s, "W", ahora=AHORA) is None


def test_a_FAILED_pass_does_not_either(sf):
    with sf() as s:
        _pase(s, "W", 7, status="failed", desde=AHORA, hasta=AHORA + timedelta(days=7))
        assert pase_vigente(s, "W", ahora=AHORA) is None


def test_an_expired_pass_stops_being_valid(sf):
    with sf() as s:
        _pase(s, "W", 7, status="active", desde=AHORA - timedelta(days=8),
              hasta=AHORA - timedelta(seconds=1))
        assert pase_vigente(s, "W", ahora=AHORA) is None


def test_with_several_passes_the_one_that_ends_LATEST_wins(sf):
    with sf() as s:
        _pase(s, "W", 7, status="active", desde=AHORA, hasta=AHORA + timedelta(days=7))
        _pase(s, "W", 30, status="active", desde=AHORA + timedelta(days=7),
              hasta=AHORA + timedelta(days=37))
        assert pase_vigente(s, "W", ahora=AHORA) == AHORA + timedelta(days=37)


def test_ANOTHER_wallets_pass_does_not_count(sf):
    with sf() as s:
        _pase(s, "OTRO", 7, status="active", desde=AHORA, hasta=AHORA + timedelta(days=7))
        assert pase_vigente(s, "W", ahora=AHORA) is None


# ── Period, with stacking ────────────────────────────────────────────────────

def test_without_a_previous_pass_it_starts_TODAY(sf):
    with sf() as s:
        desde, hasta = periodo(s, "W", 7, ahora=AHORA)
        assert desde == AHORA
        assert hasta == AHORA + timedelta(days=7)


def test_buying_with_an_active_pass_ADDS_AT_THE_END(sf):
    # If buying early removed days, people would learn to wait for it to expire. Worse for them
    # and worse for us.
    with sf() as s:
        _pase(s, "W", 7, status="active", desde=AHORA, hasta=AHORA + timedelta(days=5))
        desde, hasta = periodo(s, "W", 30, ahora=AHORA)
        assert desde == AHORA + timedelta(days=5)
        assert hasta == AHORA + timedelta(days=35)


def test_with_the_pass_already_expired_it_starts_over_today(sf):
    with sf() as s:
        _pase(s, "W", 7, status="active", desde=AHORA - timedelta(days=10),
              hasta=AHORA - timedelta(days=3))
        desde, _ = periodo(s, "W", 7, ahora=AHORA)
        assert desde == AHORA


def test_a_pending_one_does_NOT_shift_the_next_purchase(sf):
    # If it did shift it, a failed charge would leave the player with the pass pushed forward.
    with sf() as s:
        _pase(s, "W", 7, status="pending", desde=AHORA, hasta=AHORA + timedelta(days=7))
        desde, _ = periodo(s, "W", 7, ahora=AHORA)
        assert desde == AHORA
