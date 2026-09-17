"""The tracker stops being public as soon as it's charged for.

Until now `/gacha/ev` was open and the client didn't even send it the token: the gate hid the
screen, not the data. A `curl` would return the whole tracker. It was defensible while it was
free; it stops being defensible the moment someone pays.

What's being protected is the one thing that can't be rebuilt: CC's `getAllWinners` caps out at
200 pulls per machine and there's no way to look further back, so the measurement already taken
is only had by whoever has been listening to the feed since before.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import BattlePlayer, PackBattle, TrackerPass
from app.services.gacha import GachaService
from tests.conftest import TRACKER_PASS_WALLET as WALLET

# The app from the pass purchase (`pase_client`, task 6) is reused here: it already comes with
# Privy configured, the pass prices turned on, and `client.hdrs`/`client.session_factory` ready.
# That it also has money intervened doesn't get in the way here: these tests don't touch any
# charging endpoint.


@pytest.fixture()
def client(pase_client, monkeypatch):
    """The same pass client, but without depending on the Collector Crypt network.

    `/gacha/ev` calls `GachaService.machines()` AFTER checking access. Without this mock it
    would actually attempt an HTTP call to `dev-gacha.example.com`, and the tests for granted
    access would depend on that call responding (or would fail with a 502 that has nothing to do
    with what's being tested here: the gate, not the catalog).
    """
    async def _sin_maquinas(*a, **k):
        return []
    monkeypatch.setattr(GachaService, "machines", _sin_maquinas)
    return pase_client


@pytest.fixture()
def usuario_sin_acceso(client):
    return client                     # neither wager nor pass: the gate stays closed


@pytest.fixture()
def usuario_con_wager(client):
    """100 USDC wagered within the window. A settled battle is seeded."""
    ahora = datetime.now(timezone.utc)
    with client.session_factory() as s:
        s.add(PackBattle(id="b1", mode="pack", status="settled", machine_code="pokemon_50",
                         price=100_000_000, max_players=2, settled_at=ahora))
        s.add(BattlePlayer(battle_id="b1", player_wallet=WALLET))
        s.commit()
    return client


@pytest.fixture()
def usuario_con_pase(client):
    ahora = datetime.now(timezone.utc)
    with client.session_factory() as s:
        s.add(TrackerPass(id="p1", wallet=WALLET, days=7, price_base_units=10_000_000,
                          status="active", starts_at=ahora, ends_at=ahora + timedelta(days=7)))
        s.commit()
    return client


@pytest.fixture()
def usuario_con_pase_caducado(client):
    ahora = datetime.now(timezone.utc)
    with client.session_factory() as s:
        s.add(TrackerPass(id="p2", wallet=WALLET, days=7, price_base_units=10_000_000,
                          status="active", starts_at=ahora - timedelta(days=8),
                          ends_at=ahora - timedelta(seconds=1)))
        s.commit()
    return client


def test_without_a_token_the_tracker_is_CLOSED(client):
    r = client.get("/gacha/ev")
    assert r.status_code == 403
    assert r.json()["detail"] == "tracker_locked"


def test_the_fast_lane_too(client):
    # It carries the streaks, which is our own measurement just like the edge.
    assert client.get("/gacha/ev/live").status_code == 403


def test_with_a_token_but_WITHOUT_access_stays_closed(client, usuario_sin_acceso):
    assert client.get("/gacha/ev", headers=client.hdrs).status_code == 403


def test_with_access_via_WAGER_it_opens(client, usuario_con_wager):
    assert client.get("/gacha/ev", headers=client.hdrs).status_code == 200


def test_with_access_via_PASS_it_opens(client, usuario_con_pase):
    assert client.get("/gacha/ev", headers=client.hdrs).status_code == 200


def test_an_EXPIRED_pass_closes_it_again(client, usuario_con_pase_caducado):
    assert client.get("/gacha/ev", headers=client.hdrs).status_code == 403


def test_the_403_is_NOT_a_disguised_502(client):
    """The screen tells apart "you can't" from "something broke", and it has to be able to keep
    doing that: a 403 shows the gate, a 502 shows the failure notice."""
    assert client.get("/gacha/ev").status_code == 403
