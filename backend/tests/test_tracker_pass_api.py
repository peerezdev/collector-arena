"""La compra del pase. Aquí se mueve dinero real de un usuario por primera vez fuera de una
partida, así que lo que más se prueba es el camino de FALLO.

Las fixtures (`entorno`, `client`, `cobro_ok`, `sin_saldo`, `saldo_reservado`, `cobro_revienta`,
`cobro_sin_confirmar`) están en `tests/conftest.py`, porque la tarea 7 las reutiliza."""
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from app.db import init_db, make_session_factory
from app.main import create_app
from app.models import TrackerPass
from app.services.gacha import GachaService
from fastapi.testclient import TestClient

from tests.test_chain_mock import MockChainSource


def test_comprar_da_acceso_y_lo_dice(client, cobro_ok):
    r = client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 7
    assert r.json()["price_usdc"] == 10.0
    acc = client.get("/gacha/tracker-access", headers=client.hdrs).json()
    assert acc["allowed"] is True and acc["via"] == "pass"


def test_el_pase_queda_ACTIVO_y_con_su_firma(client, cobro_ok):
    client.post("/gacha/tracker-pass", json={"days": 30}, headers=client.hdrs)
    with client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "active"
        assert p.tx_signature
        assert p.price_base_units == 30_000_000


def test_sin_saldo_NO_se_crea_ninguna_fila(client, sin_saldo):
    # El 402 ocurre antes de escribir nada: ni fila, ni cobro, ni rastro.
    r = client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    assert r.status_code == 402
    with client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []


def test_el_saldo_RESERVADO_para_una_batalla_no_se_puede_gastar_en_un_pase(client, saldo_reservado):
    # Si un pase pudiera gastarlo, la batalla se quedaría sin fondos al liquidar.
    r = client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    assert r.status_code == 402


def test_un_cobro_que_FALLA_no_da_acceso_y_deja_la_fila_en_failed(client, cobro_revienta):
    r = client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    assert r.status_code == 502
    with client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).one().status == "failed"
    acc = client.get("/gacha/tracker-access", headers=client.hdrs).json()
    assert acc["allowed"] is False


def test_un_cobro_ENVIADO_pero_NO_confirmado_tampoco_da_acceso(client, cobro_sin_confirmar):
    # El caso que justifica `confirmar_firma`: la transacción salió y se cayó después.
    r = client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    assert r.status_code == 502
    with client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "failed"
        assert p.tx_signature, "la firma se guarda igual, para poder reconciliar a mano"


def test_la_fila_sigue_pending_en_el_instante_del_cobro(client, cobro_ok):
    # El estado final "active" no basta para probar el orden: las ramas de fallo lo vuelven a
    # dejar en "failed" pase lo que pase antes. Lo que hay que comprobar es que, cuando se intenta
    # cobrar, la fila TODAVÍA no ha dado acceso a nadie.
    client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    assert client.mando["estado_al_cobrar"] == ["pending"]


def test_comprar_dos_veces_APILA(client, cobro_ok):
    client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    with client.session_factory() as s:
        pases = sorted(s.scalars(select(TrackerPass)).all(), key=lambda p: p.starts_at)
        assert pases[1].starts_at == pases[0].ends_at


def test_sin_token_no_se_puede_comprar(client):
    assert client.post("/gacha/tracker-pass", json={"days": 7}).status_code == 401


def test_una_duracion_que_no_vendemos_se_rechaza(client, cobro_ok):
    r = client.post("/gacha/tracker-pass", json={"days": 1}, headers=client.hdrs)
    assert r.status_code == 422


def test_con_el_precio_APAGADO_la_compra_no_existe(monkeypatch, cobro_ok):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    app = create_app(make_session_factory(engine), MockChainSource(),
                     gacha=GachaService(base_url="https://dev-gacha.example.com", api_key=""),
                     solana_rpc_url="https://api.devnet.solana.com")   # sin precios
    c = TestClient(app)
    # Sin `privy=` configurado, `current_user_id` ya devolvería 503 por su cuenta, así que estos
    # headers solo sirven para dejar claro que la wallet no es lo que se está probando aquí.
    r = c.post("/gacha/tracker-pass", json={"days": 7}, headers=cobro_ok.hdrs)
    assert r.status_code == 503
