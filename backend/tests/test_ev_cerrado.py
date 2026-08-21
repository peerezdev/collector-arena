"""El tracker deja de ser público en cuanto se cobra por él.

Hasta ahora `/gacha/ev` era abierto y el cliente ni le mandaba el token: la puerta escondía la
pantalla, no los datos. Un `curl` devolvía el tracker entero. Era defendible mientras fuese
gratis; deja de serlo en cuanto alguien paga.

Lo que se protege es lo único irreconstruible: el `getAllWinners` de CC tope en 200 tiradas por
máquina y no hay forma de mirar más atrás, así que la medición realizada solo la tiene quien
lleva escuchando el feed desde antes.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import BattlePlayer, PackBattle, TrackerPass
from app.services.gacha import GachaService
from tests.conftest import TRACKER_PASS_WALLET as WALLET

# Se reutiliza la app de la compra del pase (`pase_client`, tarea 6): ya trae Privy configurado,
# los precios del pase encendidos y `client.hdrs`/`client.session_factory` listos. Que además
# lleve el dinero intervenido no molesta aquí — estos tests no tocan ningún endpoint de cobro.


@pytest.fixture()
def client(pase_client, monkeypatch):
    """El mismo cliente del pase, pero sin depender de la red de Collector Crypt.

    `/gacha/ev` llama a `GachaService.machines()` DESPUÉS de comprobar el acceso. Sin este mock
    intentaría de verdad un HTTP a `dev-gacha.example.com` y los tests de acceso concedido
    dependerían de que esa llamada responda (o fallarían con un 502 que nada tiene que ver con
    lo que aquí se prueba: la puerta, no el catálogo).
    """
    async def _sin_maquinas(*a, **k):
        return []
    monkeypatch.setattr(GachaService, "machines", _sin_maquinas)
    return pase_client


@pytest.fixture()
def usuario_sin_acceso(client):
    return client                     # ni wager ni pase: la puerta cerrada


@pytest.fixture()
def usuario_con_wager(client):
    """100 USDC apostados dentro de la ventana. Se siembra una batalla liquidada."""
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


def test_sin_token_el_tracker_esta_CERRADO(client):
    r = client.get("/gacha/ev")
    assert r.status_code == 403
    assert r.json()["detail"] == "tracker_locked"


def test_el_carril_rapido_tambien(client):
    # Lleva las rachas, que es medición nuestra igual que el edge.
    assert client.get("/gacha/ev/live").status_code == 403


def test_con_token_pero_SIN_acceso_sigue_cerrado(client, usuario_sin_acceso):
    assert client.get("/gacha/ev", headers=client.hdrs).status_code == 403


def test_con_acceso_por_WAGER_se_abre(client, usuario_con_wager):
    assert client.get("/gacha/ev", headers=client.hdrs).status_code == 200


def test_con_acceso_por_PASE_se_abre(client, usuario_con_pase):
    assert client.get("/gacha/ev", headers=client.hdrs).status_code == 200


def test_un_pase_CADUCADO_vuelve_a_cerrar(client, usuario_con_pase_caducado):
    assert client.get("/gacha/ev", headers=client.hdrs).status_code == 403


def test_el_403_NO_es_un_502_disfrazado(client):
    """La pantalla distingue "no puedes" de "se ha roto algo", y tiene que poder seguir
    haciéndolo: un 403 enseña la puerta, un 502 enseña el aviso de fallo."""
    assert client.get("/gacha/ev").status_code == 403
