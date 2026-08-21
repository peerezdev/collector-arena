"""Qué enseña `GET /gacha/ev` cuando Collector Crypt no contesta.

El endpoint necesita a CC para UNA cosa: la lista de máquinas con su precio y su recompra. Las
mediciones son nuestras, están en nuestra base de datos y no dependen de nadie. Aun así, hasta
ahora un fallo de CC se convertía en un 502 y la pantalla entera decía "Couldn't load the tracker",
tirando datos que teníamos delante.

La única protección eran dos cachés de sesenta segundos, así que bastaba un minuto de CC caído para
quedarnos sin tracker. Esto es lo que lo sustituye: si hay algo medido, se sirve lo último bueno
con SU hora, y la pantalla ya se encarga de marcarlo como viejo (`estaRancio`, 300 s).
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

# `/gacha/ev` cerró sus puertas en cuanto el tracker pasó a cobrarse (tarea 7): este fichero
# prueba el respaldo, no el acceso, así que la wallet de prueba se mete de oficio en la lista
# blanca — la vía de la casa, no la del wager ni la del pase — para no ensuciar cada test con el
# sembrado de una batalla o un `TrackerPass` que no viene a cuento aquí.
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
    # Headers por defecto y no por llamada: así ningún `client.get(...)` de este fichero tiene que
    # tocarse para llevar el token, y la wallet autenticada coincide con la de la lista blanca.
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


def test_con_CC_caido_sirve_lo_ULTIMO_BUENO_en_vez_de_un_502(client, monkeypatch):
    """El caso que rompía la pantalla entera."""
    _sembrar(client)
    _cc_responde(monkeypatch)
    primera = client.get("/gacha/ev")
    assert primera.status_code == 200, primera.text
    assert primera.json()["rows"], "hace falta algo medido para que haya respaldo"

    _cc_caido(monkeypatch)
    # `hours` distinto salta la caché de 60 s, que es lo que enmascaraba el problema.
    r = client.get("/gacha/ev?hours=47")
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == primera.json()["rows"]


def test_el_respaldo_conserva_SU_hora_y_no_finge_estar_recien_medido(client, monkeypatch):
    """Es lo que hace honesto al respaldo.

    La pantalla decide que algo está rancio comparando `updated_at` con el reloj (`estaRancio`,
    300 s). Si al servir lo viejo le pusiéramos la hora actual, el aviso STALE no saltaría NUNCA y
    estaríamos enseñando mediciones de hace horas como si fueran de ahora mismo.

    HACE FALTA ADELANTAR EL RELOJ. Sin esto el test pasa igual aunque el endpoint ponga la hora
    actual, porque las dos lecturas caen en el mismo segundo: comprobado mutando el código, la
    primera versión de este test no sujetaba nada.

    Se sustituye el `_time` que ve `main`, no `time.time` global, para no tocarle el reloj a
    httpx ni a nadie más durante la petición.
    """
    _sembrar(client)
    _cc_responde(monkeypatch)
    sello = client.get("/gacha/ev").json()["updated_at"]

    class RelojAdelantado:
        @staticmethod
        def time():
            return sello + 3600          # una hora después de la última medición buena
    monkeypatch.setattr(main_mod, "_time", RelojAdelantado)

    _cc_caido(monkeypatch)
    cuerpo = client.get("/gacha/ev?hours=47").json()
    assert cuerpo["updated_at"] == sello, "el respaldo tiene que llevar la hora en que se midió"
    assert cuerpo["updated_at"] < sello + 300, "y por tanto la pantalla lo marcará STALE"


def test_el_respaldo_se_marca_como_tal(client, monkeypatch):
    """Para poder distinguir en el log y en pruebas una medición fresca de una servida de reserva."""
    _sembrar(client)
    _cc_responde(monkeypatch)
    assert client.get("/gacha/ev").json().get("stale") is not True

    _cc_caido(monkeypatch)
    assert client.get("/gacha/ev?hours=47").json()["stale"] is True


def test_sin_NADA_medido_todavia_sigue_siendo_un_502(client, monkeypatch):
    """No hay respaldo que servir, así que mentir con una lista vacía sería peor: la pantalla diría
    "no machines measured yet" y parecería un problema de datos en vez de uno de red."""
    _cc_caido(monkeypatch)
    r = client.get("/gacha/ev")
    assert r.status_code == 502


def test_el_gacha_APAGADO_a_proposito_no_se_disfraza_de_respaldo(client, monkeypatch):
    """`gacha_base_url` vacío es el kill-switch, una decisión nuestra y no una caída de CC.

    Seguir sirviendo el tracker con datos guardados después de apagar el gacha a mano haría que el
    interruptor no apagara del todo.
    """
    _sembrar(client)
    _cc_responde(monkeypatch)
    assert client.get("/gacha/ev").status_code == 200

    _cc_caido(monkeypatch, GachaDisabled("gacha_disabled"))
    assert client.get("/gacha/ev?hours=47").status_code == 503


def test_cuando_CC_vuelve_se_deja_de_servir_el_respaldo(client, monkeypatch):
    """El respaldo es un puente, no un destino."""
    _sembrar(client)
    _cc_responde(monkeypatch)
    client.get("/gacha/ev")

    _cc_caido(monkeypatch)
    assert client.get("/gacha/ev?hours=47").json()["stale"] is True

    _cc_responde(monkeypatch)
    assert client.get("/gacha/ev?hours=46").json().get("stale") is not True
