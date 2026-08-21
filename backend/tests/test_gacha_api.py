import json
from datetime import datetime, timedelta, timezone

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.main import create_app
from app.db import make_session_factory, init_db
from app.privy import PrivyVerifier
from app.chain.mock import MockChainSource
from app.services.gacha import GachaService
from app.models import GachaPack, PackBattle, BattlePull

from tests.conftest import make_es256, privy_auth_headers

BASE = "https://dev-gacha.collectorcrypt.com"
APP_ID = "app123"

# Direcciones Solana embebidas de prueba (44 caracteres)
WALLET_A = "So1anaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"
WALLET_B = "So1anaBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB1"


def _client(api_key="k123", rate_limit=10, base_url=BASE, tracker_access_allowlist=None):
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)
    priv = make_es256()
    privy = PrivyVerifier(app_id=APP_ID, key_resolver=lambda kid: priv.public_key())
    gacha = GachaService(base_url=base_url, api_key=api_key)
    app = create_app(sf, MockChainSource(), elo_start=1200, elo_k=32,
                     gacha=gacha, gacha_rate_limit=rate_limit, privy=privy,
                     tracker_access_allowlist=tracker_access_allowlist)
    client = TestClient(app)
    client.session_factory = sf     # los tests que apagan máquinas necesitan la base
    return client, priv


def _hdrs(priv, wallet):
    """Devuelve headers de Authorization para `wallet`."""
    return privy_auth_headers(priv, APP_ID, wallet)


def _owns(monkeypatch, holder=None):
    """Stub de la comprobación on-chain de propiedad del buyback.

    `holder=None` → la carta es de quien la pide. Si se pasa una wallet, esa es la dueña real,
    que es como se monta el caso de "vender la carta de otro" sin tocar la cadena.
    """
    async def fake(rpc, owner, mint):
        return owner == holder if holder is not None else True
    monkeypatch.setattr("app.main.nft_in_owner", fake)


@respx.mock
def test_machines_keyless_ok():
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_50", "name": "P50", "price": 50, "odds": {}, "stock": {},
         "ev": 1.0, "image": None, "turboMode": True}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    c, _ = _client(api_key="")
    r = c.get("/gacha/machines")
    assert r.status_code == 200
    assert r.json()[0]["code"] == "pokemon_50"
    assert r.json()[0]["turboMode"] is True


def test_503_when_base_url_empty():
    c, _ = _client(api_key="", base_url="")
    r = c.get("/gacha/machines")
    assert r.status_code == 503
    assert r.json()["detail"] == "gacha_disabled"


@respx.mock
def test_machines_ok():
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_50", "name": "P50", "price": 50, "odds": {}, "stock": {},
         "ev": 1.0, "image": None}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    c, _ = _client()
    r = c.get("/gacha/machines")
    assert r.status_code == 200
    assert r.json()[0]["code"] == "pokemon_50"


def test_generate_pack_requiere_auth():
    c, _ = _client()
    assert c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}).status_code == 401


@respx.mock
def test_generate_pack_fija_player_y_guarda_memo(monkeypatch):
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_50", "price": 50, "available": True}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    route = respx.post(f"{BASE}/api/generatePack").mock(
        return_value=Response(200, json={"memo": "slug-m1", "transaction": "dA=="}))
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client()
    hdrs = _hdrs(priv, WALLET_A)
    r = c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs)
    assert r.status_code == 200
    assert r.json() == {"memo": "slug-m1", "transaction": "dA=="}
    assert json.loads(route.calls[0].request.content)["playerAddress"] == WALLET_A


@respx.mock
def test_open_pack_memo_ajeno_403(monkeypatch):
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_50", "price": 50, "available": True}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    respx.post(f"{BASE}/api/generatePack").mock(
        return_value=Response(200, json={"memo": "slug-m2", "transaction": "dA=="}))
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client()
    hdrs_a = _hdrs(priv, WALLET_A)
    c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs_a)
    hdrs_b = _hdrs(priv, WALLET_B)  # otra wallet, misma clave (app verifier la acepta)
    r = c.post("/gacha/open-pack", json={"memo": "slug-m2"}, headers=hdrs_b)
    assert r.status_code == 403


@respx.mock
def test_open_pack_ok_marca_abierto(monkeypatch):
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_50", "price": 50, "available": True}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    respx.post(f"{BASE}/api/generatePack").mock(
        return_value=Response(200, json={"memo": "slug-m3", "transaction": "dA=="}))
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json={
        "success": True, "nft_address": "Mint" + "1" * 40, "rarity": "Rare",
        "nftWon": {"content": {"metadata": {"name": "Pika"}}, "image": "https://x/p.png"}}))
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client()
    hdrs = _hdrs(priv, WALLET_A)
    c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs)
    r = c.post("/gacha/open-pack", json={"memo": "slug-m3"}, headers=hdrs)
    assert r.status_code == 200
    assert r.json() == {"pending": False, "nft_address": "Mint" + "1" * 40,
                        "rarity": "Rare", "name": "Pika", "image": "https://x/p.png",
                        "images": ["https://x/p.png"],
                        "grade": None, "year": None,
                        "grading_company": None, "grading_id": None,
                        "authenticated": None, "insured_value": None,
                        "auto_sold": False, "buyback_amount": None}


@respx.mock
def test_open_pack_pendiente(monkeypatch):
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_50", "price": 50, "available": True}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    respx.post(f"{BASE}/api/generatePack").mock(
        return_value=Response(200, json={"memo": "slug-m4", "transaction": "dA=="}))
    respx.post(f"{BASE}/api/openPack").mock(
        return_value=Response(200, json={"code": "WAITING_FOR_WEBHOOK"}))
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client()
    hdrs = _hdrs(priv, WALLET_A)
    c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs)
    r = c.post("/gacha/open-pack", json={"memo": "slug-m4"}, headers=hdrs)
    assert r.status_code == 200
    assert r.json() == {"pending": True}


@respx.mock
def test_submit_tx_valida_base64_y_tamano():
    c, priv = _client()
    hdrs = _hdrs(priv, WALLET_A)
    assert c.post("/gacha/submit-tx", json={"signed_transaction": "no base64 !!"},
                  headers=hdrs).status_code == 422
    assert c.post("/gacha/submit-tx", json={"signed_transaction": "A" * 4000},
                  headers=hdrs).status_code == 422


@respx.mock
def test_upstream_caido_502():
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(500, text="interno secreto"))
    c, _ = _client()
    r = c.get("/gacha/machines")
    assert r.status_code == 502
    assert "secreto" not in r.text


@respx.mock
def test_rate_limit_429(monkeypatch):
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_50", "price": 50, "available": True}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    respx.post(f"{BASE}/api/generatePack").mock(
        return_value=Response(200, json={"memo": None, "transaction": None}))
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client(rate_limit=2)
    hdrs = _hdrs(priv, WALLET_A)
    codes = [c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs).status_code
             for _ in range(3)]
    # las 2 primeras llegan al upstream (memo nulo → 502); la 3ª ni sale → 429
    assert codes[2] == 429


@respx.mock
def test_open_pack_polling_not_rate_limited(monkeypatch):
    """The client POLLS open-pack (up to ~8×/pack) while CC settles the pack, so a single pull
    hits it several times by design. It must NOT count against the per-wallet pull rate limit,
    or the *next* pull 429s — the reported "I click open and nothing happens" bug."""
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_50", "price": 50, "available": True}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    respx.post(f"{BASE}/api/generatePack").mock(
        return_value=Response(200, json={"memo": "slug-poll", "transaction": "dA=="}))
    respx.post(f"{BASE}/api/openPack").mock(
        return_value=Response(200, json={"code": "WAITING_FOR_WEBHOOK"}))  # pending → gets polled
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    # rate_limit=2 is a tiny budget: pre-fix the 2nd open-pack poll already tripped 429.
    c, priv = _client(rate_limit=2)
    hdrs = _hdrs(priv, WALLET_A)
    assert c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs).status_code == 200
    codes = [c.post("/gacha/open-pack", json={"memo": "slug-poll"}, headers=hdrs).status_code
             for _ in range(6)]
    assert codes == [200] * 6, codes  # every poll succeeds; none throttled


@respx.mock
def test_submit_tx_not_rate_limited():
    """A YOLO of N packs calls submit-tx once per pack. It must not count against the pull limit."""
    respx.post(f"{BASE}/api/submitTransaction").mock(
        return_value=Response(200, json={"signature": "sig", "confirmationStatus": "confirmed"}))
    c, priv = _client(rate_limit=2)
    hdrs = _hdrs(priv, WALLET_A)
    codes = [c.post("/gacha/submit-tx", json={"signed_transaction": "dGVzdA=="}, headers=hdrs).status_code
             for _ in range(6)]
    assert codes == [200] * 6, codes  # pre-fix the 3rd submit-tx 429'd


@respx.mock
def test_machine_cards_ok():
    respx.get(f"{BASE}/api/getNfts").mock(return_value=Response(200, json={"nfts": [
        {"nft_address": "A", "name": "Card A", "image": "i", "rarity": "rare",
         "insured_value": 400, "attributes": [{"trait_type": "Grading Company", "value": "PSA"},
                                               {"trait_type": "The Grade", "value": "MINT 9"}]}]}))
    c, _ = _client(api_key="")
    r = c.get("/gacha/machines/pokemon_50/cards?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["name"] == "Card A"
    assert body[0]["grade"] == "PSA MINT 9"


def test_machine_cards_503_when_base_url_empty():
    c, _ = _client(api_key="", base_url="")
    r = c.get("/gacha/machines/pokemon_50/cards")
    assert r.status_code == 503


@respx.mock
def test_generate_pack_502_detail_carries_reason(monkeypatch):
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_25", "price": 25, "available": True}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    respx.post(f"{BASE}/api/generatePack").mock(return_value=Response(500, json={"details": "Machine is off"}))
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client(api_key="")
    r = c.post("/gacha/generate-pack", json={"pack_type": "pokemon_25"}, headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 502
    assert "Machine is off" in r.json()["detail"]


@respx.mock
def test_buyback_available_ok():
    respx.get(f"{BASE}/api/buyback/available").mock(
        return_value=Response(200, json={"available": True, "amount": 42500000}))
    c, _ = _client()
    r = c.get("/gacha/buyback/available", params={"wallet": WALLET_A, "nft": "NFT1"})
    assert r.status_code == 200
    assert r.json() == {"available": True, "amount": 42500000}


@respx.mock
def test_buyback_available_false():
    respx.get(f"{BASE}/api/buyback/available").mock(
        return_value=Response(200, json={"available": False}))
    c, _ = _client()
    r = c.get("/gacha/buyback/available", params={"wallet": WALLET_A, "nft": "NFT1"})
    assert r.status_code == 200
    assert r.json() == {"available": False, "amount": None}


def test_buyback_available_requiere_params():
    c, _ = _client()
    assert c.get("/gacha/buyback/available", params={"wallet": WALLET_A}).status_code == 422


def test_buyback_requiere_auth():
    c, _ = _client()
    assert c.post("/gacha/buyback", json={"nft_address": "NFT1"}).status_code == 401


@respx.mock
def test_buyback_fija_player_y_whitelista(monkeypatch):
    _owns(monkeypatch)
    route = respx.post(f"{BASE}/api/buyback").mock(return_value=Response(200, json={
        "success": True,
        "serializedTransaction": "BASE64TX",
        "refundAmount": 42500000,
        "memo": "memo-xyz",
        "secret": "should-not-leak",
    }))
    c, priv = _client()
    r = c.post("/gacha/buyback", json={"nft_address": "NFT1"}, headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 200
    assert r.json() == {"serialized_transaction": "BASE64TX", "refund_amount": 42500000, "memo": "memo-xyz"}
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"playerAddress": WALLET_A, "nftAddress": "NFT1"}


@respx.mock
def test_buyback_upstream_error_502(monkeypatch):
    _owns(monkeypatch)
    respx.post(f"{BASE}/api/buyback").mock(
        return_value=Response(400, json={"error": "outside 72-hour window"}))
    c, priv = _client()
    r = c.post("/gacha/buyback", json={"nft_address": "NFT1"}, headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 502
    assert "72-hour" in r.json()["detail"]


@respx.mock
def test_buyback_rechaza_nft_ajeno(monkeypatch):
    """Vender la carta de otro se corta AQUÍ, sin llegar a CC.

    `nft_address` lo elige el cliente y la pantalla de winnings de una partida vieja sigue siendo
    accesible con sus mints, así que pedir el buyback de una carta que ya no es tuya (o que nunca
    lo fue) es un POST trivial. Antes la única barrera era que CC lo validara por su cuenta.
    """
    route = respx.post(f"{BASE}/api/buyback").mock(return_value=Response(200, json={
        "success": True, "serializedTransaction": "BASE64TX", "refundAmount": 42500000}))
    _owns(monkeypatch, holder=WALLET_B)     # la carta es de B; la pide A
    c, priv = _client()
    r = c.post("/gacha/buyback", json={"nft_address": "NFT1"}, headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 403
    assert not route.called     # ni siquiera se le pide a CC que construya la tx


@respx.mock
def test_buyback_no_vende_si_falla_la_comprobacion(monkeypatch):
    """RPC caído → 502, no venta. Un `except` que se tragara el fallo abriría el agujero entero."""
    route = respx.post(f"{BASE}/api/buyback").mock(return_value=Response(200, json={
        "success": True, "serializedTransaction": "BASE64TX", "refundAmount": 42500000}))
    async def boom(rpc, owner, mint):
        raise RuntimeError("rpc down")
    monkeypatch.setattr("app.main.nft_in_owner", boom)
    c, priv = _client()
    r = c.post("/gacha/buyback", json={"nft_address": "NFT1"}, headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 502
    assert not route.called


# ── Vender la carta recién sacada, sin esperar a que la cadena se entere ──────────────────────
# El sondeo on-chain llega tarde: la carta ya está en la wallet y el RPC/DAS todavía no la ven.
# Medido en mainnet: hasta 5 s de "no eres dueño de este NFT" justo cuando el jugador tiene el
# botón de vender delante. Estos tests fijan las dos mitades del arreglo — el atajo por nuestro
# propio libro y el reintento — sin aflojar la barrera contra vender la carta de otro.

def _guarda_sobre(c, wallet, mint, *, abierto_hace=timedelta(0), auto_sold=False):
    """Deja en la base un sobre YA abierto: nuestro libro dice que esa carta se la dimos a esa wallet."""
    with c.session_factory() as s:
        s.add(GachaPack(memo=f"memo-{mint}-{wallet}", wallet=wallet, pack_type="pokemon_50",
                        submitted_at=datetime.now(timezone.utc),
                        opened_at=datetime.now(timezone.utc) - abierto_hace,
                        nft_address=mint, auto_sold=auto_sold))
        s.commit()


def _ok_buyback():
    return respx.post(f"{BASE}/api/buyback").mock(return_value=Response(200, json={
        "success": True, "serializedTransaction": "BASE64TX", "refundAmount": 42500000}))


@respx.mock
def test_buyback_de_carta_recien_sacada_no_espera_a_la_cadena(monkeypatch):
    """Sobre abierto hace un momento → se vende YA, sin sondear el RPC.

    Es el caso que rompía: dos tiradas, buyback inmediato y 403 durante segundos. La carta la
    acabamos de entregar nosotros, así que de quién es lo sabemos sin preguntarle a ningún índice.
    """
    route = _ok_buyback()
    async def nunca(rpc, owner, mint):
        raise AssertionError("no se pregunta a la cadena por una carta que acabamos de entregar")
    monkeypatch.setattr("app.main.nft_in_owner", nunca)
    c, priv = _client()
    _guarda_sobre(c, WALLET_A, "NFT1")
    r = c.post("/gacha/buyback", json={"nft_address": "NFT1"}, headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 200, r.text
    assert route.called


@respx.mock
def test_vender_la_carta_la_saca_del_atajo(monkeypatch):
    """Construida la venta, la segunda petición de esa carta vuelve a preguntar a la cadena.

    CC recompra la carta y la devuelve a la máquina, así que dentro de la ventana del libro le
    puede tocar a OTRO jugador. Si el atajo siguiera vivo para el que la vendió, pedir el buyback
    otra vez construiría la venta de una carta que ya es de un tercero — apoyándonos solo en que CC
    lo rechace, que es la dependencia que esta comprobación venía a quitar.
    """
    route = _ok_buyback()
    c, priv = _client()
    _guarda_sobre(c, WALLET_A, "NFT1")
    hdrs = _hdrs(priv, WALLET_A)

    _owns(monkeypatch, holder=WALLET_B)     # la cadena diría que no es de A; el atajo la vende igual
    assert c.post("/gacha/buyback", json={"nft_address": "NFT1"}, headers=hdrs).status_code == 200
    assert route.call_count == 1

    # Vendida: ahora la carta ya es de otro y el libro ya no la ampara.
    r = c.post("/gacha/buyback", json={"nft_address": "NFT1"}, headers=hdrs)
    assert r.status_code == 403
    assert route.call_count == 1            # no se le vuelve a pedir a CC que construya nada


@respx.mock
def test_vender_no_le_quita_el_atajo_a_quien_la_saque_despues(monkeypatch):
    """La marca es por (wallet, carta): que A la vendiera no puede penalizar a B, que la acaba de
    sacar de la máquina y a quien se la hemos entregado nosotros hace un segundo."""
    route = _ok_buyback()
    async def nunca(rpc, owner, mint):
        raise AssertionError("B la acaba de recibir: su atajo sigue siendo válido")
    c, priv = _client()
    _guarda_sobre(c, WALLET_A, "NFT1")
    monkeypatch.setattr("app.main.nft_in_owner", nunca)
    assert c.post("/gacha/buyback", json={"nft_address": "NFT1"},
                  headers=_hdrs(priv, WALLET_A)).status_code == 200

    _guarda_sobre(c, WALLET_B, "NFT1")      # CC la recompró y ahora le ha tocado a B
    r = c.post("/gacha/buyback", json={"nft_address": "NFT1"}, headers=_hdrs(priv, WALLET_B))
    assert r.status_code == 200, r.text
    assert route.call_count == 2


@respx.mock
def test_el_atajo_del_libro_es_por_wallet(monkeypatch):
    """El sobre es de B: que exista no le sirve a A para vender esa carta."""
    route = _ok_buyback()
    _owns(monkeypatch, holder=WALLET_B)
    c, priv = _client()
    _guarda_sobre(c, WALLET_B, "NFT1")
    r = c.post("/gacha/buyback", json={"nft_address": "NFT1"}, headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 403
    assert not route.called


@respx.mock
def test_un_sobre_viejo_vuelve_a_preguntar_a_la_cadena(monkeypatch):
    """Pasada la ventana de indexado el atajo caduca: el libro dice "se la dimos", no "sigue siendo
    suya", así que una carta que ya cambió de manos no se vende por haber salido de un sobre suyo."""
    route = _ok_buyback()
    _owns(monkeypatch, holder=WALLET_B)     # a día de hoy la carta es de B
    c, priv = _client()
    _guarda_sobre(c, WALLET_A, "NFT1", abierto_hace=timedelta(hours=3))
    r = c.post("/gacha/buyback", json={"nft_address": "NFT1"}, headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 403
    assert not route.called


@respx.mock
def test_el_buyback_reintenta_cuando_la_cadena_aun_no_la_ve(monkeypatch):
    """Carta sin sobre nuestro (inventario, o llegada de fuera): un "todavía no la veo" no es un
    "no es tuya". Se reintenta unos segundos antes de negar la venta."""
    route = _ok_buyback()
    intentos = []
    async def tarde(rpc, owner, mint):
        intentos.append(owner)
        return len(intentos) >= 2          # el índice se pone al día al segundo sondeo
    monkeypatch.setattr("app.main.nft_in_owner", tarde)
    c, priv = _client()
    r = c.post("/gacha/buyback", json={"nft_address": "NFT1"}, headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 200, r.text
    assert len(intentos) == 2
    assert route.called


@respx.mock
def test_el_botin_de_una_partida_ganada_se_vende_al_momento(monkeypatch):
    """Mismo atajo para la pantalla de winnings: la carta salió del escrow hacia el ganador porque
    la mandamos nosotros, y `transferred` lo dice."""
    route = _ok_buyback()
    async def nunca(rpc, owner, mint):
        raise AssertionError("el botín entregado por nosotros no necesita sondeo")
    monkeypatch.setattr("app.main.nft_in_owner", nunca)
    c, priv = _client()
    with c.session_factory() as s:
        s.add(PackBattle(id="b1", mode="pack", machine_code="pokemon_50", price=50, max_players=2,
                         status="settled", winner=WALLET_A, settled_at=datetime.now(timezone.utc)))
        s.add(BattlePull(battle_id="b1", player_wallet=WALLET_B, memo="m1",
                         nft_address="NFT9", transferred=True))
        s.commit()
    r = c.post("/gacha/buyback", json={"nft_address": "NFT9"}, headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 200, r.text
    assert route.called


@respx.mock
def test_machine_cards_enriched():
    respx.get(f"{BASE}/api/getNfts").mock(return_value=Response(200, json={"nfts": [{
        "nft_address": "MINT1", "name": "1999 Charizard", "image": "img-front",
        "rarity": "epic", "insured_value": 5000,
        "content": {"files": [
            {"cc_cdn": "img-front"}, {"cdn_uri": "img-back"},
        ]},
        "attributes": [
            {"trait_type": "Year", "value": "1999"},
            {"trait_type": "Grading Company", "value": "PSA"},
            {"trait_type": "Grading ID", "value": "44272228"},
            {"trait_type": "The Grade", "value": "MINT 9"},
            {"trait_type": "GradeNum", "value": 9},
            {"trait_type": "Authenticated", "value": "true"},
        ],
    }]}))
    c, _ = _client(api_key="")
    r = c.get("/gacha/machines/pokemon_50/cards?limit=10")
    assert r.status_code == 200
    card = r.json()[0]
    assert card["images"] == ["img-front", "img-back"]
    assert card["grading_company"] == "PSA"
    assert card["grading_id"] == "44272228"
    assert card["the_grade"] == "MINT 9"
    assert card["generic_grade"] == "9"
    assert card["authenticated"] is True
    assert card["year"] == "1999"
    assert card["grade"] == "PSA MINT 9"  # existing composed field unchanged


@respx.mock
def test_yolo_generates_and_stores_memos():
    route = respx.post(f"{BASE}/api/generateYoloPacks").mock(return_value=Response(200, json={
        "yoloId": "y-1", "count": 2, "extra": "drop-me",
        "transactions": [
            {"memo": "ym-1", "transaction": "TX1", "junk": 1},
            {"memo": "ym-2", "transaction": "TX2"},
        ],
    }))
    c, priv = _client(api_key="")
    r = c.post("/gacha/yolo", json={"pack_type": "pokemon_50", "count": 2, "turbo": True},
               headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 200
    assert r.json() == {"yolo_id": "y-1", "count": 2,
                        "transactions": [{"memo": "ym-1", "transaction": "TX1"},
                                         {"memo": "ym-2", "transaction": "TX2"}]}
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"playerAddress": WALLET_A, "packType": "pokemon_50", "count": 2, "turbo": True}


def test_yolo_count_bounds():
    c, priv = _client()
    assert c.post("/gacha/yolo", json={"pack_type": "pokemon_50", "count": 0},
                  headers=_hdrs(priv, WALLET_A)).status_code == 422
    assert c.post("/gacha/yolo", json={"pack_type": "pokemon_50", "count": 11},
                  headers=_hdrs(priv, WALLET_A)).status_code == 422


def test_yolo_requires_auth():
    c, _ = _client()
    assert c.post("/gacha/yolo", json={"pack_type": "pokemon_50", "count": 2}).status_code == 401


@respx.mock
def test_yolo_open_pack_owns_memo():
    respx.post(f"{BASE}/api/generateYoloPacks").mock(return_value=Response(200, json={
        "yoloId": "y-2", "count": 1, "transactions": [{"memo": "ym-own", "transaction": "TX"}]}))
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json={
        "nft_address": "MINT", "rarity": "Common", "code": "TURBO_MODE_BUYBACK",
        "buybackAmount": 42500000, "nftWon": {"content": {"metadata": {"name": "C"}}}}))
    c, priv = _client(api_key="")
    c.post("/gacha/yolo", json={"pack_type": "pokemon_50", "count": 1, "turbo": True},
           headers=_hdrs(priv, WALLET_A))
    r = c.post("/gacha/open-pack", json={"memo": "ym-own"}, headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 200
    body = r.json()
    assert body["auto_sold"] is True
    assert body["buyback_amount"] == 42500000


@respx.mock
def test_open_pack_not_auto_sold_by_default(monkeypatch):
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_50", "price": 50, "available": True}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    respx.post(f"{BASE}/api/generatePack").mock(return_value=Response(200, json={"memo": "m-x", "transaction": "T"}))
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json={
        "nft_address": "MINT", "rarity": "Rare", "nftWon": {"content": {"metadata": {"name": "R"}}}}))
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client(api_key="")
    c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=_hdrs(priv, WALLET_A))
    r = c.post("/gacha/open-pack", json={"memo": "m-x"}, headers=_hdrs(priv, WALLET_A))
    assert r.json()["auto_sold"] is False
    assert r.json()["buyback_amount"] is None


@respx.mock
@pytest.mark.asyncio
async def test_generate_pack_forwards_alt_player_address():
    from app.services.gacha import GachaService
    route = respx.post(f"{BASE}/api/generatePack").mock(
        return_value=Response(200, json={"memo": "m", "transaction": "T"}))
    svc = GachaService(base_url=BASE, api_key="")
    await svc.generate_pack(player_address="P", pack_type="pokemon_50", alt_player_address="ESCROW")
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"playerAddress": "P", "packType": "pokemon_50", "altPlayerAddress": "ESCROW"}


@respx.mock
@pytest.mark.asyncio
async def test_generate_pack_omits_alt_when_none():
    from app.services.gacha import GachaService
    route = respx.post(f"{BASE}/api/generatePack").mock(
        return_value=Response(200, json={"memo": "m", "transaction": "T"}))
    svc = GachaService(base_url=BASE, api_key="")
    await svc.generate_pack(player_address="P", pack_type="pokemon_50")
    sent = json.loads(route.calls.last.request.content)
    assert "altPlayerAddress" not in sent


# ── Sobres pendientes ─────────────────────────────────────────────────────────
# La fila de GachaPack se crea al GENERAR, antes de pagar. Por eso "pendiente" exige
# submitted_at: si no, la lista incluiría tiradas que el usuario abandonó sin comprar nada y le
# estaríamos diciendo que tiene sobres —y dinero— que jamás gastó.

def _mock_pack_upstream(memo="slug-p1"):
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_50", "price": 50, "available": True}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    respx.post(f"{BASE}/api/generatePack").mock(
        return_value=Response(200, json={"memo": memo, "transaction": "dA=="}))
    respx.post(f"{BASE}/api/submitTransaction").mock(
        return_value=Response(200, json={"signature": "sig", "confirmationStatus": "confirmed"}))


@respx.mock
def test_pending_excluye_los_sobres_generados_pero_nunca_pagados(monkeypatch):
    _mock_pack_upstream()
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client()
    hdrs = _hdrs(priv, WALLET_A)

    c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs)
    r = c.get("/gacha/packs/pending", headers=hdrs)
    assert r.status_code == 200
    assert r.json() == [], "un sobre generado y no pagado NO es un pendiente"


@respx.mock
def test_submit_tx_con_memo_marca_el_sobre_como_pagado(monkeypatch):
    _mock_pack_upstream()
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client()
    hdrs = _hdrs(priv, WALLET_A)

    c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs)
    assert c.post("/gacha/submit-tx",
                  json={"signed_transaction": "dGVzdA==", "memo": "slug-p1"},
                  headers=hdrs).status_code == 200

    body = c.get("/gacha/packs/pending", headers=hdrs).json()
    assert [p["memo"] for p in body] == ["slug-p1"]
    assert body[0]["pack_type"] == "pokemon_50"
    assert body[0]["submitted_at"]


@respx.mock
def test_submit_tx_sin_memo_sigue_funcionando(monkeypatch):
    """Los buybacks y transferencias usan la misma ruta y no tienen memo asociado."""
    _mock_pack_upstream()
    c, priv = _client()
    hdrs = _hdrs(priv, WALLET_A)
    assert c.post("/gacha/submit-tx", json={"signed_transaction": "dGVzdA=="},
                  headers=hdrs).status_code == 200


@respx.mock
def test_pending_sigue_listando_un_sobre_abierto_pero_no_visto(monkeypatch):
    _mock_pack_upstream(memo="slug-p2")
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json={
        "success": True, "nft_address": "Mint" + "2" * 40, "rarity": "Rare",
        "nftWon": {"content": {"metadata": {"name": "Pika"}}, "image": "https://x/p.png"}}))
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client()
    hdrs = _hdrs(priv, WALLET_A)

    c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs)
    c.post("/gacha/submit-tx", json={"signed_transaction": "dGVzdA==", "memo": "slug-p2"}, headers=hdrs)
    assert len(c.get("/gacha/packs/pending", headers=hdrs).json()) == 1

    # El servidor abre el sobre en cuanto CC lo resuelve, PERO el reveal espera al click del
    # jugador. Si abrir lo quitara de pendientes, cerrar la pestaña en esa ventana —que es justo
    # donde el sobre 3D se queda esperando— perdería el reveal para siempre.
    c.post("/gacha/open-pack", json={"memo": "slug-p2"}, headers=hdrs)
    body = c.get("/gacha/packs/pending", headers=hdrs).json()
    assert len(body) == 1, "abierto por CC pero no visto por el jugador: sigue pendiente"
    assert body[0]["nft_address"], "trae la carta, para reproducir el reveal sin reabrirlo"

    # Solo verlo lo saca de la lista.
    assert c.post("/gacha/packs/revealed", json={"memos": ["slug-p2"]},
                  headers=hdrs).json() == {"marked": 1}
    assert c.get("/gacha/packs/pending", headers=hdrs).json() == []


@respx.mock
def test_pending_no_filtra_sobres_de_otra_wallet(monkeypatch):
    _mock_pack_upstream(memo="slug-p3")
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client()

    hdrs_a = _hdrs(priv, WALLET_A)
    c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs_a)
    c.post("/gacha/submit-tx", json={"signed_transaction": "dGVzdA==", "memo": "slug-p3"}, headers=hdrs_a)

    assert c.get("/gacha/packs/pending", headers=_hdrs(priv, WALLET_B)).json() == []
    assert len(c.get("/gacha/packs/pending", headers=hdrs_a).json()) == 1


def test_pending_requiere_auth():
    c, _ = _client()
    assert c.get("/gacha/packs/pending").status_code == 401


@respx.mock
def test_marcar_visto_es_idempotente_y_solo_afecta_a_tu_wallet(monkeypatch):
    _mock_pack_upstream(memo="slug-p4")
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client()
    hdrs_a = _hdrs(priv, WALLET_A)
    c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs_a)
    c.post("/gacha/submit-tx", json={"signed_transaction": "dGVzdA==", "memo": "slug-p4"}, headers=hdrs_a)

    # otra wallet no puede marcarlo
    assert c.post("/gacha/packs/revealed", json={"memos": ["slug-p4"]},
                  headers=_hdrs(priv, WALLET_B)).json() == {"marked": 0}
    assert len(c.get("/gacha/packs/pending", headers=hdrs_a).json()) == 1

    assert c.post("/gacha/packs/revealed", json={"memos": ["slug-p4"]}, headers=hdrs_a).json() == {"marked": 1}
    # repetir no vuelve a contar ni revienta
    assert c.post("/gacha/packs/revealed", json={"memos": ["slug-p4"]}, headers=hdrs_a).json() == {"marked": 0}
    assert c.get("/gacha/packs/pending", headers=hdrs_a).json() == []


def test_marcar_visto_requiere_auth():
    c, _ = _client()
    assert c.post("/gacha/packs/revealed", json={"memos": ["x"]}).status_code == 401


@respx.mock
def test_la_rareza_se_guarda_al_abrir_y_viaja_en_pendientes(monkeypatch):
    """/gacha/nft/{mint} devuelve rarity null: la rareza SOLO la da CC al abrir. Si no se
    persiste, un reveal reproducido más tarde no puede mostrarla nunca."""
    _mock_pack_upstream(memo="slug-p5")
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json={
        "success": True, "nft_address": "Mint" + "5" * 40, "rarity": "Epic",
        "nftWon": {"content": {"metadata": {"name": "Zard"}}, "image": "https://x/z.png"}}))
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client()
    hdrs = _hdrs(priv, WALLET_A)

    c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs)
    c.post("/gacha/submit-tx", json={"signed_transaction": "dGVzdA==", "memo": "slug-p5"}, headers=hdrs)
    c.post("/gacha/open-pack", json={"memo": "slug-p5"}, headers=hdrs)

    body = c.get("/gacha/packs/pending", headers=hdrs).json()
    assert len(body) == 1
    assert body[0]["rarity"] == "Epic", "sin esto el reveal reproducido sale sin rareza"
    assert body[0]["name"] == "Zard"


@respx.mock
def test_el_autosell_del_turbo_se_guarda_y_viaja_en_pendientes(monkeypatch):
    """Con turbo CC recompra la carta al abrir. Si no se persiste, un reveal reproducido desde la
    lista ofrece "Keep" y "Sell" de un NFT que ya no es del jugador."""
    _mock_pack_upstream(memo="slug-p6")
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json={
        "code": "TURBO_MODE_BUYBACK", "buybackAmount": 12_500_000,
        "nft_address": "Mint" + "6" * 40, "rarity": "Common",
        "nftWon": {"content": {"metadata": {"name": "Pidgey"}}, "image": "https://x/p.png"}}))
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)
    c, priv = _client()
    hdrs = _hdrs(priv, WALLET_A)

    c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs)
    c.post("/gacha/submit-tx", json={"signed_transaction": "dGVzdA==", "memo": "slug-p6"}, headers=hdrs)
    c.post("/gacha/open-pack", json={"memo": "slug-p6"}, headers=hdrs)

    body = c.get("/gacha/packs/pending", headers=hdrs).json()
    assert len(body) == 1
    assert body[0]["auto_sold"] is True
    assert body[0]["buyback_amount"] == 12_500_000


# ── máquinas apagadas a mano ──────────────────────────────────────────────────
# Se apagan desde scripts/machines.py y el backend lo lee en cada petición, así que el efecto es
# inmediato sin reiniciar. Lo que se comprueba aquí es que el filtro está en el CATÁLOGO.

def _dos_maquinas():
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_50", "name": "P50", "price": 50, "odds": {}, "stock": {},
         "ev": 1.0, "image": None, "turboMode": True},
        {"code": "sweet_99", "name": "Sweets", "price": 99, "odds": {}, "stock": {},
         "ev": 1.0, "image": None, "turboMode": True}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))


@respx.mock
def test_una_maquina_apagada_no_sale_en_el_catalogo():
    from app.services.machine_visibility import hide
    _dos_maquinas()
    c, _ = _client(api_key="")
    assert {m["code"] for m in c.get("/gacha/machines").json()} == {"pokemon_50", "sweet_99"}

    with c.session_factory() as s:
        hide(s, "sweet_99", reason="miniatura rota")

    # Sin reiniciar nada ni tocar la caché del catálogo de CC: la siguiente petición ya no la trae.
    assert {m["code"] for m in c.get("/gacha/machines").json()} == {"pokemon_50"}


@respx.mock
def test_volver_a_encenderla_la_devuelve_al_catalogo():
    from app.services.machine_visibility import hide, show
    _dos_maquinas()
    c, _ = _client(api_key="")
    with c.session_factory() as s:
        hide(s, "sweet_99")
    assert {m["code"] for m in c.get("/gacha/machines").json()} == {"pokemon_50"}
    with c.session_factory() as s:
        show(s, "sweet_99")
    assert {m["code"] for m in c.get("/gacha/machines").json()} == {"pokemon_50", "sweet_99"}


@respx.mock
def test_apagar_la_maquina_no_le_quita_los_gimmighouls_a_quien_ya_tiro(monkeypatch):
    """Apagar una máquina es una decisión de catálogo POSTERIOR a la compra del jugador.

    El precio del sobre se anota al abrirlo, y esa anotación es lo que dispara la recompensa de
    lealtad. Si se resolviese sobre el catálogo filtrado, apagar la máquina justo entre la compra y
    la apertura dejaba el precio a None y el jugador se quedaba sin sus gimmighouls, en silencio.
    """
    from app.models import GachaPack, User
    from app.services.machine_visibility import hide
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "pokemon_50", "price": 50, "available": True}]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    respx.post(f"{BASE}/api/generatePack").mock(
        return_value=Response(200, json={"memo": "slug-off", "transaction": "dA=="}))
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json={
        "success": True, "nft_address": "Mint" + "9" * 40, "rarity": "Rare",
        "nftWon": {"content": {"metadata": {"name": "Pika"}}, "image": "https://x/p.png"}}))
    async def _high_bal(*a, **kw): return 100_000_000
    monkeypatch.setattr("app.main.usdc_balance_base_units", _high_bal)

    c, priv = _client()
    hdrs = _hdrs(priv, WALLET_A)
    c.post("/gacha/generate-pack", json={"pack_type": "pokemon_50"}, headers=hdrs)

    with c.session_factory() as s:      # se apaga DESPUÉS de comprar, antes de abrir
        hide(s, "pokemon_50", reason="retirada del catálogo")

    assert c.post("/gacha/open-pack", json={"memo": "slug-off"}, headers=hdrs).status_code == 200

    with c.session_factory() as s:
        pack = s.get(GachaPack, "slug-off")
        assert pack.price == 50_000_000, "el precio del sobre ya comprado tiene que resolverse igual"
        assert (s.get(User, WALLET_A).gimmighouls or 0) > 0, "y su recompensa no puede perderse"


# ── Tiradas gratis con puntos de Collector Crypt ──────────────────────────────
#
# Endpoints NO documentados por CC; el contrato se estableció midiendo contra devnet:
#   GET  /api/freeSpins?wallet=…  → puntos, tiradas disponibles y coste por tirada
#   POST /api/freePack            → { publicKey, packType, turbo, transactionSignature } → memo
# `transactionSignature` es una transacción firmada por la wallet que sirve de PRUEBA DE
# PROPIEDAD y que CC no envía a la cadena. La firma el backend con la wallet delegada.

# WALLET_A/B son de relleno y NO son direcciones base58 válidas, así que no sirven aquí: el
# canje construye una transacción de verdad y `Pubkey.from_string` las rechaza.
WALLET_REAL = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"


class _SignerFalso:
    """Firma cualquier cosa. Solo interesa que el endpoint le pase el wallet_id correcto."""
    enabled = True

    def __init__(self):
        self.visto = []

    async def sign_solana(self, wallet_id, tx_b64):
        self.visto.append((wallet_id, tx_b64))
        return "FIRMADA"


def _client_con_firmante(api_key="k123"):
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)
    priv = make_es256()
    privy = PrivyVerifier(app_id=APP_ID, key_resolver=lambda kid: priv.public_key())
    gacha = GachaService(base_url=BASE, api_key=api_key)
    firmante = _SignerFalso()
    app = create_app(sf, MockChainSource(), elo_start=1200, elo_k=32,
                     gacha=gacha, gacha_rate_limit=10, privy=privy, privy_signer=firmante,
                     solana_rpc_url="https://rpc.test")
    return TestClient(app), priv, firmante, sf


def _hdrs_con_id(priv, wallet, wallet_id="wid-1"):
    """Como _hdrs, pero con el `id` de la wallet, que es lo que necesita firmar."""
    from tests.conftest import make_id_token
    cuenta = {"type": "wallet", "chain_type": "solana", "connector_type": None,
              "wallet_client_type": "privy", "address": wallet, "id": wallet_id}
    return {"Authorization": f"Bearer {make_id_token(priv, APP_ID, [cuenta])}"}


def _maquinas(mock):
    """El catálogo que consulta `_machine_price` antes de dejar estrenar una tirada."""
    mock.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    mock.get(f"{BASE}/api/machines").mock(return_value=Response(200, json=[
        {"code": "pokemon_50", "name": "Elite", "price": 50}]))


def _rpc(mock):
    """El blockhash que necesita la transacción-prueba. Vale cualquiera válido en base58."""
    mock.post("https://rpc.test/").mock(return_value=Response(200, json={
        "jsonrpc": "2.0", "id": 1,
        "result": {"value": {"blockhash": "11111111111111111111111111111111"}}}))


def _spins(mock, **over):
    # `freeSpinsLeft`/`pointsPerSpin`/`pointsUntilNextSpin` vienen en la respuesta real de CC pero
    # están calculados sobre una máquina de 50 $. Se dejan aquí a propósito, con valores que NO
    # cuadran con los puntos, para que un test falle si volviéramos a propagarlos.
    d = {"points": 250000, "usedPoints": 0, "freeSpinsLeft": 99, "freeSpinsLeftToday": 2,
         "pointsPerSpin": 100000, "pointsUntilNextSpin": 1}
    d.update(over)
    mock.get(f"{BASE}/api/freeSpins").mock(return_value=Response(200, json=d))


@respx.mock
def test_free_spins_da_los_puntos_de_la_wallet_y_nada_por_maquina():
    c, priv = _client()
    _spins(respx)
    r = c.get("/users/me/free-spins", headers=_hdrs(priv, WALLET_A))
    assert r.status_code == 200, r.text
    # Solo los puntos y el tope diario. Cuántas tiradas dan depende de la máquina, así que no se
    # puede responder aquí — y los campos que CC manda calculados sobre la de 50 $ se descartan.
    assert r.json() == {"points_available": 250000, "spins_left_today": 2}


@respx.mock
def test_free_spins_descuenta_los_puntos_ya_gastados():
    c, priv = _client()
    _spins(respx, points=250000, usedPoints=90000)
    r = c.get("/users/me/free-spins", headers=_hdrs(priv, WALLET_A))
    assert r.json()["points_available"] == 160000


def _nonce(mock, valor="nonce-abc-123"):
    """El nonce del canje, que CC empezó a exigir. Ver `test_free_pack_pide_el_nonce…`."""
    return mock.post(f"{BASE}/api/generateFreePack").mock(
        return_value=Response(200, json={"success": True, "nonce": valor,
                                         "expiry": "2030-01-01T00:00:00.000Z"}))


@respx.mock
def test_free_pack_pide_el_nonce_y_lo_manda_firmado_dentro_de_la_transaccion():
    """CC endureció el canje: la prueba de propiedad ya no es una transacción cualquiera.

    Ahora hay que pedir un `nonce` a `/api/generateFreePack` y devolverlo por DOS vías a la vez:
    en el cuerpo de `/api/freePack` y, sobre todo, DENTRO de la transacción firmada, como
    contenido de la instrucción memo. Mandar el formato viejo responde
    `400 {"error":"Missing or invalid nonce"}` y ninguna tirada gratis se puede canjear.
    """
    c, priv, firmante, sf = _client_con_firmante()
    _maquinas(respx)
    _rpc(respx)
    _spins(respx)
    gen = _nonce(respx)
    ruta = respx.post(f"{BASE}/api/freePack").mock(
        return_value=Response(200, json={"success": True, "memo": "cc-libre-9",
                                         "remainingPoints": 0}))

    r = c.post("/gacha/free-pack", json={"pack_type": "pokemon_50"},
               headers=_hdrs_con_id(priv, WALLET_REAL))
    assert r.status_code == 200, r.text

    # El nonce se pide para ESTA wallet y ESTA máquina.
    assert json.loads(gen.calls[0].request.content) == {
        "publicKey": WALLET_REAL, "packType": "pokemon_50"}

    # Y viaja por las dos vías: en el cuerpo…
    assert json.loads(ruta.calls[0].request.content)["nonce"] == "nonce-abc-123"
    # …y dentro de la transacción que se firmó, que es la que CC comprueba de verdad.
    import base64 as _b64
    _, tx_sin_firmar = firmante.visto[0]
    assert b"nonce-abc-123" in _b64.b64decode(tx_sin_firmar)


@respx.mock
def test_free_pack_sigue_funcionando_donde_no_existe_el_nonce():
    """El nonce lo estrenó MAINNET; devnet sigue con el contrato viejo.

    Los hosts de CC no van a la par, así que exigir el nonce en todas las redes dejaba devnet con
    un 502 y ninguna tirada gratis: `/api/generateFreePack` allí devuelve 404. Cuando el endpoint
    no existe se canjea como antes, con el memo de siempre y sin `nonce` en el cuerpo.
    """
    c, priv, firmante, sf = _client_con_firmante()
    _maquinas(respx)
    _rpc(respx)
    _spins(respx)
    # 404 = "esta red todavía no pide nonce", que NO es lo mismo que "CC está caído".
    respx.post(f"{BASE}/api/generateFreePack").mock(return_value=Response(404, text="Not Found"))
    ruta = respx.post(f"{BASE}/api/freePack").mock(
        return_value=Response(200, json={"success": True, "memo": "cc-viejo-1",
                                         "remainingPoints": 10}))

    r = c.post("/gacha/free-pack", json={"pack_type": "pokemon_50"},
               headers=_hdrs_con_id(priv, WALLET_REAL))
    assert r.status_code == 200, r.text
    assert r.json()["memo"] == "cc-viejo-1"

    # Sin nonce en el cuerpo: mandarlo vacío o nulo sería inventarse un contrato que allí no existe.
    enviado = json.loads(ruta.calls[0].request.content)
    assert "nonce" not in enviado
    assert enviado["transactionSignature"] == "FIRMADA"


@respx.mock
def test_free_pack_no_se_traga_una_caida_de_cc_como_si_fuera_la_red_vieja():
    """Un fallo de verdad de CC no puede confundirse con "esta red no pide nonce".

    Si se tratara igual, en mainnet se acabaría mandando el formato viejo y el jugador vería
    "Missing or invalid nonce", que no explica nada. Solo el 404 significa "no existe aquí".
    """
    c, priv, _, _ = _client_con_firmante()
    _maquinas(respx)
    _rpc(respx)
    _spins(respx)
    respx.post(f"{BASE}/api/generateFreePack").mock(
        return_value=Response(500, json={"error": "boom"}))
    libre = respx.post(f"{BASE}/api/freePack")

    r = c.post("/gacha/free-pack", json={"pack_type": "pokemon_50"},
               headers=_hdrs_con_id(priv, WALLET_REAL))
    assert r.status_code == 502, r.text
    assert not libre.called       # no se intenta el canje a ciegas


@respx.mock
def test_free_pack_canjea_y_deja_el_sobre_listo_para_abrir():
    c, priv, firmante, sf = _client_con_firmante()
    _maquinas(respx)
    _rpc(respx)
    _spins(respx)
    _nonce(respx)
    ruta = respx.post(f"{BASE}/api/freePack").mock(
        return_value=Response(200, json={"success": True, "memo": "cc-libre-1",
                                         "remainingPoints": 150000}))
    r = c.post("/gacha/free-pack", json={"pack_type": "pokemon_50"},
               headers=_hdrs_con_id(priv, WALLET_REAL))
    assert r.status_code == 200, r.text
    assert r.json()["memo"] == "cc-libre-1"

    enviado = json.loads(ruta.calls[0].request.content)
    assert enviado["publicKey"] == WALLET_REAL
    assert enviado["packType"] == "pokemon_50"
    assert enviado["transactionSignature"] == "FIRMADA"   # la prueba de propiedad va firmada
    assert firmante.visto[0][0] == "wid-1"                # y con la wallet del JUGADOR

    with sf() as s:
        pack = s.get(GachaPack, "cc-libre-1")
        assert pack.wallet == WALLET_REAL
        assert pack.price == 0                 # gratis: no se le cobró nada
        assert pack.submitted_at is not None   # sin pago pendiente, listo para abrir


@respx.mock
def test_free_pack_sin_tiradas_dice_cuantos_puntos_faltan():
    # Un 409 con la cifra exacta, en vez de dejar que CC devuelva un error opaco.
    c, priv, _, _ = _client_con_firmante()
    _maquinas(respx)
    _spins(respx, points=92389, usedPoints=0)      # faltan 7.611 para los 100.000 de la de 50 $
    llamada = respx.post(f"{BASE}/api/freePack")
    r = c.post("/gacha/free-pack", json={"pack_type": "pokemon_50"},
               headers=_hdrs_con_id(priv, WALLET_REAL))
    assert r.status_code == 409
    assert "7611" in r.json()["detail"]
    assert not llamada.called      # ni se le pide nada a CC ni se firma nada


@respx.mock
def test_free_pack_mide_los_puntos_contra_el_precio_de_esa_maquina():
    """Los mismos puntos bastan en la máquina barata y no en la cara.

    Es el fallo que motivó el cambio: se miraba `freeSpinsLeft`, que CC calcula siempre sobre una
    máquina de 50 $, así que en la de 250 $ se dejaba pasar una tirada que no existía y el jugador
    se comía el error de CC después de haber firmado.
    """
    c, priv, _, _ = _client_con_firmante()
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json=[
        {"code": "cara_250", "name": "Cara", "price": 250, "freeSpins": True}]))
    _spins(respx, points=300000, usedPoints=0)     # 3 tiradas de 50 $, ninguna de 250 $
    llamada = respx.post(f"{BASE}/api/freePack")
    r = c.post("/gacha/free-pack", json={"pack_type": "cara_250"},
               headers=_hdrs_con_id(priv, WALLET_REAL))
    assert r.status_code == 409
    assert "200000" in r.json()["detail"]          # 500.000 − 300.000
    assert not llamada.called


@respx.mock
def test_free_pack_traduce_los_rechazos_de_cc():
    # CC distingue "esta máquina no da gratis" de "sin stock"; las dos son cosas que el jugador
    # entiende, así que no se esconden detrás de un 502 mudo. Se devuelven como CÓDIGO: el texto
    # que lee el jugador lo escribe el frontend (ver `freePackErrors.ts`).
    for detalle, espera in [("Invalid pack type", "machine_no_free_spins"),
                            ("Machine is low", "machine_out_of_cards")]:
        c, priv, _, _ = _client_con_firmante()
        _maquinas(respx)
        _rpc(respx)
        _spins(respx)
        _nonce(respx)
        respx.post(f"{BASE}/api/freePack").mock(
            return_value=Response(400, json={"error": detalle}))
        r = c.post("/gacha/free-pack", json={"pack_type": "pokemon_50"},
                   headers=_hdrs_con_id(priv, WALLET_REAL))
        assert r.status_code == 409, r.text
        assert espera in r.json()["detail"]


# ── Replay: repetir una tirada ya hecha, para poder enseñarla ──────────────────────────────────
#
# Se apoya en que `openPack` de CC es idempotente: repetirlo sobre un memo ya abierto devuelve la
# misma carta. Es su propio mecanismo (?replay=<memo>), medido contra su API.

_CARTA = {"success": True, "nft_address": "NFT1", "rarity": "Epic",
          "nftWon": {"id": "NFT1", "content": {"metadata": {"name": "Charizard"},
                                               "files": [{"uri": "https://x/c.jpg"}]},
                     "insuredValue": 500}}


def _pack_abierto(c, memo="cc-replay-1", wallet=WALLET_A):
    from app.models import GachaPack
    from datetime import datetime, timezone
    with c.session_factory() as s:
        s.add(GachaPack(memo=memo, wallet=wallet, pack_type="pokemon_50",
                        opened_at=datetime.now(timezone.utc), nft_address="NFT1"))
        s.commit()


@respx.mock
def test_replay_devuelve_la_tirada_sin_autenticarse():
    """Público a propósito: el enlace tiene que verse sin cuenta o no sirve para enseñar nada."""
    c, _ = _client()
    _pack_abierto(c)
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json=_CARTA))
    r = c.get("/gacha/replay/cc-replay-1")          # sin cabecera Authorization
    assert r.status_code == 200, r.text
    assert r.json()["nft_address"] == "NFT1" and r.json()["rarity"] == "Epic"


@respx.mock
def test_replay_vale_para_una_tirada_de_batalla():
    """Las de batalla son donde están los buenos pulls; viven en battle_pulls, no en gacha_packs."""
    from app.models import BattlePull
    c, _ = _client()
    with c.session_factory() as s:
        s.add(BattlePull(battle_id="b1", player_wallet=WALLET_A, memo="cc-batalla-1",
                         nft_address="NFT1"))
        s.commit()
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json=_CARTA))
    assert c.get("/gacha/replay/cc-batalla-1").status_code == 200


@respx.mock
def test_replay_no_es_un_proxy_a_cc_para_memos_ajenos():
    """Sin este filtro, cualquiera podría consultar memos que no son nuestros a través nuestro."""
    c, _ = _client()
    llamada = respx.post(f"{BASE}/api/openPack")
    r = c.get("/gacha/replay/cc-de-otro")
    assert r.status_code == 404
    assert not llamada.called          # ni se le pregunta a CC


@respx.mock
def test_replay_no_reabre_ni_cobra_nada():
    """Repetir una tirada no es volver a hacerla: no se toca `opened_at` ni el precio."""
    from app.models import GachaPack
    from datetime import datetime, timezone
    c, _ = _client()
    antes = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with c.session_factory() as s:
        s.add(GachaPack(memo="cc-replay-1", wallet=WALLET_A, pack_type="pokemon_50",
                        opened_at=antes, nft_address="NFT1", price=50_000_000))
        s.commit()
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json=_CARTA))
    assert c.get("/gacha/replay/cc-replay-1").status_code == 200
    with c.session_factory() as s:
        p = s.get(GachaPack, "cc-replay-1")
        assert p.opened_at.replace(tzinfo=timezone.utc) == antes
        assert p.price == 50_000_000


@respx.mock
def test_replay_de_un_sobre_nunca_abierto():
    """Distinto de "no existe": para quien abrió el enlace no es lo mismo."""
    from app.models import GachaPack
    c, _ = _client()
    with c.session_factory() as s:
        s.add(GachaPack(memo="cc-sin-abrir", wallet=WALLET_A, pack_type="pokemon_50"))
        s.commit()
    respx.post(f"{BASE}/api/openPack").mock(
        return_value=Response(200, json={"code": "WAITING_FOR_WEBHOOK"}))
    r = c.get("/gacha/replay/cc-sin-abrir")
    assert r.status_code == 409
    assert "never opened" in r.json()["detail"]


@respx.mock
def test_replay_limitado_por_ip():
    """Es público, así que no hay wallet a la que cobrarle el límite: sin esto seríamos un proxy
    gratis a Collector Crypt, una llamada suya por visita."""
    c, _ = _client(rate_limit=3)
    _pack_abierto(c)
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json=_CARTA))
    codigos = [c.get("/gacha/replay/cc-replay-1").status_code for _ in range(5)]
    assert codigos[:3] == [200, 200, 200]
    assert codigos[3] == 429 and codigos[4] == 429

# ── El replay tiene que traer SU máquina, no la que el jugador tenga abierta ───────────────────
#
# Era el fallo: el replay solo devolvía la carta, así que la pantalla no podía saber la recompra de
# esa tirada y usaba la de `selected`. Un replay de una máquina al 90% se veía al 85%.

def _maquinas_mock(*, code="pokemon_250", buyback=90):
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": code, "name": "Elite", "shortName": "ELITE", "price": 250, "odds": {},
         "stock": {}, "ev": 1.0, "image": None, "instantBuyback": buyback},
        {"code": "pokemon_50", "name": "P50", "price": 50, "odds": {}, "stock": {},
         "ev": 1.0, "image": None, "instantBuyback": 85},
    ]}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))


@respx.mock
def test_replay_trae_la_recompra_de_SU_maquina():
    """Y no la de otra: es exactamente el fallo que se vio, un 90% enseñado como 85%."""
    c, _ = _client()
    _pack_abierto(c, memo="cc-r90")
    with c.session_factory() as s:
        from app.models import GachaPack
        s.get(GachaPack, "cc-r90").pack_type = "pokemon_250"
        s.commit()
    _maquinas_mock()
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json=_CARTA))
    d = c.get("/gacha/replay/cc-r90").json()
    assert d["machine"] == "pokemon_250"
    assert d["buyback_pct"] == 90            # NO 85, que es el de pokemon_50
    assert d["pack_price"] == 250


@respx.mock
def test_una_maquina_que_no_se_puede_resolver_NO_inventa_recompra():
    """Mejor no enseñar recompra que enseñar la de otra máquina."""
    c, _ = _client()
    _pack_abierto(c, memo="cc-rara")
    with c.session_factory() as s:
        from app.models import GachaPack
        s.get(GachaPack, "cc-rara").pack_type = "maquina_retirada"
        s.commit()
    _maquinas_mock()
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json=_CARTA))
    d = c.get("/gacha/replay/cc-rara").json()
    assert d["machine"] == "maquina_retirada"
    assert d["buyback_pct"] is None


@respx.mock
def test_el_replay_de_una_batalla_saca_la_maquina_de_la_partida():
    """Las tiradas de batalla no guardan la máquina: se llega por la partida."""
    from app.models import BattlePull, PackBattle
    c, _ = _client()
    with c.session_factory() as s:
        s.add(PackBattle(id="b9", mode="royale", machine_code="pokemon_250", status="settled",
                         price=250_000_000, max_players=5))
        s.add(BattlePull(battle_id="b9", player_wallet=WALLET_A, memo="cc-b9", nft_address="NFT1"))
        s.commit()
    _maquinas_mock()
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json=_CARTA))
    d = c.get("/gacha/replay/cc-b9").json()
    assert d["machine"] == "pokemon_250" and d["buyback_pct"] == 90


@respx.mock
def test_si_CC_no_contesta_la_lista_el_replay_sigue_saliendo():
    """La carta es lo importante; la recompra es un extra y no puede tumbar el replay."""
    c, _ = _client()
    _pack_abierto(c, memo="cc-sinlista")
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(502, json={}))
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={"gachas": []}))
    respx.post(f"{BASE}/api/openPack").mock(return_value=Response(200, json=_CARTA))
    r = c.get("/gacha/replay/cc-sinlista")
    assert r.status_code == 200
    assert r.json()["nft_address"] == "NFT1"
    assert r.json()["buyback_pct"] is None


# ── El tracker solo enseña máquinas que se pueden jugar AHORA ──────────────────────────────────

def _ev_machines():
    respx.get(f"{BASE}/api/machines").mock(return_value=Response(200, json={"machines": [
        {"code": "viva", "name": "Viva", "price": 50, "odds": {}, "stock": {}, "ev": 1.0,
         "image": None, "instantBuyback": 85},
        {"code": "cerrada", "name": "Cerrada", "price": 50, "odds": {}, "stock": {}, "ev": 1.0,
         "image": None, "instantBuyback": 85},
    ]}))
    # `available` sale de /api/status: CC manda `code` + `status`, y solo "open" cuenta como
    # abierta. Lo que no aparece se asume abierto (ver `_availability`).
    respx.get(f"{BASE}/api/status").mock(return_value=Response(200, json={
        "gachas": [{"code": "viva", "status": "open"},
                   {"code": "cerrada", "status": "closed"}]}))


@respx.mock
def test_el_tracker_no_enseña_una_maquina_que_CC_tiene_cerrada():
    """Medir el EV de una máquina a la que nadie puede tirar ocupa sitio y sugiere una decisión
    que no se puede tomar."""
    # El tracker cerró sus puertas (tarea 7): la wallet de este test entra por la lista blanca de
    # la casa, que es la vía que no exige sembrar una batalla ni un TrackerPass para probar un
    # filtro que no tiene nada que ver con el acceso.
    c, priv = _client(tracker_access_allowlist={WALLET_A})
    _ev_machines()
    codigos = [f["machine"] for f in c.get("/gacha/ev", headers=_hdrs(priv, WALLET_A)).json()["rows"]]
    assert "viva" in codigos
    assert "cerrada" not in codigos


@respx.mock
def test_el_tracker_no_enseña_una_maquina_que_hemos_apagado_nosotros():
    """El mismo filtro que el catálogo: si no se ofrece para jugar, no se mide en pantalla."""
    from app.services import machine_visibility
    c, priv = _client(tracker_access_allowlist={WALLET_A})
    _ev_machines()
    with c.session_factory() as s:
        machine_visibility.hide(s, "viva", reason="prueba")
    codigos = [f["machine"] for f in c.get("/gacha/ev", headers=_hdrs(priv, WALLET_A)).json()["rows"]]
    assert "viva" not in codigos
