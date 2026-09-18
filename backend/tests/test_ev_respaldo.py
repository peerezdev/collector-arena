"""What `GET /gacha/ev` shows when Collector Crypt doesn't answer.

The endpoint needs CC for ONE thing: the list of machines with their price and buyback. The
measurements are ours, they live in our database and don't depend on anyone. Even so, until now
a CC failure turned into a 502 and the whole screen said "Couldn't load the tracker", throwing
away data we already had in front of us.

The only protection was two sixty-second caches, so one minute of CC being down was enough to
leave us without a tracker. This is what replaces that: if something has been measured, the last
good one is served with ITS OWN timestamp, and the screen already takes care of marking it as
stale (`estaRancio`, 300 s).
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import app.main as main_mod
from app.db import init_db, make_session_factory
from app.main import create_app
from app.privy import PrivyVerifier
from app.services.gacha import GachaService, GachaDisabled, GachaUpstreamError
from app.services.winners_store import guardar
from tests.conftest import make_es256, privy_auth_headers
from tests.test_chain_mock import MockChainSource

AHORA = datetime.now(timezone.utc)

MAQUINAS = [{"code": "pokemon_50", "name": "Elite Pokémon", "price": 50, "buyback": 0.85,
             "available": True}]

# `/gacha/ev` closed its doors as soon as the tracker started being charged for (task 7): this
# file tests the backup, not access, so the test wallet gets put on the allowlist by default
# (the house's own path, not the wager's or the pass's), so as not to clutter every test with
# seeding a battle or a `TrackerPass` that's beside the point here.
WALLET = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"


@pytest.fixture()
def client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)
    priv = make_es256()
    app_id = "app-test"
    app = create_app(sf, MockChainSource(),
                     gacha=GachaService(base_url="https://dev-gacha.example.com", api_key=""),
                     solana_rpc_url="https://api.devnet.solana.com",
                     privy=PrivyVerifier(app_id=app_id, key_resolver=lambda kid: priv.public_key()),
                     tracker_access_allowlist={WALLET})
    c = TestClient(app, raise_server_exceptions=True)
    c.session_factory = sf
    # Default headers instead of per-call: this way no `client.get(...)` in this file has to be
    # touched to carry the token, and the authenticated wallet matches the one on the allowlist.
    c.headers.update(privy_auth_headers(priv, app_id, WALLET))
    return c


def _sembrar(client, machine="pokemon_50", n=40):
    with client.session_factory() as s:
        guardar(s, [{"nft_address": f"{machine}-{i}", "machine": machine, "prize_tier": 4,
                     "insured_value": 40.0, "weighted_insured_value": None, "memo": None,
                     "winner": "W", "created_at": AHORA - timedelta(minutes=n - i),
                     "source": "live"} for i in range(n)])


def _cc_responde(monkeypatch, maquinas=MAQUINAS):
    async def _ok(*a, **k):
        return maquinas
    monkeypatch.setattr(GachaService, "machines", _ok)


def _cc_caido(monkeypatch, error=None):
    async def _explota(*a, **k):
        raise error or GachaUpstreamError("timeout")
    monkeypatch.setattr(GachaService, "machines", _explota)


def _reloj_adelantado(monkeypatch, sello, segundos=3600):
    """Moves forward the clock `main` sees, which expires the 60 s cache of `/gacha/ev`.

    `main._time` is replaced and not the global `time.time`, so httpx's clock and everyone else's
    stay untouched during the request.
    """
    class RelojAdelantado:
        @staticmethod
        def time():
            return sello + segundos
    monkeypatch.setattr(main_mod, "_time", RelojAdelantado)


def test_the_backup_is_only_served_for_the_window_it_was_measured_over(client, monkeypatch):
    """The cache only ever holds 48 h, so it cannot answer for any other window.

    It is written under `hours == 48` and only then, but the backup used to serve it whatever was
    asked for. `/gacha/ev?hours=6` during an outage came back with 48 hours of pulls and nothing
    in the response said so: the whole point of this tracker is that it does not misstate what it
    measured.
    """
    _sembrar(client)
    _cc_responde(monkeypatch)
    sello = client.get("/gacha/ev").json()["updated_at"]

    _cc_caido(monkeypatch)
    _reloj_adelantado(monkeypatch, sello)
    assert client.get("/gacha/ev?hours=6").status_code == 502
    # And the 48 h one, which is what the cache does hold, still has its backup.
    assert client.get("/gacha/ev").json()["stale"] is True


def test_with_CC_down_it_serves_the_LAST_GOOD_one_instead_of_a_502(client, monkeypatch):
    """The case that used to break the whole screen."""
    _sembrar(client)
    _cc_responde(monkeypatch)
    primera = client.get("/gacha/ev")
    assert primera.status_code == 200, primera.text
    assert primera.json()["rows"], "something measured is needed for there to be a backup"

    _cc_caido(monkeypatch)
    _reloj_adelantado(monkeypatch, primera.json()["updated_at"])   # expires the 60 s cache
    r = client.get("/gacha/ev")
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == primera.json()["rows"]


def test_the_backup_keeps_ITS_OWN_time_and_does_not_pretend_to_be_freshly_measured(client, monkeypatch):
    """It's what keeps the backup honest.

    The screen decides something is stale by comparing `updated_at` against the clock
    (`estaRancio`, 300 s). If we set the current time on the old data being served, the STALE
    warning would NEVER trigger and we'd be showing measurements from hours ago as if they were
    from right now.

    THE CLOCK NEEDS TO BE MOVED FORWARD. Without this the test passes just the same even if the
    endpoint sets the current time, because both readings land in the same second: verified by
    mutating the code, the first version of this test wasn't holding anything down.

    The `_time` that `main` sees gets substituted, not the global `time.time`, so as not to mess
    with httpx's clock or anyone else's during the request.
    """
    _sembrar(client)
    _cc_responde(monkeypatch)
    sello = client.get("/gacha/ev").json()["updated_at"]

    _reloj_adelantado(monkeypatch, sello)

    _cc_caido(monkeypatch)
    cuerpo = client.get("/gacha/ev").json()
    assert cuerpo["updated_at"] == sello, "the backup must carry the time it was measured"
    assert cuerpo["updated_at"] < sello + 300, "and therefore the screen will mark it STALE"


def test_the_backup_is_marked_as_such(client, monkeypatch):
    """So as to be able to tell apart, in the log and in tests, a fresh measurement from one served as backup."""
    _sembrar(client)
    _cc_responde(monkeypatch)
    sello = client.get("/gacha/ev").json()["updated_at"]
    assert client.get("/gacha/ev").json().get("stale") is not True

    _cc_caido(monkeypatch)
    _reloj_adelantado(monkeypatch, sello)                          # expires the 60 s cache
    assert client.get("/gacha/ev").json()["stale"] is True


def test_with_NOTHING_measured_yet_it_is_still_a_502(client, monkeypatch):
    """There's no backup to serve, so lying with an empty list would be worse: the screen would say
    "no machines measured yet" and it would look like a data problem instead of a network one."""
    _cc_caido(monkeypatch)
    r = client.get("/gacha/ev")
    assert r.status_code == 502


def test_the_gacha_turned_OFF_on_purpose_does_not_disguise_itself_as_a_backup(client, monkeypatch):
    """An empty `gacha_base_url` is the kill switch, a decision of ours and not a CC outage.

    Continuing to serve the tracker with stored data after turning off the gacha by hand would
    make the switch not really turn it off.
    """
    _sembrar(client)
    _cc_responde(monkeypatch)
    r = client.get("/gacha/ev")
    assert r.status_code == 200

    _cc_caido(monkeypatch, GachaDisabled("gacha_disabled"))
    _reloj_adelantado(monkeypatch, r.json()["updated_at"])
    assert client.get("/gacha/ev").status_code == 503


def test_when_CC_comes_back_the_backup_stops_being_served(client, monkeypatch):
    """The backup is a bridge, not a destination."""
    _sembrar(client)
    _cc_responde(monkeypatch)
    sello = client.get("/gacha/ev").json()["updated_at"]

    _cc_caido(monkeypatch)
    _reloj_adelantado(monkeypatch, sello)
    assert client.get("/gacha/ev").json()["stale"] is True

    _cc_responde(monkeypatch)
    _reloj_adelantado(monkeypatch, sello, 7200)
    assert client.get("/gacha/ev").json().get("stale") is not True
