"""El EV tracker no puede dejar mudo al backend mientras calcula.

POR QUÉ EXISTE ESTE FICHERO. `/gacha/ev` hace 4.000 remuestreos por máquina. Medido sobre
pokemon_50 con sus 16.000 tiradas de 48 h: 1.000 remuestreos cuestan 27 s, o sea ~108 s los 4.000,
de UNA máquina (`test_ev_stats.py` fija ese techo y hoy no se cumple). El backend corre en UN
proceso, así que calcular eso dentro del bucle de eventos deja a TODOS los demás sin backend
durante ese rato: ni saldo, ni máquinas, ni chat, ni liquidar una batalla en curso.

Ya pasó el 11/08 por otra vía —una ráfaga de peticiones de alias— y costó nueve minutos de web
muda. La diferencia es que aquello fue un accidente y esto vendría solo, cada vez que caducara la
caché de 60 s.

Lo que se prueba no es que el cálculo sea rápido (no lo es, y no pasa nada), sino que mientras
ocurre el bucle de eventos SIGUE VIVO.
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
from app.privy import PrivyVerifier
from tests.conftest import make_es256, privy_auth_headers
from tests.test_chain_mock import MockChainSource

# `/gacha/ev` y `/gacha/ev/live` dejaron de ser públicos al pasar el tracker a cobrarse: aquí se
# prueba que el bucle de eventos sigue vivo, no el acceso, así que la wallet de prueba entra por la
# lista blanca (la vía de la casa) y el cliente lleva su token.
WALLET = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"

TARDANZA = 0.4          # lo que "cuesta" el cálculo de una máquina en el test
LATIDO = 0.01           # cada cuánto comprueba el vigilante que el bucle responde


def _app(monkeypatch, lento):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)

    async def maquinas_falsas():
        return [{"code": "pokemon_50", "name": "Elite", "price": 50, "instantBuyback": 80}]

    priv = make_es256()
    app_id = "app-test"
    app = create_app(sf, MockChainSource(),
                     gacha=GachaService(base_url="https://dev-gacha.example.com", api_key=""),
                     solana_rpc_url="https://api.devnet.solana.com",
                     privy=PrivyVerifier(app_id=app_id, key_resolver=lambda kid: priv.public_key()),
                     tracker_access_allowlist={WALLET})
    monkeypatch.setattr(GachaService, "machines", lambda self: maquinas_falsas())
    monkeypatch.setattr(main_mod, "fila_ev", lento)
    return app, privy_auth_headers(priv, app_id, WALLET)


async def _latidos_mientras(app, ruta, headers):
    """Pide `ruta` y cuenta cuántas veces el bucle de eventos pudo despertarse mientras tanto."""
    latidos = 0

    async def vigilante():
        nonlocal latidos
        while True:
            await asyncio.sleep(LATIDO)
            latidos += 1

    tarea = asyncio.create_task(vigilante())
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://t") as c:
        r = await c.get(ruta, headers=headers, timeout=30)
    tarea.cancel()
    return r, latidos


@pytest.mark.asyncio
async def test_calcular_el_ev_no_deja_mudo_al_backend(monkeypatch):
    def fila_lenta(s, code, **kw):
        time.sleep(TARDANZA)          # BLOQUEANTE a propósito: es lo que hace el remuestreo real
        return {"machine": code, "realized_edge_pct": 1.0}

    app, headers = _app(monkeypatch, fila_lenta)
    r, latidos = await _latidos_mientras(app, "/gacha/ev", headers)

    assert r.status_code == 200
    # Sin hilo, el bucle se queda clavado los 0,4 s y no late ni una vez. Con hilo late ~40 veces;
    # se pide bastante menos para no atarlo a la velocidad de la máquina que corra los tests.
    assert latidos >= 10, (
        f"el bucle solo pudo despertarse {latidos} veces mientras se calculaba el EV: "
        "el cálculo está bloqueando a todo el mundo")


@pytest.mark.asyncio
async def test_el_carril_rapido_tampoco_bloquea(monkeypatch):
    """Es más barato, pero la página lo pide cada pocos segundos y son N consultas."""
    def fila_lenta(s, code, **kw):
        return {"machine": code}

    app, headers = _app(monkeypatch, fila_lenta)
    monkeypatch.setattr(main_mod, "rachas_por_tier",
                        lambda s, code: time.sleep(TARDANZA) or {})
    monkeypatch.setattr(main_mod.winners_store, "maquinas_con_datos", lambda s: ["pokemon_50"])

    r, latidos = await _latidos_mientras(app, "/gacha/ev/live", headers)
    assert r.status_code == 200
    assert latidos >= 10, f"el carril rápido bloqueó el bucle ({latidos} latidos)"
