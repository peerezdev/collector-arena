"""Tests for Pack Battle lobby REST endpoints (Task 5).

Mirrors test_gacha_api.py scaffolding:
- In-memory SQLite DB
- Fake Privy with a key_resolver (no network)
- TestClient
- Authorization: Bearer <token>

Monkeypatches:
- usdc_balance_base_units (high balance → pass, low → 402)
- gacha.machines (returns a single machine)
- run_pack_battle_live (async stub — asserts it was scheduled, does NOT run it)
"""
from __future__ import annotations

import asyncio
import json
import time
import pytest

from cryptography.hazmat.primitives.asymmetric import ec
import jwt

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.main import create_app
from app.db import make_session_factory, init_db
from app.privy import PrivyVerifier
from app.chain.mock import MockChainSource
from app.services.gacha import GachaService

APP_ID = "testapp"

# ── Wallets usadas en los tests ──────────────────────────────────────────────
# Must be valid base-58 Solana-like addresses (44 chars)
WALLET_A = "So1anaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"
WALLET_B = "So1anaBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB1"
WALLET_ID_A = "wallet-id-aaa"
WALLET_ID_B = "wallet-id-bbb"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_es256():
    return ec.generate_private_key(ec.SECP256R1())


def _solana_embedded_with_id(addr: str, wallet_id: str) -> dict:
    """linked_account entry that carries both address AND id (needed for wallet_id endpoint)."""
    return {
        "type": "wallet",
        "chain_type": "solana",
        "connector_type": None,
        "wallet_client_type": "privy",
        "address": addr,
        "id": wallet_id,
    }


def _make_token(priv, app_id: str, addr: str, wallet_id: str) -> str:
    now = int(time.time())
    payload = {
        "aud": app_id,
        "iss": "privy.io",
        "sub": f"did:privy:{addr[:8]}",
        "iat": now,
        "exp": now + 3600,
        "linked_accounts": json.dumps([_solana_embedded_with_id(addr, wallet_id)]),
    }
    return jwt.encode(payload, priv, algorithm="ES256", headers={"kid": "test-kid", "alg": "ES256"})


def _auth_headers(priv, addr: str, wallet_id: str) -> dict:
    token = _make_token(priv, APP_ID, addr, wallet_id)
    return {"Authorization": f"Bearer {token}"}


# ── App builder ───────────────────────────────────────────────────────────────
# We pass a dummy usdc_mint that is a valid Solana pubkey so Pubkey.from_string doesn't blow up.
# The actual balance check is monkeypatched so no RPC call is made.
DUMMY_RPC = "https://api.devnet.solana.com"
DUMMY_MINT = "Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr"
# Mint DISTINTO del de USDC a propósito: el test que más importa es el que comprueba que un
# withdraw de CARDS usa ESTE mint y no el de arriba — mezclarlos mandaría el token equivocado.
DUMMY_CARDS_MINT = "CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp"


def _build_client(signer=None, dev_endpoints_enabled=False, withdraw_fee_pct=0.0, fee_wallet_address="",
                  royale_creator_allowlist=None, cards_airdrop_mint="", min_withdraw_cards=1.0):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    sf = make_session_factory(engine)
    priv = _make_es256()
    privy = PrivyVerifier(app_id=APP_ID, key_resolver=lambda kid: priv.public_key())
    gacha = GachaService(base_url="https://dev-gacha.example.com", api_key="")
    app = create_app(
        sf,
        MockChainSource(),
        gacha=gacha,
        privy=privy,
        privy_signer=signer,                # NEW: inject (None by default, as before)
        solana_rpc_url=DUMMY_RPC,
        cc_usdc_mint=DUMMY_MINT,
        privy_operator_wallet_id="op-wallet-id",
        privy_operator_address="So1anaOPERATOR1111111111111111111111111111",
        escrow_seed_lamports=10_000_000,
        dev_endpoints_enabled=dev_endpoints_enabled,
        withdraw_fee_pct=withdraw_fee_pct,
        fee_wallet_address=fee_wallet_address,
        royale_creator_allowlist=royale_creator_allowlist,
        cards_airdrop_mint=cards_airdrop_mint,
        min_withdraw_cards=min_withdraw_cards,
    )
    return TestClient(app, raise_server_exceptions=True), priv


def _build_client_with_sf(signer=None, cards_airdrop_mint=""):
    """Like _build_client, but also returns the session_factory so a test can seed rows directly
    (needed to exercise the startup sweep against pre-existing 'voided' battles)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    sf = make_session_factory(engine)
    priv = _make_es256()
    privy = PrivyVerifier(app_id=APP_ID, key_resolver=lambda kid: priv.public_key())
    gacha = GachaService(base_url="https://dev-gacha.example.com", api_key="")
    app = create_app(
        sf, MockChainSource(), gacha=gacha, privy=privy, privy_signer=signer,
        solana_rpc_url=DUMMY_RPC, cc_usdc_mint=DUMMY_MINT,
        privy_operator_wallet_id="op-wallet-id",
        privy_operator_address="So1anaOPERATOR1111111111111111111111111111",
        escrow_seed_lamports=10_000_000,
        cards_airdrop_mint=cards_airdrop_mint,
    )
    return sf, TestClient(app, raise_server_exceptions=True), priv


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client_priv():
    return _build_client()


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_create_battle_returns_hash_not_seed(client_priv, monkeypatch):
    """POST /pack-battles → 200, returns server_seed_hash, does NOT reveal server_seed."""
    c, priv = client_priv

    async def _high_balance(*args, **kwargs):
        return 100_000_000  # 100 USDC in base units — well above price

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "id" in body
    assert "server_seed_hash" in body
    assert "server_seed" not in body  # must NOT be revealed pre-settle
    assert body["status"] == "lobby"


def test_rematch_guards(client_priv, monkeypatch):
    """Rematch requires a participant and a finished battle."""
    c, priv = client_priv

    async def _high_balance(*args, **kwargs):
        return 100_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())

    hdrs_a = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    bid = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs_a).json()["id"]

    # battle still in lobby (not finished) → 409, even for a participant
    assert c.post(f"/pack-battles/{bid}/rematch", headers=hdrs_a).status_code == 409
    # non-participant → 403
    hdrs_b = _auth_headers(priv, WALLET_B, WALLET_ID_B)
    assert c.post(f"/pack-battles/{bid}/rematch", headers=hdrs_b).status_code == 403
    # unknown battle → 404
    assert c.post("/pack-battles/does-not-exist/rematch", headers=hdrs_a).status_code == 404


def test_battle_emote_guards(client_priv, monkeypatch):
    """Emote broadcast requires owning the emote + being a participant, and is rate-limited."""
    c, priv = client_priv

    async def _high_balance(*args, **kwargs):
        return 100_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())

    hdrs_a = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    bid = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs_a).json()["id"]
    c.get("/users/me/emotes", headers=hdrs_a)  # grants the default emotes to A

    # owns + participant → 200
    assert c.post(f"/pack-battles/{bid}/emote", json={"code": "charmander"}, headers=hdrs_a).status_code == 200
    # not owned → 403 (checked before the rate-limit)
    assert c.post(f"/pack-battles/{bid}/emote", json={"code": "ghost"}, headers=hdrs_a).status_code == 403
    # owned but too soon → 429 rate-limit
    assert c.post(f"/pack-battles/{bid}/emote", json={"code": "bulbasaur"}, headers=hdrs_a).status_code == 429
    # non-participant → 403
    hdrs_b = _auth_headers(priv, WALLET_B, WALLET_ID_B)
    assert c.post(f"/pack-battles/{bid}/emote", json={"code": "charmander"}, headers=hdrs_b).status_code == 403


def test_second_player_join_schedules_run(client_priv, monkeypatch):
    """Second player joining a 2-player lobby fills it → run_pack_battle_live is scheduled."""
    c, priv = client_priv

    run_called: list = []

    async def _high_balance(*args, **kwargs):
        return 100_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    async def _fake_run(session, battle, *, gacha, signer, **kwargs):
        run_called.append(battle.id)

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.run_pack_battle_live", _fake_run)

    # Player A creates the battle
    hdrs_a = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r_create = c.post(
        "/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs_a
    )
    assert r_create.status_code == 200, r_create.text
    battle_id = r_create.json()["id"]

    # Player B joins — this fills the lobby (max_players=2)
    hdrs_b = _auth_headers(priv, WALLET_B, WALLET_ID_B)
    r_join = c.post(f"/pack-battles/{battle_id}/join", headers=hdrs_b)
    assert r_join.status_code == 200, r_join.text

    # Give the event loop a tick so the asyncio.create_task fires
    async def _drain():
        await asyncio.sleep(0)

    asyncio.get_event_loop().run_until_complete(_drain())

    # The stub should have been called (task was scheduled)
    assert run_called, "run_pack_battle_live was not scheduled after lobby filled"


def test_get_open_battles(client_priv, monkeypatch):
    """GET /pack-battles/open lists open lobbies."""
    c, priv = client_priv

    async def _high_balance(*args, **kwargs):
        return 100_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 3}, headers=hdrs)

    r = c.get("/pack-battles/open")
    assert r.status_code == 200, r.text
    battles = r.json()
    assert isinstance(battles, list)
    assert len(battles) >= 1
    assert battles[0]["machine_code"] == "pokemon_50"


def test_get_battle_no_server_seed_pre_settle(client_priv, monkeypatch):
    """GET /pack-battles/{id} returns state without server_seed while not settled."""
    c, priv = client_priv

    async def _high_balance(*args, **kwargs):
        return 100_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r_create = c.post(
        "/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs
    )
    assert r_create.status_code == 200
    battle_id = r_create.json()["id"]

    r = c.get(f"/pack-battles/{battle_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == battle_id
    assert "server_seed_hash" in body
    assert "server_seed" not in body  # not settled yet


def test_join_insufficient_usdc_returns_402(client_priv, monkeypatch):
    """Joining with insufficient USDC balance → 402."""
    c, priv = client_priv

    call_count = 0

    async def _balance_varies(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # First call (create): high balance; subsequent calls (join): low balance
        if call_count <= 1:
            return 100_000_000
        return 0  # zero balance for join attempt

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _balance_varies)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())

    # Player A creates (has funds)
    hdrs_a = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r_create = c.post(
        "/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs_a
    )
    assert r_create.status_code == 200, r_create.text
    battle_id = r_create.json()["id"]

    # Player B tries to join but has zero USDC
    hdrs_b = _auth_headers(priv, WALLET_B, WALLET_ID_B)
    r_join = c.post(f"/pack-battles/{battle_id}/join", headers=hdrs_b)
    assert r_join.status_code == 402, r_join.text


# ── Royale mode tests ─────────────────────────────────────────────────────────

def _make_royale_app(escrow_created_list=None, escrow_address="So1anaESCROWXXXXXXXXXXXXXXXXXXXXXXXXXXX1",
                     royale_creator_allowlist=None):
    """Build a fresh TestClient+priv pair with a fake signer that records escrow creation."""
    from app.db import make_session_factory, init_db
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.main import create_app
    from app.privy import PrivyVerifier
    from app.chain.mock import MockChainSource
    from app.services.gacha import GachaService
    from fastapi.testclient import TestClient

    counter = [0]

    class FakeSigner:
        # Delegado: estos tests van de otra cosa. Los de la puerta de delegación viven aparte.
        async def podemos_firmar(self, wallet_id):
            return True

        async def create_solana_wallet(self):
            counter[0] += 1
            if escrow_created_list is not None:
                escrow_created_list.append(True)
            return {"id": f"escrow-wid-{counter[0]}", "address": escrow_address}

    engine2 = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine2)
    sf2 = make_session_factory(engine2)
    priv2 = _make_es256()
    privy2 = PrivyVerifier(app_id=APP_ID, key_resolver=lambda kid: priv2.public_key())
    gacha2 = GachaService(base_url="https://dev-gacha.example.com", api_key="")

    app2 = create_app(
        sf2, MockChainSource(), gacha=gacha2, privy=privy2,
        privy_signer=FakeSigner(),
        solana_rpc_url=DUMMY_RPC, cc_usdc_mint=DUMMY_MINT,
        privy_operator_wallet_id="op-wallet-id",
        privy_operator_address="So1anaOPERATOR1111111111111111111111111111",
        escrow_seed_lamports=10_000_000,
        royale_creator_allowlist=royale_creator_allowlist,
    )
    return TestClient(app2, raise_server_exceptions=True), priv2


def test_royale_create_returns_200_with_buyin_and_escrow(client_priv, monkeypatch):
    """POST /pack-battles with mode=royale → 200; body has buyin and escrow_address; escrow is pre-created."""
    async def _high_balance(*args, **kwargs):
        return 200_000_000  # well above any buyin

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    async def _fake_collect_buyin(*args, **kwargs):
        return "fake-sig"

    async def _fake_blockhash(rpc_url: str) -> str:
        return "FakeBH444444444444444444444444444444444444444"

    escrow_created = []
    c2, priv2 = _make_royale_app(escrow_created_list=escrow_created)

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.collect_buyin", _fake_collect_buyin)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _fake_blockhash)

    hdrs = _auth_headers(priv2, WALLET_A, WALLET_ID_A)
    r = c2.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 5, "mode": "royale"}, headers=hdrs)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("mode") == "royale"
    assert "buyin" in body, f"buyin not in response: {body}"
    assert body["buyin"] > 0
    # escrow must be pre-created (create_solana_wallet was called)
    assert escrow_created, "signer.create_solana_wallet was not called at royale create time"
    assert body.get("escrow_address"), "escrow_address not in response"


def test_royale_join_collects_buyin(client_priv, monkeypatch):
    """Joining a royale battle calls collect_buyin once per new player."""
    async def _high_balance(*args, **kwargs):
        return 200_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    buyin_calls: list = []

    async def _fake_collect_buyin(*args, **kwargs):
        buyin_calls.append(args)
        return "fake-sig"

    async def _fake_blockhash(rpc_url: str) -> str:
        return "FakeBH111111111111111111111111111111111111111"

    c2, priv2 = _make_royale_app()

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.collect_buyin", _fake_collect_buyin)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _fake_blockhash)

    # Player A creates royale battle
    hdrs_a = _auth_headers(priv2, WALLET_A, WALLET_ID_A)
    r = c2.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 5, "mode": "royale"}, headers=hdrs_a)
    assert r.status_code == 200, r.text
    battle_id = r.json()["id"]

    # Player B joins — collect_buyin should be called again (once for creator at create, once for joiner B)
    hdrs_b = _auth_headers(priv2, WALLET_B, WALLET_ID_B)
    r2 = c2.post(f"/pack-battles/{battle_id}/join", headers=hdrs_b)
    assert r2.status_code == 200, r2.text
    assert len(buyin_calls) == 2, f"collect_buyin called {len(buyin_calls)} times, expected 2 (1 for creator at create + 1 for joiner B)"


def test_royale_creator_buyin_collected_at_create(client_priv, monkeypatch):
    """POST /pack-battles with mode=royale → collect_buyin is called for the creator immediately."""
    async def _high_balance(*args, **kwargs):
        return 200_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    buyin_calls: list = []

    async def _fake_collect_buyin(*args, **kwargs):
        buyin_calls.append(args)
        return "fake-sig"

    async def _fake_blockhash(rpc_url: str) -> str:
        return "FakeBH333333333333333333333333333333333333333"

    c2, priv2 = _make_royale_app()

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.collect_buyin", _fake_collect_buyin)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _fake_blockhash)

    # Player A creates royale battle
    hdrs_a = _auth_headers(priv2, WALLET_A, WALLET_ID_A)
    r = c2.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 5, "mode": "royale"}, headers=hdrs_a)
    assert r.status_code == 200, r.text

    # collect_buyin should have been called once (for the creator) right at create time
    assert len(buyin_calls) == 1, f"collect_buyin called {len(buyin_calls)} times, expected 1 for creator"
    # The creator's wallet (WALLET_A) and wallet_id (WALLET_ID_A) must be in the call args
    creator_call_args = buyin_calls[0]
    assert WALLET_ID_A in creator_call_args, f"WALLET_ID_A not in collect_buyin args: {creator_call_args}"
    assert WALLET_A in creator_call_args, f"WALLET_A not in collect_buyin args: {creator_call_args}"


def test_royale_fill_schedules_run_royale_live(client_priv, monkeypatch):
    """When a royale lobby fills, run_royale_live (not run_pack_battle_live) is scheduled."""
    async def _high_balance(*args, **kwargs):
        return 200_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    async def _fake_collect_buyin(*args, **kwargs):
        return "fake-sig"

    async def _fake_blockhash(rpc_url: str) -> str:
        return "FakeBH222222222222222222222222222222222222222"

    royale_scheduled: list = []

    async def _fake_run_royale_live(session, battle, **kwargs):
        royale_scheduled.append(battle.id)

    pack_scheduled: list = []

    async def _fake_run_pack_live(session, battle, **kwargs):
        pack_scheduled.append(battle.id)

    c2, priv2 = _make_royale_app()

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.collect_buyin", _fake_collect_buyin)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _fake_blockhash)
    monkeypatch.setattr("app.main.run_royale_live", _fake_run_royale_live)
    monkeypatch.setattr("app.main.run_pack_battle_live", _fake_run_pack_live)

    hdrs_a = _auth_headers(priv2, WALLET_A, WALLET_ID_A)
    r = c2.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 5, "mode": "royale"}, headers=hdrs_a)
    assert r.status_code == 200, r.text
    battle_id = r.json()["id"]

    # Fill the remaining 4 seats (royale minimum is 5) so the lobby fills and schedules the run.
    for addr, wid in [
        (WALLET_B, WALLET_ID_B),
        ("So1anaCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC1", "wallet-id-ccc"),
        ("So1anaDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD1", "wallet-id-ddd"),
        ("So1anaEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE1", "wallet-id-eee"),
    ]:
        rj = c2.post(f"/pack-battles/{battle_id}/join", headers=_auth_headers(priv2, addr, wid))
        assert rj.status_code == 200, rj.text

    # Drain the event loop so the task fires
    asyncio.get_event_loop().run_until_complete(asyncio.sleep(0))

    assert royale_scheduled, "run_royale_live was not scheduled after royale lobby filled"
    assert not pack_scheduled, "run_pack_battle_live should NOT be called for royale mode"


def test_available_balance_blocks_overcommit(client_priv, monkeypatch):
    """With on-chain funds for exactly ONE price, a second Pack Battle create is 402."""
    c, priv = client_priv

    async def _one_price_balance(*args, **kwargs):
        return 50_000_000  # exactly $50 — one pack price

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _one_price_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r1 = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs)
    assert r1.status_code == 200, r1.text            # first reserves $50 → available now 0
    r2 = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs)
    assert r2.status_code == 402, r2.text            # over-commit blocked


def test_reservations_released_after_run(client_priv, monkeypatch):
    """After a filled lobby runs (stubbed), the wiring releases its reservations — proven by the
    creator being able to create a SECOND battle while holding funds for only ONE price."""
    c, priv = client_priv

    async def _one_price(*args, **kwargs):
        return 50_000_000   # exactly one $50 price → only affordable if the first reservation freed

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    async def _fake_run(session, battle, *, gacha, signer, **kwargs):
        return "settled"

    monkeypatch.setattr("app.main.usdc_balance_base_units", _one_price)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.run_pack_battle_live", _fake_run)

    import asyncio
    hdrs_a = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    bid = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs_a).json()["id"]
    hdrs_b = _auth_headers(priv, WALLET_B, WALLET_ID_B)
    c.post(f"/pack-battles/{bid}/join", headers=hdrs_b)   # fills → schedules _run_bg (stubbed) → release

    asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.1))   # let _run_bg + its finally run

    # A's reservation for the finished battle was released → a second create now succeeds.
    r = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs_a)
    assert r.status_code == 200, r.text


def test_pack_cancel_releases_reservation_creator_only(client_priv, monkeypatch):
    c, priv = client_priv

    async def _exact_one_price(*args, **kwargs):
        return 50_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _exact_one_price)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())

    hdrs_a = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    bid = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs_a).json()["id"]

    # Non-creator cannot cancel
    hdrs_b = _auth_headers(priv, WALLET_B, WALLET_ID_B)
    assert c.post(f"/pack-battles/{bid}/cancel", headers=hdrs_b).status_code == 409

    # Creator cancels → cancelled
    r = c.post(f"/pack-battles/{bid}/cancel", headers=hdrs_a)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"

    # Reservation was released: a new battle with the same wallet must succeed (available = price again)
    r2 = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs_a)
    assert r2.status_code == 200, f"second create failed ({r2.status_code}) — reservation not released"


class _FakeSigner:
    # Delegado: estos tests van de otra cosa. Los de la puerta de delegación viven aparte.
    async def podemos_firmar(self, wallet_id):
        return True

    async def create_solana_wallet(self):
        return {"id": "esc-id", "address": "So1anaESCROW111111111111111111111111111111"}


def test_royale_cancel_refunds_buyins(monkeypatch):
    # The royale create path calls privy_signer.create_solana_wallet(), so this test builds a
    # client WITH a fake signer (the default _build_client() passes privy_signer=None).
    c, priv = _build_client(signer=_FakeSigner())
    refunds = []

    async def _high(*args, **kwargs):
        return 1_000_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    async def _collect(*args, **kwargs):
        return "collect-sig"

    async def _bh(*args, **kwargs):
        return "11111111111111111111111111111111"

    async def _refund(rpc, signer, ewid, eaddr, opid, opaddr, player, mint, amount, bh):
        refunds.append((player, amount)); return "refund-sig"

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.collect_buyin", _collect)
    monkeypatch.setattr("app.main.refund_buyin", _refund)

    hdrs_a = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    res = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 5, "mode": "royale"}, headers=hdrs_a)
    assert res.status_code == 200, res.text
    bid = res.json()["id"]

    r = c.post(f"/pack-battles/{bid}/cancel", headers=hdrs_a)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    assert len(refunds) == 1                          # only the creator had joined
    assert refunds[0][0] == WALLET_A


def test_cancelar_un_lobby_devuelve_su_escrow_al_pool(monkeypatch):
    """Las royale crean el escrow al ABRIR el lobby, así que un lobby cancelado tenía una wallet
    reservada para siempre. Eran 26 de las 79 wallets históricas: el mayor derroche, y el pool no lo
    tocaba porque nadie liberaba en este camino."""
    from app.models import EscrowWallet
    sf, c, priv = _build_client_with_sf(signer=_FakeSigner())

    async def _high(*args, **kwargs):
        return 1_000_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    async def _collect(*args, **kwargs):
        return "collect-sig"

    async def _bh(*args, **kwargs):
        return "11111111111111111111111111111111"

    async def _refund(*args, **kwargs):
        return "refund-sig"

    async def _vacio(rpc_url, address, usdc_mint):
        return None            # el escrow queda limpio tras devolver los buy-ins

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.collect_buyin", _collect)
    monkeypatch.setattr("app.main.refund_buyin", _refund)
    monkeypatch.setattr("app.services.escrow_pool.motivo_retencion", _vacio)

    hdrs_a = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    res = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 5,
                                        "mode": "royale"}, headers=hdrs_a)
    bid = res.json()["id"]
    esc = res.json()["escrow_address"]

    with sf() as s:
        assert s.get(EscrowWallet, esc).status == "in_use"

    assert c.post(f"/pack-battles/{bid}/cancel", headers=hdrs_a).status_code == 200

    with sf() as s:
        fila = s.get(EscrowWallet, esc)
        assert fila.status == "free", "el escrow de un lobby cancelado tiene que volver al pool"
        assert fila.battle_id is None


def test_cancel_refundea_al_jugador_que_entro_durante_el_cancel(monkeypatch):
    """Simula el interleaving: un jugador se une DESPUÉS del (viejo) snapshot pero ANTES del flip.
    Con el fix, el snapshot es post-flip y ese jugador queda incluido en los refunds."""
    import app.main as m
    from app.services.pack_lobby import cancel_battle as real_cancel
    from app.models import BattlePlayer

    late_added = {}

    def cancel_and_sneak_join(s, battle_id, wallet):
        # el "otro request" inserta su BattlePlayer justo antes del flip
        if not late_added.get(battle_id):
            s.add(BattlePlayer(battle_id=battle_id, player_wallet="LATE_JOINER", wallet_id="late-id"))
            s.commit()
            late_added[battle_id] = True
        return real_cancel(s, battle_id, wallet)
    monkeypatch.setattr(m, "cancel_battle", cancel_and_sneak_join)

    refunds = []

    async def fake_refund_buyin(rpc, signer, ewid, eaddr, owid, oaddr, player, mint, amount, bh):
        refunds.append(player); return "sig"
    monkeypatch.setattr(m, "refund_buyin", fake_refund_buyin)

    async def fake_bh(rpc):
        return "B" * 32
    monkeypatch.setattr(m, "fetch_latest_blockhash", fake_bh)

    # Crear una royale en lobby con creador CREATOR (mismo patrón de test_royale_cancel_refunds_buyins)
    c, priv = _build_client(signer=_FakeSigner())

    async def _high(*args, **kwargs):
        return 1_000_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    async def _collect(*args, **kwargs):
        return "collect-sig"

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.collect_buyin", _collect)

    hdrs = _auth_headers(priv, "CREATOR", "creator-id")
    res = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 5, "mode": "royale"}, headers=hdrs)
    assert res.status_code == 200, res.text
    bid = res.json()["id"]

    r = c.post(f"/pack-battles/{bid}/cancel", headers=hdrs)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    assert "LATE_JOINER" in refunds and "CREATOR" in refunds


def test_verify_endpoint_pre_settle_and_404(client_priv, monkeypatch):
    c, priv = client_priv

    async def _high(*args, **kwargs):
        return 100_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())

    assert c.get("/pack-battles/does-not-exist/verify").status_code == 404

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    bid = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs).json()["id"]
    r = c.get(f"/pack-battles/{bid}/verify")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["server_seed_hash"] and body["server_seed"] is None   # pre-settle


def test_me_balance_returns_reserved(client_priv, monkeypatch):
    c, priv = client_priv

    async def _high_balance(*args, **kwargs):
        return 100_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)

    # no reservations yet
    r0 = c.get("/users/me/balance", headers=hdrs)
    assert r0.status_code == 200 and r0.json() == {"reserved": 0, "locked_royale": 0}

    # creating a pack battle reserves the price (50 * 1e6 base units)
    c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs)
    r1 = c.get("/users/me/balance", headers=hdrs)
    assert r1.json() == {"reserved": 50_000_000, "locked_royale": 0}


def test_me_balance_requires_auth(client_priv):
    c, _ = client_priv
    assert c.get("/users/me/balance").status_code == 401


def test_open_battles_include_creator_wallet(client_priv, monkeypatch):
    c, priv = client_priv

    async def _high_balance(*args, **kwargs):
        return 100_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs)
    row = c.get("/pack-battles/open").json()[0]
    assert row["creator_wallet"] == WALLET_A


def test_create_multipack_bundle(client_priv, monkeypatch):
    c, priv = client_priv

    async def _high_balance(*args, **kwargs):
        return 1_000_000_000

    async def _machines():
        return [{"code": "m25", "price": 25, "available": True},
                {"code": "m50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)

    r = c.post("/pack-battles", json={"max_players": 2,
               "packs": [{"machine_code": "m25", "count": 1}, {"machine_code": "m50", "count": 2}]},
               headers=hdrs)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["price"] == 125_000_000   # 25 + 50 + 50, base units
    assert body["packs"] == [
        {"machine_code": "m25", "sequence": 1, "price": 25_000_000},
        {"machine_code": "m50", "sequence": 2, "price": 50_000_000},
        {"machine_code": "m50", "sequence": 3, "price": 50_000_000}]
    # the creator reserved the total
    assert c.get("/users/me/balance", headers=hdrs).json() == {"reserved": 125_000_000, "locked_royale": 0}


def test_create_multipack_rejects_over_ten_boxes(client_priv, monkeypatch):
    c, priv = client_priv

    async def _high_balance(*args, **kwargs):
        return 1_000_000_000

    async def _machines():
        return [{"code": "m25", "price": 25, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/pack-battles", json={"max_players": 2, "packs": [{"machine_code": "m25", "count": 11}]},
               headers=hdrs)
    assert r.status_code == 422, r.text


def test_create_legacy_single_machine_still_works(client_priv, monkeypatch):
    c, priv = client_priv

    async def _high_balance(*args, **kwargs):
        return 1_000_000_000

    async def _machines():
        return [{"code": "m50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/pack-battles", json={"machine_code": "m50", "max_players": 2}, headers=hdrs)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["price"] == 50_000_000
    assert body["packs"] == [{"machine_code": "m50", "sequence": 1, "price": 50_000_000}]


# ── A1: /pack-battles/{id}/join-bot must be disabled outside dev ───────────────

def test_join_bot_disabled_by_default():
    """SECURITY (A1): the unauthenticated, fund-moving join-bot endpoint must 404
    when DEV_ENDPOINTS_ENABLED is off (the default), so it cannot be reached in prod.
    The gate runs before the battle lookup, so the 404 detail is FastAPI's own "Not Found",
    not our "battle not found"."""
    c, _priv = _build_client()  # dev_endpoints_enabled=False by default
    r = c.post("/pack-battles/whatever-id/join-bot")
    assert r.status_code == 404
    assert r.json()["detail"] == "Not Found"


def test_join_bot_enabled_passes_gate_in_dev():
    """With DEV_ENDPOINTS_ENABLED on, the gate is open: the handler proceeds to the
    battle lookup. A nonexistent battle then yields 404 "battle not found" — proving the
    request got PAST the dev gate (different detail than the disabled case)."""
    c, _priv = _build_client(dev_endpoints_enabled=True)
    r = c.post("/pack-battles/nonexistent/join-bot")
    assert r.status_code == 404
    assert r.json()["detail"] == "battle not found"


# ── /dev/announce: chat-event demo endpoint is dev-gated ──────────────────────

def test_dev_announce_disabled_by_default():
    """The chat-announcement demo endpoint 404s when DEV_ENDPOINTS_ENABLED is off."""
    c, _priv = _build_client()
    r = c.post("/dev/announce", json={"event": "hit", "user": "neo", "text": "pulled Charizard", "amountUsd": 320})
    assert r.status_code == 404
    assert r.json()["detail"] == "Not Found"


def test_dev_announce_enabled_ok_in_dev():
    """With dev endpoints on, /dev/announce accepts a sample announcement and returns ok."""
    c, _priv = _build_client(dev_endpoints_enabled=True)
    r = c.post("/dev/announce", json={"event": "winner", "user": "mole", "text": "won a Pack Battle",
                                      "amountUsd": 1240, "mode": "pack", "action_label": "View"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── A2: /users/me/withdraw anti-drain (minimum amount + per-wallet rate limit) ─

def test_withdraw_below_minimum_rejected():
    """SECURITY (A2): a sub-minimum withdrawal is rejected (422) BEFORE any on-chain
    work, so an attacker can't flood 1-base-unit withdrawals to fresh addresses and
    drain the operator-paid destination-ATA rent. Default minimum is 1.0 USDC."""
    c, priv = _build_client(signer=object())  # truthy signer passes the 503 gate
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 0.000001}, headers=hdrs)
    assert r.status_code == 422, r.text
    assert "minimum withdrawal" in r.json()["detail"]


def test_withdraw_permitido_con_royale_esperando_en_lobby(monkeypatch):
    """Una royale que aún no ha arrancado NO cierra el retiro.

    Su buy-in ya salió de la wallet al entrar (está en el escrow) y todavía no ha habido ningún
    reparto, así que lo que le queda es suyo y no tiene destino. Antes se bloqueaba igual, y eso
    dejaba a un jugador sin poder tocar su dinero mientras esperaba a que la sala se llenase.

    El caso peligroso —la misma royale en `running`— lo cubre el test de abajo.
    """
    from app.models import PackBattle, BattlePlayer
    sf, c, priv = _build_client_with_sf(signer=object())

    async def _high_balance(*a, **k):
        return 1_000_000_000

    async def _bh(*a, **k):
        return "11111111111111111111111111111111"

    async def _wd(*a, **k):
        return "sig-stub"

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.withdraw_usdc_with_fee", _wd)
    monkeypatch.setattr("app.main.withdraw_usdc", _wd)

    with sf() as s:
        s.add(PackBattle(id="r-lobby", mode="royale", machine_code="m", price=25_000_000,
                         max_players=5, status="lobby"))
        s.add(BattlePlayer(battle_id="r-lobby", player_wallet=WALLET_A))
        s.commit()

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 1.0}, headers=hdrs)
    assert r.status_code == 200, r.text


def test_withdraw_bloqueado_con_pack_esperando_en_lobby(monkeypatch):
    """La pack battle sí cierra el retiro aunque esté solo esperando: a diferencia del royale,
    su buy-in NO ha salido de la wallet — es con lo que el jugador va a pagar su caja."""
    from app.models import PackBattle, BattlePlayer
    sf, c, priv = _build_client_with_sf(signer=object())

    async def _high_balance(*a, **k):
        return 1_000_000_000

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)

    with sf() as s:
        s.add(PackBattle(id="p-lobby", mode="pack", machine_code="m", price=25_000_000,
                         max_players=2, status="lobby"))
        s.add(BattlePlayer(battle_id="p-lobby", player_wallet=WALLET_A))
        s.commit()

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 1.0}, headers=hdrs)
    assert r.status_code == 409, r.text


def test_withdraw_bloqueado_con_partida_sin_terminar(monkeypatch):
    """SEGURIDAD: con una partida en curso el saldo de la wallet todavía tiene destino.

    En una royale el escrow le manda al jugador el precio de cada caja justo ANTES de tirar, y ese
    importe no lleva reserva —el buy-in ya salió al entrar—, así que `on-chain − reservado` no lo
    protege. Sin esta puerta se podría sacar ese dinero en la ventana entre el reparto y la tirada:
    la tirada fallaría, la partida se anularía y el escrow quedaría corto justo por esa cantidad,
    de modo que el agujero lo pagarían los reembolsos de los OTROS jugadores.
    """
    from app.models import PackBattle, BattlePlayer
    sf, c, priv = _build_client_with_sf(signer=object())

    async def _high_balance(*a, **k):
        return 1_000_000_000

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    body = {"address": WALLET_B, "amount": 1.0}

    with sf() as s:
        s.add(PackBattle(id="r1", mode="royale", machine_code="m", price=25_000_000,
                         max_players=5, status="running"))
        s.add(BattlePlayer(battle_id="r1", player_wallet=WALLET_A))
        s.commit()

    r = c.post("/users/me/withdraw", json=body, headers=hdrs)
    assert r.status_code == 409, r.text
    assert "battle in play" in r.json()["detail"]

    # Y al acabar la partida, el retiro vuelve a estar abierto.
    with sf() as s:
        s.get(PackBattle, "r1").status = "settled"
        s.commit()

    async def _bh(*a, **k):
        return "11111111111111111111111111111111"

    async def _wd(*a, **k):
        return "sig-stub"

    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.withdraw_usdc", _wd)
    assert c.post("/users/me/withdraw", json=body, headers=hdrs).status_code == 200


def test_withdraw_no_lo_bloquea_la_partida_de_otro(monkeypatch):
    """La puerta mira SOLO las partidas del que pide: si no, cualquiera podría congelar el
    retiro de otro con solo abrir un lobby."""
    from app.models import PackBattle, BattlePlayer
    sf, c, priv = _build_client_with_sf(signer=object())

    async def _high_balance(*a, **k):
        return 1_000_000_000

    async def _bh(*a, **k):
        return "11111111111111111111111111111111"

    async def _wd(*a, **k):
        return "sig-stub"

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.withdraw_usdc", _wd)

    with sf() as s:
        s.add(PackBattle(id="r2", mode="royale", machine_code="m", price=25_000_000,
                         max_players=5, status="running"))
        s.add(BattlePlayer(battle_id="r2", player_wallet=WALLET_B))
        s.commit()

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 1.0}, headers=hdrs)
    assert r.status_code == 200, r.text


def test_withdraw_rate_limited(monkeypatch):
    """SECURITY (A2): withdrawals are rate-limited per wallet. With the default limit of 5
    per window, the 6th withdrawal is throttled (429) — the throttle fires before the
    on-chain transfer, capping how fast the operator's gas/rent can be spent."""
    c, priv = _build_client(signer=object())

    async def _high_balance(*a, **k):
        return 1_000_000_000  # plenty so _require_available always passes

    async def _bh(*a, **k):
        return "11111111111111111111111111111111"

    async def _wd(*a, **k):
        return "sig-stub"

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.withdraw_usdc", _wd)

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    body = {"address": WALLET_B, "amount": 1.0}  # at/above the minimum

    for i in range(5):
        r = c.post("/users/me/withdraw", json=body, headers=hdrs)
        assert r.status_code == 200, f"call {i}: {r.text}"

    r = c.post("/users/me/withdraw", json=body, headers=hdrs)
    assert r.status_code == 429, r.text


def test_withdraw_charges_platform_fee(monkeypatch):
    """A withdraw fee (pct of the amount) is DEDUCTED: the destination receives the net and the
    fee wallet receives the fee, in one atomic call to withdraw_usdc_with_fee."""
    fee_wallet = "So1anaFEEWALLET1111111111111111111111111111"
    captured = {}

    async def _high_balance(*a, **k):
        return 1_000_000_000

    async def _bh(*a, **k):
        return "11111111111111111111111111111111"

    async def _wf(rpc, signer, pwid, paddr, owid, oaddr, dest, fee_dest, mint, net, fee, bh):
        captured.update(dest=dest, fee_dest=fee_dest, net=net, fee=fee)
        return "sig-fee"

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.withdraw_usdc_with_fee", _wf)

    c, priv = _build_client(signer=object(), withdraw_fee_pct=0.01, fee_wallet_address=fee_wallet)
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 100.0}, headers=hdrs)

    assert r.status_code == 200, r.text
    # 100 USDC → 1% fee = 1 USDC (1_000_000 base) to the fee wallet, 99 USDC (99_000_000) to dest.
    assert captured == {"dest": WALLET_B, "fee_dest": fee_wallet, "net": 99_000_000, "fee": 1_000_000}
    body = r.json()
    assert body["net"] == 99.0 and body["fee"] == 1.0


def test_withdraw_no_fee_when_pct_zero(monkeypatch):
    """With withdraw_fee_pct=0 the plain single-transfer withdraw is used (no fee split)."""
    used = {"plain": False, "fee": False}

    async def _high_balance(*a, **k):
        return 1_000_000_000

    async def _bh(*a, **k):
        return "11111111111111111111111111111111"

    async def _wd(*a, **k):
        used["plain"] = True
        return "sig-plain"

    async def _wf(*a, **k):
        used["fee"] = True
        return "sig-fee"

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.withdraw_usdc", _wd)
    monkeypatch.setattr("app.main.withdraw_usdc_with_fee", _wf)

    c, priv = _build_client(signer=object(), withdraw_fee_pct=0.0)
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 100.0}, headers=hdrs)
    assert r.status_code == 200, r.text
    assert used == {"plain": True, "fee": False}


# ── CARDS withdraw (airdrop de Collector Crypt, mismo endpoint con token="cards") ──────────────

def test_withdraw_cards_usa_el_mint_de_cards_no_el_de_usdc(monkeypatch):
    """El fallo caro aquí es mezclar mints: si un withdraw con token="cards" saliera firmado con
    el mint de USDC, el jugador recibiría el token equivocado (o nada, si no tiene esa ATA)."""
    captured = {}

    async def _high_balance(*a, **k):
        return 1_000_000_000

    async def _bh(*a, **k):
        return "11111111111111111111111111111111"

    async def _wd(rpc, signer, pwid, paddr, owid, oaddr, dest, mint, amount, bh):
        captured.update(mint=mint, amount=amount, dest=dest)
        return "sig-cards"

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.withdraw_usdc", _wd)

    c, priv = _build_client(signer=object(), cards_airdrop_mint=DUMMY_CARDS_MINT,
                            withdraw_fee_pct=0.05, fee_wallet_address="So1anaFEEWALLET1111111111111111111111111111")
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 10.0, "token": "cards"}, headers=hdrs)

    assert r.status_code == 200, r.text
    assert captured["mint"] == DUMMY_CARDS_MINT
    assert captured["mint"] != DUMMY_MINT
    assert captured["amount"] == 10_000_000
    assert captured["dest"] == WALLET_B


def test_withdraw_cards_no_cobra_comision(monkeypatch):
    """CARDS nunca pasa por withdraw_usdc_with_fee, ni siquiera con withdraw_fee_pct configurado:
    la comisión grava dinero que SALE de la economía de la plataforma, y CARDS nunca entró."""
    used_fee_path = {"called": False}

    async def _high_balance(*a, **k):
        return 1_000_000_000

    async def _bh(*a, **k):
        return "11111111111111111111111111111111"

    async def _wd(*a, **k):
        return "sig-cards"

    async def _wf(*a, **k):
        used_fee_path["called"] = True
        return "sig-fee"

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.withdraw_usdc", _wd)
    monkeypatch.setattr("app.main.withdraw_usdc_with_fee", _wf)

    c, priv = _build_client(signer=object(), cards_airdrop_mint=DUMMY_CARDS_MINT,
                            withdraw_fee_pct=0.1, fee_wallet_address="So1anaFEEWALLET1111111111111111111111111111")
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 10.0, "token": "cards"}, headers=hdrs)

    assert r.status_code == 200, r.text
    assert used_fee_path["called"] is False
    body = r.json()
    assert body["fee"] == 0.0
    assert body["net"] == 10.0


def test_withdraw_cards_no_lo_bloquea_una_partida_en_curso(monkeypatch):
    """CARDS no se apuesta en ninguna batalla, así que una partida abierta no le da destino a
    este saldo — a diferencia de USDC (ver test_withdraw_bloqueado_con_partida_sin_terminar)."""
    from app.models import PackBattle, BattlePlayer
    sf, c, priv = _build_client_with_sf(signer=object(), cards_airdrop_mint=DUMMY_CARDS_MINT)

    async def _high_balance(*a, **k):
        return 1_000_000_000

    async def _bh(*a, **k):
        return "11111111111111111111111111111111"

    async def _wd(*a, **k):
        return "sig-cards"

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.withdraw_usdc", _wd)

    with sf() as s:
        s.add(PackBattle(id="r-cards", mode="royale", machine_code="m", price=25_000_000,
                         max_players=5, status="running"))
        s.add(BattlePlayer(battle_id="r-cards", player_wallet=WALLET_A))
        s.commit()

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 10.0, "token": "cards"}, headers=hdrs)
    assert r.status_code == 200, r.text


def test_withdraw_cards_bajo_minimo_rechazado():
    """CARDS tiene su propio mínimo (min_withdraw_cards), independiente del de USDC."""
    c, priv = _build_client(signer=object(), cards_airdrop_mint=DUMMY_CARDS_MINT, min_withdraw_cards=5.0)
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 1.0, "token": "cards"}, headers=hdrs)
    assert r.status_code == 422, r.text
    assert "minimum withdrawal" in r.json()["detail"]


def test_withdraw_cards_sin_mint_configurado_da_503():
    """Sin cards_airdrop_mint configurado, "indisponible" y no "no": mismo criterio que el claim."""
    c, priv = _build_client(signer=object())  # cards_airdrop_mint="" por defecto
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 10.0, "token": "cards"}, headers=hdrs)
    assert r.status_code == 503, r.text


def test_withdraw_token_invalido_da_422():
    c, priv = _build_client(signer=object(), cards_airdrop_mint=DUMMY_CARDS_MINT)
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 10.0, "token": "sol"}, headers=hdrs)
    assert r.status_code == 422, r.text


# ── Join All Bots (DEV/TEST) ──────────────────────────────────────────────────

_BOTS_3 = [
    {"id": "bot-1", "address": "So1anaBOT11111111111111111111111111111111"},
    {"id": "bot-2", "address": "So1anaBOT22222222222222222222222222222222"},
    {"id": "bot-3", "address": "So1anaBOT33333333333333333333333333333333"},
]


def _mock_battle_env(monkeypatch, *, bots, run_called):
    async def _high_balance(*args, **kwargs):
        return 100_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    async def _fake_run(session, battle, *, gacha, signer, **kwargs):
        run_called.append(battle.id)

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.run_pack_battle_live", _fake_run)
    monkeypatch.setattr("app.main.load_bots", lambda: bots)


def _create_pack(c, priv, max_players):
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": max_players}, headers=hdrs)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _tick():
    async def _drain():
        await asyncio.sleep(0)
    asyncio.get_event_loop().run_until_complete(_drain())


def test_join_all_bots_fills_lobby_and_schedules_run(monkeypatch):
    c, priv = _build_client(dev_endpoints_enabled=True)
    run_called: list = []
    _mock_battle_env(monkeypatch, bots=_BOTS_3, run_called=run_called)

    battle_id = _create_pack(c, priv, max_players=4)  # creator = 1 player, 3 empty seats
    r = c.post(f"/pack-battles/{battle_id}/join-all-bots")

    assert r.status_code == 200, r.text
    assert len(r.json()["players"]) == 4  # creator + 3 bots → full
    _tick()
    assert run_called, "run_pack_battle_live was not scheduled after bots filled the lobby"


def test_join_all_bots_announces_the_start(monkeypatch):
    """Llenar el lobby con bots tiene que anunciar 'battle_start' como lo hace una entrada humana.

    Regresión: /join-bot y /join-all-bots arrancaban la partida sin difundir nada, así que a los
    humanos ya sentados no les llegaba el aviso — ni toast, ni forma de enterarse de que su
    partida había empezado.
    """
    sent: list = []

    async def _spy(self, msg):
        sent.append(msg)

    monkeypatch.setattr("app.chat.ConnectionManager.broadcast", _spy)
    c, priv = _build_client(dev_endpoints_enabled=True)
    _mock_battle_env(monkeypatch, bots=_BOTS_3, run_called=[])

    battle_id = _create_pack(c, priv, max_players=4)
    r = c.post(f"/pack-battles/{battle_id}/join-all-bots")
    assert r.status_code == 200, r.text

    starts = [m for m in sent if m.get("type") == "battle_start" and m.get("battle_id") == battle_id]
    assert len(starts) == 1, f"esperaba un battle_start, llegaron: {[m.get('type') for m in sent]}"
    assert len(starts[0]["players"]) == 4


def test_join_all_bots_409_when_no_eligible_bots(monkeypatch):
    c, priv = _build_client(dev_endpoints_enabled=True)
    _mock_battle_env(monkeypatch, bots=[], run_called=[])  # no bots configured

    battle_id = _create_pack(c, priv, max_players=2)
    r = c.post(f"/pack-battles/{battle_id}/join-all-bots")

    assert r.status_code == 409, r.text


def test_join_all_bots_404_when_dev_disabled(monkeypatch):
    c, priv = _build_client(dev_endpoints_enabled=False)
    _mock_battle_env(monkeypatch, bots=_BOTS_3, run_called=[])

    battle_id = _create_pack(c, priv, max_players=2)
    r = c.post(f"/pack-battles/{battle_id}/join-all-bots")

    assert r.status_code == 404, r.text


def test_join_bot_still_adds_exactly_one(monkeypatch):
    """Regression: the refactored /join-bot adds a single bot without filling a 4-seat lobby."""
    c, priv = _build_client(dev_endpoints_enabled=True)
    _mock_battle_env(monkeypatch, bots=_BOTS_3, run_called=[])

    battle_id = _create_pack(c, priv, max_players=4)
    r = c.post(f"/pack-battles/{battle_id}/join-bot")

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["players"]) == 2  # creator + exactly one bot
    assert body["status"] == "lobby"  # not full → not started


# ── Task 4: startup reconcile sweep + deferred reconcile after a hot void ─────

def test_startup_sweep_reconciles_voided_battles(monkeypatch):
    """On startup, every 'voided' battle is swept through reconcile_voided_battle_live; a
    'settled' battle must NOT be touched."""
    import app.main as m
    from app.models import PackBattle, BattlePull

    swept = []

    async def fake_sweep(session, battle, **kw):
        swept.append(battle.id)

    monkeypatch.setattr(m, "reconcile_voided_battle_live", fake_sweep)

    # privy_signer must be truthy for _resume_orphaned_battles to proceed past its early-return
    # gate (it bails when privy_signer is None) — a bare object() is enough, it's never called
    # because reconcile_voided_battle_live itself is faked.
    sf, c, priv = _build_client_with_sf(signer=object())

    with sf() as s:
        s.add(PackBattle(id="vd1", mode="pack", machine_code="m", price=50, max_players=2,
                         status="voided", escrow_wallet_id="eid", escrow_address="ESC"))
        s.add(BattlePull(battle_id="vd1", player_wallet="A", memo="mA", round_number=1))
        s.add(PackBattle(id="ok1", mode="pack", machine_code="m", price=50, max_players=2,
                         status="settled"))
        s.commit()

    with c:
        # The startup hook schedules the sweep via asyncio.create_task on the TestClient's
        # persistent portal loop; a request inside the `with` block gives that loop a chance
        # to run the (already-scheduled, no-internal-await) task before we assert.
        c.get("/pack-battles/open")

    assert swept == ["vd1"]


def test_startup_resume_lanza_resume_royale_para_huerfanas(monkeypatch):
    """Una royale en 'running' al arrancar dispara resume_royale_live (ya no solo un warning)."""
    import app.main as m
    resumed = []

    async def fake_resume_live(session, battle, **kw):
        resumed.append(battle.id); return "settled"

    monkeypatch.setattr(m, "resume_royale_live", fake_resume_live)
    sf, c, priv = _build_client_with_sf(signer=object())
    from app.models import PackBattle
    with sf() as s:
        s.add(PackBattle(id="ro1", mode="royale", machine_code="m", price=50, max_players=5,
                         status="running", server_seed="ab" * 32,
                         escrow_wallet_id="eid", escrow_address="ESC"))
        s.commit()
    with c:
        c.get("/pack-battles/open")
    assert resumed == ["ro1"]


def test_run_bg_voided_schedules_deferred_reconcile(client_priv, monkeypatch):
    """When the live pack run comes back 'voided', _run_bg schedules a deferred reconcile
    (_reconcile_voided_later) instead of just dropping the result on the floor."""
    c, priv = client_priv

    async def _high_balance(*args, **kwargs):
        return 100_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    async def _fake_run_voided(session, battle, *, gacha, signer, **kwargs):
        return "voided"

    scheduled_names = []
    orig_create_task = asyncio.create_task

    def spy_create_task(coro, *a, **kw):
        scheduled_names.append(getattr(coro, "__qualname__", str(coro)))
        return orig_create_task(coro, *a, **kw)

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.run_pack_battle_live", _fake_run_voided)
    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    hdrs_a = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    bid = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs_a).json()["id"]
    hdrs_b = _auth_headers(priv, WALLET_B, WALLET_ID_B)
    r = c.post(f"/pack-battles/{bid}/join", headers=hdrs_b)
    assert r.status_code == 200, r.text

    asyncio.get_event_loop().run_until_complete(asyncio.sleep(0))

    assert any("_reconcile_voided_later" in name for name in scheduled_names), (
        f"_run_bg did not schedule a deferred reconcile on 'voided'; scheduled: {scheduled_names}"
    )


# ── Royale creator allowlist (launch week) ────────────────────────────────────

def test_royale_creator_allowlist_parses_csv():
    from app.config import Settings
    s = Settings(royale_creator_allowlist="  A1 , B2 ,, C3 ")
    assert s.royale_creator_allowlist_set == {"A1", "B2", "C3"}
    assert Settings(royale_creator_allowlist="").royale_creator_allowlist_set == set()


def test_royale_create_blocked_for_non_allowlisted_wallet(monkeypatch):
    """With an allowlist set, a wallet not on it gets 403 on royale create and NOTHING is charged/created."""
    async def _high_balance(*args, **kwargs):
        return 200_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    async def _fake_collect_buyin(*args, **kwargs):
        return "fake-sig"

    escrow_created = []
    # allowlist holds WALLET_B only; WALLET_A (below) is NOT allowed.
    c, priv = _make_royale_app(escrow_created_list=escrow_created, royale_creator_allowlist={WALLET_B})

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.collect_buyin", _fake_collect_buyin)

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 5, "mode": "royale"}, headers=hdrs)
    assert r.status_code == 403, r.text
    assert escrow_created == [], "no escrow must be created when creation is blocked"


def test_royale_create_allowed_for_allowlisted_wallet(monkeypatch):
    """A wallet ON the allowlist can still create a royale."""
    async def _high_balance(*args, **kwargs):
        return 200_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    async def _fake_collect_buyin(*args, **kwargs):
        return "fake-sig"

    async def _fake_blockhash(rpc_url: str) -> str:
        return "FakeBH444444444444444444444444444444444444444"

    c, priv = _make_royale_app(royale_creator_allowlist={WALLET_A})

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    monkeypatch.setattr("app.main.collect_buyin", _fake_collect_buyin)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _fake_blockhash)

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 5, "mode": "royale"}, headers=hdrs)
    assert r.status_code == 200, r.text


def test_pack_create_unaffected_by_royale_allowlist(monkeypatch):
    """The royale allowlist does NOT gate Pack Battle creation."""
    async def _high_balance(*args, **kwargs):
        return 100_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    # allowlist holds WALLET_B; WALLET_A creates a PACK battle → must still succeed.
    c, priv = _build_client(royale_creator_allowlist={WALLET_B})
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs)
    assert r.status_code == 200, r.text


def test_me_usdc_reads_onchain_balance(client_priv, monkeypatch):
    """GET /users/me/usdc returns the caller's on-chain USDC (base units + UI amount), read
    server-side with the per-network rpc_url + mint. The browser can't query mainnet RPC
    directly (403 to browser Origins), so the backend proxies the read."""
    c, priv = client_priv
    seen = {}

    async def _balance(rpc_url, owner, mint, *args, **kwargs):
        seen["rpc_url"], seen["owner"], seen["mint"] = rpc_url, owner, mint
        return 2_500_000  # 2.5 USDC in base units

    monkeypatch.setattr("app.main.usdc_balance_base_units", _balance)

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.get("/users/me/usdc", headers=hdrs)
    assert r.status_code == 200, r.text
    assert r.json() == {"base_units": 2_500_000, "usdc": 2.5}
    # Reads with the injected per-network config, for the authenticated caller's wallet.
    assert seen == {"rpc_url": DUMMY_RPC, "owner": WALLET_A, "mint": DUMMY_MINT}


def test_me_usdc_requires_auth(client_priv):
    """No Bearer token → 401 (never leaks a balance for an unauthenticated caller)."""
    c, _ = client_priv
    assert c.get("/users/me/usdc").status_code == 401


def test_me_cards_reads_onchain_balance(monkeypatch):
    """GET /users/me/cards mirrors /users/me/usdc but reads the CARDS airdrop mint."""
    c, priv = _build_client(cards_airdrop_mint=DUMMY_CARDS_MINT)
    seen = {}

    async def _balance(rpc_url, owner, mint, *args, **kwargs):
        seen["rpc_url"], seen["owner"], seen["mint"] = rpc_url, owner, mint
        return 1_200_000  # 1.2 CARDS in base units

    monkeypatch.setattr("app.main.usdc_balance_base_units", _balance)

    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.get("/users/me/cards", headers=hdrs)
    assert r.status_code == 200, r.text
    assert r.json() == {"base_units": 1_200_000, "cards": 1.2}
    assert seen == {"rpc_url": DUMMY_RPC, "owner": WALLET_A, "mint": DUMMY_CARDS_MINT}


def test_me_cards_sin_mint_configurado_da_503():
    """Igual que el withdraw de CARDS: sin mint no hay lectura posible, y eso es 503."""
    c, priv = _build_client()  # cards_airdrop_mint="" por defecto
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    assert c.get("/users/me/cards", headers=hdrs).status_code == 503


def test_me_cards_requires_auth():
    """No Bearer token → 401, igual que /users/me/usdc."""
    c, _ = _build_client(cards_airdrop_mint=DUMMY_CARDS_MINT)
    assert c.get("/users/me/cards").status_code == 401


# ── máquinas apagadas a mano: no se pueden estrenar partidas con ellas ─────────
# Apagar una máquina (scripts/machines.py) la quita del catálogo. Estos tests fijan que la puerta
# está en AMBOS modos, no solo en la pantalla: alguien que llame a la API a pelo con el código
# apagado tiene que rebotar igual.

def _con_maquina_apagada(monkeypatch, code="pokemon_50"):
    async def _high_balance(*args, **kwargs):
        return 10_000_000_000    # holgado: un royale de 5 con máquina de $99 pide bastante

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True},
                {"code": "sweet_99", "price": 99, "available": True}]

    async def _bh(*a, **k):
        return "11111111111111111111111111111111"

    async def _collect(*a, **k):
        return "collect-sig"

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())
    # El royale cobra el buy-in on-chain al crear el lobby; sin esto el caso base daría 502 y el
    # test no distinguiría "rechazada por apagada" de "no hay red".
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.collect_buyin", _collect)


@pytest.mark.parametrize("mode", ["pack", "royale"])
def test_no_se_puede_crear_partida_con_una_maquina_apagada(monkeypatch, mode):
    from app.services.machine_visibility import hide
    sf, c, priv = _build_client_with_sf(signer=_FakeSigner())
    _con_maquina_apagada(monkeypatch)
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    cuerpo = {"machine_code": "sweet_99", "max_players": 2 if mode == "pack" else 5, "mode": mode}

    assert c.post("/pack-battles", json=cuerpo, headers=hdrs).status_code == 200

    with sf() as s:
        hide(s, "sweet_99", reason="apagada a mano")

    r = c.post("/pack-battles", json=cuerpo, headers=hdrs)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "machine unavailable"


def test_una_maquina_encendida_sigue_pudiendose_usar(monkeypatch):
    """La puerta filtra por código, no apaga la creación entera."""
    from app.services.machine_visibility import hide
    sf, c, priv = _build_client_with_sf(signer=_FakeSigner())
    _con_maquina_apagada(monkeypatch)
    with sf() as s:
        hide(s, "sweet_99")
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2}, headers=hdrs)
    assert r.status_code == 200, r.text


# ── Puerta de delegación ───────────────────────────────────────────────────────────────────────
#
# Para tirar una caja el servidor firma en nombre del jugador. Si no le ha añadido como firmante,
# esa firma falla al ARRANCAR la partida y el motor la anula para TODA la sala, con el escrow ya
# creado — en mainnet tumbó una Pack Battle de 250 $. Estas pruebas fijan que el rechazo ocurre al
# entrar, donde el daño se queda en quien lo causa.
#
# La llamada a Privy va doblada: `podemos_firmar` responde sin salir a la red.

class _SignerSinDelegar:
    async def podemos_firmar(self, wallet_id):
        return False

    async def create_solana_wallet(self):
        raise AssertionError("no debería llegar a crear escrow: la puerta va antes")


class _SignerPrivyCaido:
    async def podemos_firmar(self, wallet_id):
        from app.services.privy_signer import PrivyNoVerificable
        raise PrivyNoVerificable("privy no respondió")

    async def create_solana_wallet(self):
        raise AssertionError("no debería llegar a crear escrow: la puerta va antes")


def _con_maquinas(monkeypatch):
    async def _high_balance(*args, **kwargs):
        return 200_000_000

    async def _machines():
        return [{"code": "pokemon_50", "price": 50, "available": True}]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.services.gacha.GachaService.machines", lambda self: _machines())


@pytest.mark.parametrize("mode", ["pack", "royale"])
def test_sin_delegacion_no_se_puede_crear_partida(monkeypatch, mode):
    _con_maquinas(monkeypatch)
    c, priv = _build_client(signer=_SignerSinDelegar())
    r = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2, "mode": mode},
               headers=_auth_headers(priv, WALLET_A, WALLET_ID_A))
    assert r.status_code == 409, r.text
    assert "enable signing" in r.json()["detail"]


@pytest.mark.parametrize("mode", ["pack", "royale"])
def test_sin_delegacion_no_se_puede_unir_ni_se_le_cobra(monkeypatch, mode):
    """Lo importante del royale: se rechaza ANTES de cobrar el buy-in."""
    _con_maquinas(monkeypatch)
    cobros: list = []

    async def _cobra(*args, **kwargs):
        cobros.append(args)
        return "sig"

    async def _bh(rpc_url):
        return "FakeBH111111111111111111111111111111111111111"

    monkeypatch.setattr("app.main.collect_buyin", _cobra)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)

    # A (delegado) crea la partida; B (sin delegar) intenta entrar.
    delegado = [True]

    class _Signer:
        async def podemos_firmar(self, wallet_id):
            return delegado[0]

        async def create_solana_wallet(self):
            return {"id": "esc-id", "address": "So1anaESCROW111111111111111111111111111111"}

    c, priv = _build_client(signer=_Signer())
    jugadores = 5 if mode == "royale" else 2      # el royale exige de 5 a 10
    r = c.post("/pack-battles",
               json={"machine_code": "pokemon_50", "max_players": jugadores, "mode": mode},
               headers=_auth_headers(priv, WALLET_A, WALLET_ID_A))
    assert r.status_code == 200, r.text
    battle_id = r.json()["id"]
    cobros_antes = len(cobros)

    delegado[0] = False
    r2 = c.post(f"/pack-battles/{battle_id}/join",
                headers=_auth_headers(priv, WALLET_B, WALLET_ID_B))
    assert r2.status_code == 409, r2.text
    assert "enable signing" in r2.json()["detail"]
    assert len(cobros) == cobros_antes, "se le cobró el buy-in a alguien que no puede jugar"

    # Y no entró: la partida sigue esperando.
    estado = c.get(f"/pack-battles/{battle_id}").json()
    assert len(estado["players"]) == 1


@pytest.mark.parametrize("mode", ["pack", "royale"])
def test_con_privy_caido_se_rechaza_como_temporal(monkeypatch, mode):
    """503, no 409: el jugador no ha hecho nada mal y tiene que poder reintentar.

    Es la decisión incómoda —una caída de Privy impide entrar— y se elige porque dejar pasar
    reintroduce el fallo que esto cierra, y ese lo pagan los demás de la sala.
    """
    _con_maquinas(monkeypatch)
    c, priv = _build_client(signer=_SignerPrivyCaido())
    r = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2, "mode": mode},
               headers=_auth_headers(priv, WALLET_A, WALLET_ID_A))
    assert r.status_code == 503, r.text
    assert "try again" in r.json()["detail"]


def test_sin_privy_configurado_no_se_comprueba_nada(monkeypatch):
    """En desarrollo sin Privy no hay firma que verificar: la puerta no puede cerrar el paso."""
    _con_maquinas(monkeypatch)
    c, priv = _build_client(signer=None)
    r = c.post("/pack-battles", json={"machine_code": "pokemon_50", "max_players": 2},
               headers=_auth_headers(priv, WALLET_A, WALLET_ID_A))
    assert r.status_code == 200, r.text


def test_el_502_del_retiro_de_cards_no_filtra_la_url_del_rpc(monkeypatch):
    """El str() de un error de httpx incluye la URL del RPC, y en mainnet esa URL lleva la
    api-key en la query. Un 429 del proveedor no puede acabar entregándole la clave al
    navegador del jugador."""
    SECRETO = "https://mainnet.helius-rpc.com/?api-key=NO-DEBE-SALIR"

    async def _high_balance(*a, **k):
        return 1_000_000_000

    async def _bh(*a, **k):
        return "11111111111111111111111111111111"

    async def _revienta(*a, **k):
        raise RuntimeError(f"Client error '429 Too Many Requests' for url '{SECRETO}'")

    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_balance)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)
    monkeypatch.setattr("app.main.withdraw_usdc", _revienta)

    c, priv = _build_client(signer=object(), cards_airdrop_mint=DUMMY_CARDS_MINT)
    hdrs = _auth_headers(priv, WALLET_A, WALLET_ID_A)
    r = c.post("/users/me/withdraw", json={"address": WALLET_B, "amount": 10.0, "token": "cards"},
               headers=hdrs)

    assert r.status_code == 502
    assert "NO-DEBE-SALIR" not in r.text
    assert "api-key" not in r.text
