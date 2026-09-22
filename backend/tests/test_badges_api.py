from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.db import init_db, make_session_factory
from app.main import create_app
from app.models import BattlePlayer, PackBattle
from app.services.gacha import GachaService
from app.services.user_tags import add_tag
from tests.test_chain_mock import MockChainSource

A = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"
B = "3q6Ucr1s7Knkp5nRQKQe3dYPzoh72XQGnn2oCgSS9S34"


def _client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)
    app = create_app(sf, MockChainSource(),
                     gacha=GachaService(base_url="https://dev-gacha.example.com", api_key=""),
                     solana_rpc_url="https://api.devnet.solana.com")
    c = TestClient(app, raise_server_exceptions=True)
    c.session_factory = sf
    return c


def _wager(c, wallet, usd):
    with c.session_factory() as s:
        s.add(PackBattle(id=f"b-{wallet}", mode="pack", machine_code="pokemon_50",
                         price=int(usd * 1_000_000), max_players=2, status="settled"))
        s.add(BattlePlayer(battle_id=f"b-{wallet}", player_wallet=wallet))
        s.commit()


def test_badges_for_several_wallets_in_one_call():
    c = _client()
    _wager(c, A, 12_000)
    with c.session_factory() as s:
        add_tag(s, A, "TEAM")
    r = c.get(f"/users/badges?wallets={A},{B}")
    assert r.status_code == 200
    assert r.json() == {A: {"rank": "gold", "tags": ["TEAM"]}, B: {"rank": None, "tags": []}}


def test_badges_ignores_duplicates_and_empty_entries():
    c = _client()
    r = c.get(f"/users/badges?wallets={A},,{A}, ")
    assert r.json() == {A: {"rank": None, "tags": []}}


def test_badges_with_no_wallets_is_empty():
    assert _client().get("/users/badges").json() == {}


def test_badges_caps_the_batch_at_100():
    wallets = ",".join(f"W{i}" for i in range(101))
    assert _client().get(f"/users/badges?wallets={wallets}").status_code == 422


def test_badges_is_not_taken_for_a_wallet():
    """/users/badges must be registered before /users/{wallet}, or it answers as a wallet."""
    body = _client().get("/users/badges?wallets=X").json()
    assert "alias" not in body and body == {"X": {"rank": None, "tags": []}}


def test_user_endpoint_carries_rank_tags_and_progress():
    c = _client()
    _wager(c, A, 7_340)
    with c.session_factory() as s:
        add_tag(s, A, "TEAM")
    u = c.get(f"/users/{A}").json()
    assert u["rank"] == "silver"
    assert u["tags"] == ["TEAM"]
    assert u["rank_progress"] == {"wagered_usd": 7_340.0, "next_rank": "gold",
                                  "next_threshold_usd": 10_000}
    assert "alias" in u and "elo" in u          # nothing removed


def test_user_endpoint_for_an_unknown_wallet_still_has_badges():
    u = _client().get(f"/users/{B}").json()
    assert u["rank"] is None and u["tags"] == []
    assert u["rank_progress"]["next_rank"] == "bronze"
