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
from app.services.gacha import GachaService
from tests.test_chain_mock import MockChainSource

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

    app = create_app(sf, MockChainSource(),
                     gacha=GachaService(base_url="https://dev-gacha.example.com", api_key=""),
                     solana_rpc_url="https://api.devnet.solana.com")
    monkeypatch.setattr(GachaService, "machines", lambda self: maquinas())
    monkeypatch.setattr(main_mod, "fila_ev", fila_lenta)
    return app


@pytest.mark.asyncio
async def test_diez_peticiones_a_la_vez_producen_un_solo_calculo(monkeypatch):
    contador = {"n": 0}
    app = _app(monkeypatch, contador)
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://t") as c:
        respuestas = await asyncio.gather(*[c.get("/gacha/ev", timeout=30) for _ in range(10)])

    assert all(r.status_code == 200 for r in respuestas)
    assert contador["n"] == 1, (
        f"{contador['n']} cálculos para 10 peticiones simultáneas: cada uno se lleva un hilo y "
        "una conexión, y así es como se agotó el pool en producción")


@pytest.mark.asyncio
async def test_todas_reciben_las_mismas_filas(monkeypatch):
    """Servir la copia anterior a quien llega tarde no puede significar servirle vacío."""
    contador = {"n": 0}
    app = _app(monkeypatch, contador)
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://t") as c:
        respuestas = await asyncio.gather(*[c.get("/gacha/ev", timeout=30) for _ in range(5)])

    for r in respuestas:
        assert r.json()["rows"], "una respuesta sin filas no le sirve a nadie"
