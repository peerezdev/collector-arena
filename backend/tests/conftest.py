import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from app.chain.mock import MockChainSource
from app.db import make_engine, make_session_factory, init_db
from app.main import create_app
from app.privy import PrivyVerifier
from app.services import tracker_pass
from app.services.gacha import GachaService


@pytest.fixture
def Session():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    return make_session_factory(engine)


# ── Helpers compartidos de Privy para tests de API ───────────────────────────

def make_es256():
    """Genera una clave privada EC/P-256 para tests."""
    return ec.generate_private_key(ec.SECP256R1())


def make_id_token(priv, app_id, linked_accounts, sub="did:privy:abc", exp_delta=3600):
    """Construye un identity token de Privy firmado con `priv`."""
    now = int(time.time())
    payload = {
        "aud": app_id,
        "iss": "privy.io",
        "sub": sub,
        "iat": now,
        "exp": now + exp_delta,
        "linked_accounts": json.dumps(linked_accounts),
    }
    return jwt.encode(payload, priv, algorithm="ES256", headers={"kid": "test-kid", "alg": "ES256"})


def solana_embedded(addr):
    """Devuelve un linked_account de embedded Solana wallet con la forma REAL del
    identity token de Privy: connector_type viene None y la embedded se identifica
    por wallet_client_type == "privy"."""
    return {"type": "wallet", "chain_type": "solana", "connector_type": None,
            "wallet_client_type": "privy", "address": addr}


def privy_auth_headers(priv, app_id, wallet_addr):
    """Devuelve un dict de headers Authorization para autenticar como `wallet_addr`."""
    token = make_id_token(priv, app_id, [solana_embedded(wallet_addr)])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── El escrow de mentira ──────────────────────────────────────────────────────

def escrow_que_entrega():
    """`confirm_in_escrow` de un escrow que SÍ entrega: dice que la carta está DENTRO la primera
    vez que se pregunta por ella —justo antes de moverla— y que ya NO está a partir de la segunda,
    que es cuando se comprueba si el traspaso surtió efecto.

    Existe porque el doble anterior, `async def confirm_in_escrow(esc, nft): return True`, describe
    un escrow imposible: la carta entregada y dentro a la vez. Con él en verde pasó a producción el
    fallo del 11/08 — el settle daba la carta por entregada en cuanto el RPC ACEPTABA la
    transacción, y una que se envió y nunca aterrizó dejó un Charizard de 93 $ dentro del escrow
    con `transferred=1`. Ese flag falso además cegaba a `sweep_stranded_cards`, que busca justo por
    `transferred == 0`.

    Para el camino contrario —la carta que no se mueve— vale el doble de siempre: uno que devuelve
    True para todo hace fallar la comprobación de salida, que es exactamente lo que se quiere.
    """
    preguntadas: set = set()

    async def confirmar(esc, nft):
        if nft in preguntadas:
            return False          # ya se movió entre una pregunta y la otra
        preguntadas.add(nft)
        return True

    return confirmar


# ── La compra del pase del Machine Tracker ────────────────────────────────────
#
# Estas fixtures las usa `test_tracker_pass_api.py` (tarea 6) y las reutilizará la tarea 7, así
# que viven aquí y no dentro de un fichero de test concreto. Prefijo `pase_` en TODAS: este
# conftest es global, y sin prefijo un fichero futuro que pida `client` recibiría en silencio
# esta app con el dinero intervenido en vez de la suya.

TRACKER_PASS_APP_ID = "app-test"
TRACKER_PASS_WALLET_ID = "wid-1"
TRACKER_PASS_WALLET = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"


class _FirmanteFalso:
    """No firma nada: el cobro se intercepta antes de llegar aquí."""
    enabled = True


def _crear_pase_entorno(monkeypatch, *, s7=10.0, s30=30.0):
    """Construye la app de la compra del pase y le interviene el dinero. `s7`/`s30` son los
    precios de 7 y 30 días en USDC; a 0.0 el pase queda APAGADO (ver `pase_precio_apagado`), que
    es la única razón de que esto sea una función y no quede todo dentro de `pase_entorno`.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)
    priv = make_es256()
    app = create_app(sf, MockChainSource(),
                     gacha=GachaService(base_url="https://dev-gacha.example.com", api_key=""),
                     privy=PrivyVerifier(app_id=TRACKER_PASS_APP_ID,
                                         key_resolver=lambda kid: priv.public_key()),
                     privy_signer=_FirmanteFalso(),
                     privy_operator_wallet_id="op-id", privy_operator_address="OpAddr",
                     solana_rpc_url="https://rpc.test",
                     tracker_pass_7d_usdc=s7, tracker_pass_30d_usdc=s30)

    # `cobro` y `confirma` son tri-estado desde que el endpoint distingue "rechazo definitivo" de
    # "no lo sé": `cobro` guarda un valor de vuelta o una Exception (RuntimeError = rechazo,
    # cualquier otra = indeterminado); `confirma` guarda True/False/None (confirmada / rechazada
    # en cadena / indeterminada), calcando lo que devuelve `confirmar_firma` de verdad.
    mando = {"saldo": 1_000_000_000, "reservado": 0, "cobro": "FirmaFalsa", "confirma": True,
             "acceso_al_cobrar": None}

    async def _saldo(*a, **k):
        return mando["saldo"]

    def _reservado(*a, **k):
        return mando["reservado"]

    async def _blockhash(*a, **k):
        return "11111111111111111111111111111111"

    async def _cobro(*a, **k):
        # Fotografía del ACCESO —no de la columna `status`— en el instante en que se intenta el
        # cobro. `pase_vigente` es la misma función que usa `/gacha/tracker-access` para decidir
        # si alguien entra, así que esto sobrevive a un renombrado de `status` y caza además
        # cualquier estado nuevo que diera acceso sin que se llame "active": lo único que puede
        # cazar "se activó antes de cobrar", porque las ramas de fallo del endpoint vuelven a
        # dejar la fila en `failed` (o la dejan en `pending`, desde que existe lo indeterminado)
        # al terminar, así que mirar solo el estado FINAL nunca vería esa ventana.
        wallet_de_la_compra = a[3]
        with sf() as chk:
            mando["acceso_al_cobrar"] = tracker_pass.pase_vigente(chk, wallet_de_la_compra) is not None
        if isinstance(mando["cobro"], Exception):
            raise mando["cobro"]
        return mando["cobro"]

    async def _confirma(*a, **k):
        return mando["confirma"]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _saldo)
    monkeypatch.setattr("app.main.reserved_total", _reservado)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _blockhash)
    monkeypatch.setattr("app.main.collect_buyin", _cobro)
    monkeypatch.setattr("app.main.confirmar_firma", _confirma)

    cuenta = {"type": "wallet", "chain_type": "solana", "connector_type": None,
              "wallet_client_type": "privy", "address": TRACKER_PASS_WALLET,
              "id": TRACKER_PASS_WALLET_ID}
    c = TestClient(app, raise_server_exceptions=True)
    c.session_factory = sf
    c.hdrs = {"Authorization": f"Bearer {make_id_token(priv, TRACKER_PASS_APP_ID, [cuenta])}"}
    c.mando = mando
    return c


@pytest.fixture()
def pase_entorno(monkeypatch):
    """App con la compra del pase ENCENDIDA (7 días = 10 USDC, 30 días = 30 USDC) y el dinero
    intervenido.

    `mando` controla los puntos donde esto puede salir mal, y cada fixture de abajo mueve uno
    solo. Así cada test dice exactamente qué falla.
    """
    return _crear_pase_entorno(monkeypatch)


@pytest.fixture()
def pase_precio_apagado(monkeypatch):
    """Misma app que `pase_entorno`, pero con los dos precios a 0.0: el pase APAGADO.
    `precio_base_units` trata 0 como interruptor, no como "gratis" (app/services/tracker_pass.py),
    así que con esto la compra no existe — 503, no 200 a coste cero."""
    return _crear_pase_entorno(monkeypatch, s7=0.0, s30=0.0)


@pytest.fixture()
def pase_client(pase_entorno):
    return pase_entorno


@pytest.fixture()
def pase_cobro_ok(pase_entorno):
    return pase_entorno            # el estado por defecto ya es el camino bueno


@pytest.fixture()
def pase_sin_saldo(pase_entorno):
    pase_entorno.mando["saldo"] = 1_000_000          # 1 USDC, y el pase cuesta 10
    return pase_entorno


@pytest.fixture()
def pase_saldo_reservado(pase_entorno):
    # Tiene 20 USDC pero 15 están comprometidos en una batalla: disponibles quedan 5.
    pase_entorno.mando["saldo"] = 20_000_000
    pase_entorno.mando["reservado"] = 15_000_000
    return pase_entorno


@pytest.fixture()
def pase_cobro_revienta(pase_entorno):
    # RuntimeError: el rechazo DEFINITIVO de `submit_signed_tx` (ver nft_transfer.py). El dinero
    # no se movió, con certeza.
    pase_entorno.mando["cobro"] = RuntimeError("sendTransaction failed")
    return pase_entorno


@pytest.fixture()
def pase_cobro_indeterminado(pase_entorno):
    # Cualquier excepción que NO sea RuntimeError: un timeout, un 5xx del proxy tras reenviar...
    # No sabemos si la transacción llegó a salir de verdad.
    pase_entorno.mando["cobro"] = TimeoutError("el RPC no respondió")
    return pase_entorno


@pytest.fixture()
def pase_cobro_sin_confirmar(pase_entorno):
    # False: la cadena la ejecutó y la RECHAZÓ (`err` presente). Rechazo definitivo, salió y cayó
    # con un motivo que consta.
    pase_entorno.mando["confirma"] = False
    return pase_entorno


@pytest.fixture()
def pase_confirmacion_indeterminada(pase_entorno):
    # None: se agotaron los intentos sin ver ni un `err` ni una confirmación.
    pase_entorno.mando["confirma"] = None
    return pase_entorno
