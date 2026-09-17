"""El carril rápido del EV tracker: `GET /gacha/ev/live`.

Lleva SOLO las rachas por rareza. La frontera con `/gacha/ev` es lo que se prueba aquí, porque es
donde está el valor: el intervalo cuesta 4.000 remuestreos por máquina (~9 s las 48 en mainnet) y
no se mueve de forma apreciable en diez segundos, mientras que las rachas cambian con cada tirada
y cuestan una consulta.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.db import init_db, make_session_factory
from app.main import create_app
from app.privy import PrivyVerifier
from app.services.gacha import GachaService
from app.services.winners_store import guardar
from tests.conftest import make_es256, privy_auth_headers
from tests.test_chain_mock import MockChainSource

AHORA = datetime.now(timezone.utc)

# `/gacha/ev/live` closed its doors together with `/gacha/ev` (task 7): same as in
# `test_ev_respaldo.py`, the test wallet gets in through the house's whitelist so this file can
# keep testing only the fast lane, not access.
WALLET = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"


def _build_client():
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
    c.headers.update(privy_auth_headers(priv, app_id, WALLET))
    return c


def _sembrar(client, machine: str, tiers, *, base=AHORA):
    with client.session_factory() as s:
        guardar(s, [{"nft_address": f"{machine}-{i}", "machine": machine, "prize_tier": t,
                     "insured_value": 40.0, "weighted_insured_value": None, "memo": None,
                     "winner": "W", "created_at": base - timedelta(minutes=len(tiers) - i),
                     "source": "live"} for i, t in enumerate(tiers)])


@pytest.fixture()
def client():
    return _build_client()


def test_devuelve_las_rachas_de_cada_maquina_con_datos(client):
    _sembrar(client, "pokemon_50", [2] + [4] * 9)          # Rare y luego 9 Commons
    r = client.get("/gacha/ev/live")
    assert r.status_code == 200, r.text
    filas = r.json()["rows"]
    assert [f["machine"] for f in filas] == ["pokemon_50"]
    rare = next(t for t in filas[0]["tiers"] if t["tier"] == "Rare")
    assert rare["current"] == 9


def test_NO_trae_el_edge_ni_el_intervalo_ni_el_veredicto(client):
    """La frontera entera del carril. Si un día se colara el edge aquí, se estaría pagando el
    bootstrap cada diez segundos sin que nadie lo hubiera decidido."""
    _sembrar(client, "pokemon_50", [4] * 40)
    fila = client.get("/gacha/ev/live").json()["rows"][0]
    assert set(fila) == {"machine", "tiers"}


def test_no_llama_a_collector_crypt(client, monkeypatch):
    """Un sondeo cada diez segundos contra su API sería maleducado, y además haría depender el
    refresco de que ellos respondan."""
    async def _explota(*a, **k):
        raise AssertionError("el carril rápido no debe llamar a CC")
    monkeypatch.setattr(GachaService, "machines", _explota)
    monkeypatch.setattr(GachaService, "winners_raw", _explota)
    _sembrar(client, "pokemon_50", [4] * 5)
    assert client.get("/gacha/ev/live").status_code == 200


def test_sin_datos_devuelve_una_lista_vacia_y_no_un_error(client):
    r = client.get("/gacha/ev/live")
    assert r.status_code == 200 and r.json()["rows"] == []


def test_la_racha_mira_mas_alla_de_la_ventana_del_ev(client):
    """Igual que en `tier_gaps`: una racha se cuenta en tiradas, no en tiempo. En una máquina de
    tres tiradas al día, recortarla a 48 h solo alcanzaba a decir "no he mirado"."""
    _sembrar(client, "comic_25", [1], base=AHORA - timedelta(days=30))     # el Epic, hace un mes
    _sembrar(client, "comic_25", [4] * 20)                                  # y 20 tiradas después
    epic = next(t for t in client.get("/gacha/ev/live").json()["rows"][0]["tiers"]
                if t["tier"] == "Epic")
    assert epic["current"] == 20
    assert 29.5 <= epic["days_since"] <= 30.5
