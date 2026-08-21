import base64
import json
import time

import httpx
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
from app.services.privy_signer import PrivySignerError
from app.services.solana_tx import build_memo_tx
from app.models import TrackerPass
from solders.keypair import Keypair
from solders.transaction import Transaction
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
    """No firma nada: en el camino mockeado, `construir_y_firmar_cobro` está interceptado y nunca
    llega aquí."""
    enabled = True


class _FirmanteDeVerdad:
    """Firma DE VERDAD, en local y con una clave de usar y tirar, la ranura del OPERADOR.

    POR QUÉ EXISTE. Con `construir_y_firmar_cobro` mockeado —como estaba TODA la suite— ningún
    test puede ver un `ValueError` real de construcción, y esa es exactamente la familia de fallos
    que se ha colado tres rondas seguidas (el blockhash, la wallet de destino, la dirección del
    operador, el mint). Con este doble, el tramo construir → firmar → leer la firma corre entero
    y sin mocks; lo único intervenido es el envío, que es lo que no se puede hacer en un test.

    Del jugador no tenemos la clave privada (la guarda Privy), así que su ranura de firma se queda
    a ceros. Da igual: la firma que IDENTIFICA una transacción de Solana es la del fee payer, y el
    fee payer aquí es el operador — que sí es nuestro. Y nada de esto se envía a ninguna red.
    """
    enabled = True

    def __init__(self, operator_wallet_id, operator_keypair):
        self._wid = operator_wallet_id
        self._kp = operator_keypair

    async def sign_solana(self, wallet_id, tx_b64):
        tx = Transaction.from_bytes(base64.b64decode(tx_b64))
        if wallet_id == self._wid:
            tx.partial_sign([self._kp], tx.message.recent_blockhash)
        return base64.b64encode(bytes(tx)).decode()


def _tx_firmada_de_verdad() -> str:
    """Una transacción de verdad, firmada de verdad, en base64.

    Es lo que devuelve el doble de `construir_y_firmar_cobro` en el camino mockeado. Devolver una
    cadena cualquiera ("FirmaFalsa") NO valdría: el endpoint le pasa esto a `leer_firma`, que solo
    acepta bytes que sean una transacción con la ranura del fee payer rellena. Así el `tx_signature`
    que acaba en la base es una firma base58 real, como en producción.
    """
    kp = Keypair()
    tx = Transaction.from_bytes(base64.b64decode(
        build_memo_tx(str(kp.pubkey()), "11111111111111111111111111111111")))
    tx.partial_sign([kp], tx.message.recent_blockhash)
    return base64.b64encode(bytes(tx)).decode()


def _crear_pase_entorno(monkeypatch, *, s7=10.0, s30=30.0, operator_address=None,
                        cc_usdc_mint="Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr",
                        fee_wallet_address="", construccion_real=False):
    """Construye la app de la compra del pase y le interviene el dinero.

    `s7`/`s30` son los precios de 7 y 30 días en USDC; a 0.0 el pase queda APAGADO (ver
    `pase_precio_apagado`). `operator_address` deja simular la wallet del operador sin configurar
    o mal escrita. `cc_usdc_mint` deja simular un mint mal escrito. `fee_wallet_address` deja
    separar la wallet de destino (que el endpoint valida por su cuenta) de la del operador (que
    no valida, y que por tanto solo revienta al CONSTRUIR).

    `construccion_real=True` NO mockea `construir_y_firmar_cobro`: se construye y se firma de
    verdad, y solo el envío queda intervenido. Es la única forma de que un test vea los
    `ValueError`/`ParseHashError` que lanza solders con la configuración mal puesta.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)
    priv = make_es256()
    operator_kp = Keypair()
    if operator_address is None:
        # Con construcción real tiene que ser la clave que este doble sabe firmar; si no, cualquier
        # dirección base58 válida sirve.
        operator_address = (str(operator_kp.pubkey()) if construccion_real
                            else "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
    firmante = (_FirmanteDeVerdad("op-id", operator_kp) if construccion_real else _FirmanteFalso())
    app = create_app(sf, MockChainSource(),
                     gacha=GachaService(base_url="https://dev-gacha.example.com", api_key=""),
                     privy=PrivyVerifier(app_id=TRACKER_PASS_APP_ID,
                                         key_resolver=lambda kid: priv.public_key()),
                     privy_signer=firmante,
                     privy_operator_wallet_id="op-id",
                     privy_operator_address=operator_address,
                     fee_wallet_address=fee_wallet_address,
                     cc_usdc_mint=cc_usdc_mint,
                     solana_rpc_url="https://rpc.test",
                     tracker_pass_7d_usdc=s7, tracker_pass_30d_usdc=s30)

    # El cobro está partido en dos, y los dobles también, porque la LÍNEA ENTRE LOS DOS ES EL
    # DISEÑO: antes de `enviar` nada se ha difundido y todo es reintentable sin rastro; a partir
    # de `enviar`, un error ya no prueba que la transacción no haya salido.
    #   · `firmar`: la transacción firmada que devolvería `construir_y_firmar_cobro`, o una
    #     Exception (cualquiera: aquí ya no se clasifica por tipo, nunca deja fila).
    #   · `enviar`: lo que devolvería el RPC, o una Exception (RuntimeError = rechazo explícito;
    #     cualquier otra = indeterminado).
    #   · `confirma`: True/False/None, calcando lo que devuelve `confirmar_firma` de verdad.
    mando = {"saldo": 1_000_000_000, "reservado": 0,
             "firmar": _tx_firmada_de_verdad(), "enviar": "FirmaQueDevuelveElRPC",
             "confirma": True, "blockhash": "11111111111111111111111111111111",
             "wallet_del_cobro": TRACKER_PASS_WALLET,
             "filas_al_firmar": None, "acceso_al_cobrar": None,
             "firma_en_la_fila_al_enviar": None}

    async def _saldo(*a, **k):
        return mando["saldo"]

    def _reservado(*a, **k):
        return mando["reservado"]

    async def _blockhash(*a, **k):
        if isinstance(mando["blockhash"], Exception):
            raise mando["blockhash"]
        return mando["blockhash"]

    async def _firmar(*a, **k):
        # Cuenta las filas de esa wallet EN EL INSTANTE de construir y firmar. Es la invariante
        # nueva de esta ronda y la que hace imposible por construcción la familia de fallos que
        # encerraba wallets: si aquí ya hubiera una fila, un `ValueError` de configuración
        # volvería a dejarla puesta y a bloquear a esa wallet para siempre.
        mando["wallet_del_cobro"] = a[2]
        with sf() as chk:
            mando["filas_al_firmar"] = len(chk.scalars(
                select(TrackerPass).where(TrackerPass.wallet == a[2])).all())
        if isinstance(mando["firmar"], Exception):
            raise mando["firmar"]
        return mando["firmar"]

    async def _enviar(*a, **k):
        # Fotografía del ACCESO —no de la columna `status`— en el instante en que el dinero se
        # mueve de verdad. `pase_vigente` es la misma función que usa `/gacha/tracker-access`, así
        # que esto sobrevive a un renombrado de `status` y caza cualquier estado nuevo que diera
        # acceso sin llamarse "active": es lo único que puede cazar "se activó antes de cobrar",
        # porque las ramas de fallo vuelven a dejar la fila en `failed`/`pending` al terminar y
        # mirar solo el estado FINAL nunca vería esa ventana.
        with sf() as chk:
            mando["acceso_al_cobrar"] = (
                tracker_pass.pase_vigente(chk, mando["wallet_del_cobro"]) is not None)
            # Y la promesa central de esta ronda: cuando el dinero se mueve, la fila YA existe y
            # YA tiene su firma. Sin esto no habría forma de probar que no queda ninguna `pending`
            # sin firma —la única que necesitaba ojos humanos— salvo mirando el estado final.
            en_curso = chk.scalars(select(TrackerPass).where(
                TrackerPass.wallet == mando["wallet_del_cobro"],
                TrackerPass.status == "pending")).first()
            mando["firma_en_la_fila_al_enviar"] = (
                en_curso.tx_signature if en_curso is not None else None)
        if isinstance(mando["enviar"], Exception):
            raise mando["enviar"]
        return mando["enviar"]

    async def _confirma(*a, **k):
        # Se registran los kwargs de cada llamada para poder distinguir la reconciliación —que le
        # pasa un presupuesto corto— de la confirmación del cobro nuevo —que usa los valores por
        # defecto de verdad, porque a una recién enviada sí hay que darle tiempo de asentarse.
        mando.setdefault("confirma_llamadas", []).append(dict(k))
        return mando["confirma"]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _saldo)
    monkeypatch.setattr("app.main.reserved_total", _reservado)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _blockhash)
    if not construccion_real:
        monkeypatch.setattr("app.main.construir_y_firmar_cobro", _firmar)
    monkeypatch.setattr("app.main.enviar_cobro", _enviar)
    monkeypatch.setattr("app.main.confirmar_firma", _confirma)

    cuenta = {"type": "wallet", "chain_type": "solana", "connector_type": None,
              "wallet_client_type": "privy", "address": TRACKER_PASS_WALLET,
              "id": TRACKER_PASS_WALLET_ID}
    c = TestClient(app, raise_server_exceptions=True)
    c.session_factory = sf
    c.hdrs = {"Authorization": f"Bearer {make_id_token(priv, TRACKER_PASS_APP_ID, [cuenta])}"}
    c.mando = mando
    c.doble_de_firmar = _firmar          # para reintentar tras un fallo de construcción real
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
def pase_fee_dest_vacio(monkeypatch):
    """Misma app que `pase_entorno`, pero sin wallet de destino del cobro configurada: ni
    `fee_wallet_address` ni `privy_operator_address` tienen valor. Con el orden nuevo esto ya no
    encerraría a nadie (no habría fila), pero un 503 "misconfigured" sigue diciéndole a quien
    despliega dónde mirar, en vez de mandarlo a buscar en la cadena un cobro que no se intentó."""
    return _crear_pase_entorno(monkeypatch, operator_address="")


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
def pase_blockhash_revienta(pase_entorno):
    # Pedir el blockhash es lo primero que toca la red, y pasa ANTES de que exista fila: no hay
    # nada que desbloquear y el jugador puede reintentar al momento.
    pase_entorno.mando["blockhash"] = httpx.ConnectError("el RPC no respondió")
    return pase_entorno


@pytest.fixture()
def pase_firma_rechazada(pase_entorno):
    # PrivySignerError: `sign_solana` falló. También antes de que exista fila.
    pase_entorno.mando["firmar"] = PrivySignerError("privy rpc unavailable")
    return pase_entorno


@pytest.fixture()
def pase_construccion_revienta(pase_entorno):
    # Un `ValueError` cualquiera saliendo de construir: la familia de fallos que encerró wallets
    # tres rondas seguidas. Con el orden nuevo ni siquiera hace falta reconocerla por su tipo.
    pase_entorno.mando["firmar"] = ValueError("String is the wrong size")
    return pase_entorno


@pytest.fixture()
def pase_envio_rechazado(pase_entorno):
    # RuntimeError DESDE EL ENVÍO: el RPC rechazó `sendTransaction` de forma explícita (ver
    # nft_transfer.py). El dinero no se movió — un rechazo de ESE nodo, no una garantía absoluta
    # de red, pero la mejor lectura que tenemos con lo que contestó.
    pase_entorno.mando["enviar"] = RuntimeError("sendTransaction failed")
    return pase_entorno


@pytest.fixture()
def pase_envio_indeterminado(pase_entorno):
    # Cualquier otra excepción DESDE EL ENVÍO: un timeout, un 5xx del proxy tras reenviar... No
    # sabemos si la transacción salió, porque pasó ya en el POST de `sendTransaction`.
    pase_entorno.mando["enviar"] = TimeoutError("el RPC no respondió")
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


# ── Construcción REAL, sin mockear: los fallos que ningún test podía ver ─────────────────────


@pytest.fixture()
def pase_construccion_real(monkeypatch):
    """Todo bien configurado, pero construyendo y firmando DE VERDAD. Solo el envío está
    intervenido."""
    return _crear_pase_entorno(monkeypatch, construccion_real=True)


@pytest.fixture()
def pase_mint_malo(monkeypatch):
    """El `cc_usdc_mint` mal escrito, construyendo de verdad: `Pubkey.from_string` levanta
    `ValueError` DENTRO de `build_token_transfer`, antes de firmar y de enviar nada."""
    return _crear_pase_entorno(monkeypatch, construccion_real=True, cc_usdc_mint="no-es-un-mint")


@pytest.fixture()
def pase_operador_mal_escrito(monkeypatch):
    """La dirección del OPERADOR con un typo (le falta un carácter), construyendo de verdad.

    La wallet de DESTINO se configura aparte y bien, para que el 503 de configuración no tape el
    caso: lo que revienta aquí es el `fee_payer` de la transacción, que el endpoint no valida y
    que por tanto solo falla al construir — la trampa exacta de la ronda 3.
    """
    return _crear_pase_entorno(
        monkeypatch, construccion_real=True,
        fee_wallet_address="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
        operator_address="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncD")
