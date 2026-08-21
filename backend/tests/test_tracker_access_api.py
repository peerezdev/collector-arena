"""`GET /gacha/tracker-access` de punta a punta, y sobre todo: de dónde sale la wallet.

La lista blanca del tracker da acceso permanente a las cuentas de la casa, así que la pregunta
que hay que dejar contestada por escrito no es "¿entra el de la lista?" sino **"¿puede alguien
hacerse pasar por él?"**. La respuesta vive en que la wallet se saca del identity token de Privy,
verificado por firma, y nunca de nada que el cliente pueda escribir. Eso es lo que se prueba aquí.
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.chain.mock import MockChainSource
from app.db import init_db, make_session_factory
from app.main import create_app
from app.privy import PrivyVerifier

from tests.conftest import make_es256, make_id_token, privy_auth_headers, solana_embedded

APP_ID = "app123"
CASA = "So1anaCASA111111111111111111111111111111111"
CUALQUIERA = "So1anaOTRO222222222222222222222222222222222"


def _cliente(lista_blanca=None):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    priv = make_es256()
    privy = PrivyVerifier(app_id=APP_ID, key_resolver=lambda kid: priv.public_key())
    app = create_app(make_session_factory(engine), MockChainSource(), privy=privy,
                     tracker_access_allowlist=lista_blanca)
    return TestClient(app), priv


def test_la_wallet_de_la_casa_entra_sin_apostar():
    client, priv = _cliente({CASA})
    r = client.get("/gacha/tracker-access", headers=privy_auth_headers(priv, APP_ID, CASA))
    assert r.status_code == 200
    assert r.json()["allowed"] is True


def test_a_los_demas_la_puerta_les_sigue_pidiendo_los_100():
    client, priv = _cliente({CASA})
    r = client.get("/gacha/tracker-access", headers=privy_auth_headers(priv, APP_ID, CUALQUIERA))
    assert r.json() == {"allowed": False, "via": None, "pass_until": None,
                        "wagered_usd": 0.0, "required_usd": 100.0,
                        "missing_usd": 100.0, "window_days": 7, "pass_prices": {}}


def test_DECIR_que_eres_la_casa_no_sirve_de_nada():
    """La suplantación por la vía barata: mandar la wallet a mano.

    El endpoint no lee ningún parámetro de wallet, así que estas peticiones no se distinguen de
    una anónima. Si algún día alguien añadiera un `?wallet=`, este test se pondría rojo.
    """
    client, _ = _cliente({CASA})
    for peticion in (f"/gacha/tracker-access?wallet={CASA}",
                     f"/gacha/tracker-access?address={CASA}"):
        assert client.get(peticion).json()["allowed"] is False
    assert client.get("/gacha/tracker-access", headers={"X-Wallet": CASA}).json()["allowed"] is False


def test_un_token_firmado_por_OTRO_no_abre_la_puerta():
    """La suplantación de verdad: un token con la wallet de la casa dentro, pero firmado por quien
    no tiene la clave de Privy. Es lo que podría fabricarse cualquiera por su cuenta."""
    client, _ = _cliente({CASA})
    otra_clave = make_es256()
    falso = make_id_token(otra_clave, APP_ID, [solana_embedded(CASA)])
    r = client.get("/gacha/tracker-access", headers={"Authorization": f"Bearer {falso}"})
    assert r.status_code == 200          # se trata como "sin sesión", no como un error
    assert r.json()["allowed"] is False


def test_un_token_de_otra_aplicacion_con_la_wallet_de_la_casa_tampoco():
    """Mismo ataque, un paso más fino: token bien firmado pero emitido para OTRA app de Privy.

    Sin comprobar la audiencia, un token de cualquier otro producto que use Privy valdría aquí.
    """
    client, priv = _cliente({CASA})
    ajeno = make_id_token(priv, "otra-app", [solana_embedded(CASA)])
    r = client.get("/gacha/tracker-access", headers={"Authorization": f"Bearer {ajeno}"})
    assert r.json()["allowed"] is False


def test_un_token_caducado_de_la_casa_deja_de_abrir():
    """El acceso permanente lo es para la wallet, no para un token concreto: si caduca, se entra
    otra vez. Si no, una sesión robada valdría para siempre."""
    client, priv = _cliente({CASA})
    viejo = make_id_token(priv, APP_ID, [solana_embedded(CASA)], exp_delta=-3600)
    r = client.get("/gacha/tracker-access", headers={"Authorization": f"Bearer {viejo}"})
    assert r.json()["allowed"] is False


def test_sin_lista_la_casa_es_uno_mas():
    """Comprobación de que el acceso lo da la LISTA y no el hecho de ser esa wallet."""
    client, priv = _cliente(None)
    r = client.get("/gacha/tracker-access", headers=privy_auth_headers(priv, APP_ID, CASA))
    assert r.json()["allowed"] is False
