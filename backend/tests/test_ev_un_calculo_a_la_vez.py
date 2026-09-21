"""Muchas peticiones al EV tracker tienen que producir UN cálculo, no uno por petición.

POR QUÉ. Medido en mainnet el 17/09 con la base en 283 MB: `/gacha/ev` cuesta 54 s y su caché dura
60, así que está vencida casi siempre. Sin nada que agrupe las peticiones, cada una que entraba
durante esos 54 s arrancaba SU PROPIO cálculo; cada cálculo se lleva un hilo del pool de FastAPI
(40) y una conexión del de SQLAlchemy (15) y la retiene hasta acabar.

El resultado no fue "el tracker va lento": fue el backend entero mudo, con el pool de conexiones
reventado 630 veces en tres días y el chat sin cargar, porque los hilos que se quedaban esperando
conexión son los mismos que sirven todo lo demás.
"""
import asyncio
import time

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import main as main_mod
from app.db import init_db, make_session_factory
from app.main import create_app
from app.privy import PrivyVerifier
from app.services.gacha import GachaService
from tests.conftest import make_es256, privy_auth_headers
from tests.test_chain_mock import MockChainSource

# `/gacha/ev` stopped being public once the tracker started charging: what is tested here is that
# concurrent requests share one computation, not access, so the test wallet gets in through the
# whitelist (the house's path) and the client carries its token.
WALLET = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"

TARDANZA = 0.3


def _app(monkeypatch, contador):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)

    async def maquinas():
        return [{"code": "pokemon_50", "name": "Elite", "price": 50, "instantBuyback": 80}]

    def fila_lenta(s, code, **kw):
        contador["n"] += 1
        time.sleep(TARDANZA)
        return {"machine": code, "realized_edge_pct": 1.0}

    priv = make_es256()
    app_id = "app-test"
    app = create_app(sf, MockChainSource(),
                     gacha=GachaService(base_url="https://dev-gacha.example.com", api_key=""),
                     solana_rpc_url="https://api.devnet.solana.com",
                     privy=PrivyVerifier(app_id=app_id, key_resolver=lambda kid: priv.public_key()),
                     tracker_access_allowlist={WALLET})
    monkeypatch.setattr(GachaService, "machines", lambda self: maquinas())
    monkeypatch.setattr(main_mod, "fila_ev", fila_lenta)
    return app, privy_auth_headers(priv, app_id, WALLET)


@pytest.mark.asyncio
async def test_diez_peticiones_a_la_vez_producen_un_solo_calculo(monkeypatch):
    contador = {"n": 0}
    app, cabeceras = _app(monkeypatch, contador)
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://t", headers=cabeceras) as c:
        respuestas = await asyncio.gather(*[c.get("/gacha/ev", timeout=30) for _ in range(10)])

    assert all(r.status_code == 200 for r in respuestas)
    assert contador["n"] == 1, (
        f"{contador['n']} cálculos para 10 peticiones simultáneas: cada uno se lleva un hilo y "
        "una conexión, y así es como se agotó el pool en producción")


@pytest.mark.asyncio
async def test_todas_reciben_las_mismas_filas(monkeypatch):
    """Servir la copia anterior a quien llega tarde no puede significar servirle vacío."""
    contador = {"n": 0}
    app, cabeceras = _app(monkeypatch, contador)
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://t", headers=cabeceras) as c:
        respuestas = await asyncio.gather(*[c.get("/gacha/ev", timeout=30) for _ in range(5)])

    for r in respuestas:
        assert r.json()["rows"], "una respuesta sin filas no le sirve a nadie"


@pytest.mark.asyncio
async def test_la_cache_se_sella_al_TERMINAR_no_al_empezar(monkeypatch):
    """`updated_at` tiene que ser la hora de ACABAR el cálculo, no la de empezarlo.

    Sellarla al empezar la hace nacer caducada en cuanto el cálculo dura más que el TTL, y
    entonces el siguiente de la cola vuelve a calcular: el cerrojo agrupa la espera pero no ahorra
    ni un cálculo. Medido en mainnet el 17/09 con un cálculo de 106 s y una caché de 60: diez
    peticiones simultáneas produjeron diez cálculos CON el cerrojo puesto.

    Se usa un cálculo deliberadamente lento (2,5 s) porque con uno instantáneo las dos formas de
    sellar dan el mismo número y el test no probaría nada.
    """
    contador = {"n": 0}
    lento = 2.5

    def fila_muy_lenta(s, code, **kw):
        contador["n"] += 1
        time.sleep(lento)
        return {"machine": code, "realized_edge_pct": 1.0}

    app, cabeceras = _app(monkeypatch, contador)
    monkeypatch.setattr(main_mod, "fila_ev", fila_muy_lenta)

    t0 = time.time()
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://t", headers=cabeceras) as c:
        r = await c.get("/gacha/ev", timeout=30)

    sellado = r.json()["updated_at"]
    assert sellado >= int(t0 + lento) - 1, (
        f"sellada en {sellado} cuando el cálculo acabó sobre {int(t0 + lento)}: "
        "se está sellando con la hora de EMPEZAR, y así la caché nace caducada")
