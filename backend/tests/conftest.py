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


# ── The Machine Tracker pass purchase ────────────────────────────────────────
#
# These fixtures are used by `test_tracker_pass_api.py` (task 6) and task 7 will reuse them, so
# they live here and not inside one specific test file. Prefix `pase_` on ALL of them: this
# conftest is global, and without the prefix a future file that asks for `client` would silently
# receive this app with the money intercepted instead of its own.

TRACKER_PASS_APP_ID = "app-test"
TRACKER_PASS_WALLET_ID = "wid-1"
TRACKER_PASS_WALLET = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"


class _FirmanteFalso:
    """Signs nothing: on the mocked path, `construir_y_firmar_cobro` is intercepted and never
    reaches here."""
    enabled = True


class _FirmanteDeVerdad:
    """Signs the OPERATOR's slot FOR REAL, locally and with a throwaway key.

    WHY IT EXISTS. With `construir_y_firmar_cobro` mocked (as the WHOLE suite used to be), no
    test could see a real construction `ValueError`, and that is exactly the family of failures
    that has slipped through three rounds in a row (the blockhash, the destination wallet, the
    operator address, the mint). With this double, the build, sign, read the signature leg runs
    end to end and without mocks; the only thing intercepted is the send, which is what cannot be
    done in a test.

    We do not have the player's private key (Privy holds it), so their signature slot stays at
    zeros. It does not matter: the signature that IDENTIFIES a Solana transaction is the fee
    payer's, and the fee payer here is the operator (which is indeed ours). And none of this gets
    sent to any network.
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
    """A real transaction, really signed, in base64.

    It is what the `construir_y_firmar_cobro` double returns on the mocked path. Returning any
    old string ("FirmaFalsa") would NOT work: the endpoint passes this to `leer_firma`, which only
    accepts bytes that are a transaction with the fee payer's slot filled in. That way the
    `tx_signature` that ends up in the database is a real base58 signature, just like in
    production.
    """
    kp = Keypair()
    tx = Transaction.from_bytes(base64.b64decode(
        build_memo_tx(str(kp.pubkey()), "11111111111111111111111111111111")))
    tx.partial_sign([kp], tx.message.recent_blockhash)
    return base64.b64encode(bytes(tx)).decode()


def _crear_pase_entorno(monkeypatch, *, s7=10.0, s30=30.0, operator_address=None,
                        cc_usdc_mint="Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr",
                        fee_wallet_address="", construccion_real=False):
    """Builds the pass purchase app and intercepts its money.

    `s7`/`s30` are the 7 and 30 day prices in USDC; at 0.0 the pass stays OFF (see
    `pase_precio_apagado`). `operator_address` lets us simulate the operator wallet unconfigured
    or misspelled. `cc_usdc_mint` lets us simulate a misspelled mint. `fee_wallet_address` lets us
    separate the destination wallet (which the endpoint validates on its own) from the operator's
    (which it does not validate, and which therefore only blows up at BUILD time).

    `construccion_real=True` does NOT mock `construir_y_firmar_cobro`: it builds and signs for
    real, and only the send stays intercepted. It is the only way for a test to see the
    `ValueError`/`ParseHashError` that solders raises with a bad configuration.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)
    priv = make_es256()
    operator_kp = Keypair()
    if operator_address is None:
        # With real construction it has to be the key this double knows how to sign with;
        # otherwise, any valid base58 address will do.
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

    # The charge is split in two, and so are the doubles, because the LINE BETWEEN THE TWO IS THE
    # DESIGN: before `enviar` nothing has been broadcast and everything is retryable without a
    # trace; from `enviar` onward, an error no longer proves the transaction did not go through.
    #   · `firmar`: the signed transaction that `construir_y_firmar_cobro` would return, or an
    #     Exception (any kind: it is no longer classified by type here, it never leaves a row).
    #   · `enviar`: what the RPC would return, or an Exception (RuntimeError = explicit rejection;
    #     anything else = indeterminate).
    #   · `confirma`: True/False/None, mirroring what `confirmar_firma` really returns.
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
        # Counts that wallet's rows AT THE MOMENT of building and signing. It is this round's new
        # invariant, and the one that makes it impossible by construction for the family of
        # failures that used to lock wallets: if a row already existed here, a configuration
        # `ValueError` would leave it in place again and lock that wallet out forever.
        mando["wallet_del_cobro"] = a[2]
        with sf() as chk:
            mando["filas_al_firmar"] = len(chk.scalars(
                select(TrackerPass).where(TrackerPass.wallet == a[2])).all())
        if isinstance(mando["firmar"], Exception):
            raise mando["firmar"]
        return mando["firmar"]

    async def _enviar(*a, **k):
        # Snapshot of ACCESS (not of the `status` column) at the moment the money really moves.
        # `pase_vigente` is the same function `/gacha/tracker-access` uses, so this survives a
        # rename of `status` and catches any new state that grants access without being called
        # "active": it is the only thing that can catch "it activated before charging", because
        # the failure branches leave the row back in `failed`/`pending` when they finish, and
        # looking only at the FINAL state would never see that window.
        with sf() as chk:
            mando["acceso_al_cobrar"] = (
                tracker_pass.pase_vigente(chk, mando["wallet_del_cobro"]) is not None)
            # And this round's central promise: when the money moves, the row ALREADY exists and
            # ALREADY has its signature. Without this there would be no way to prove that no
            # `pending` row is left without a signature (the one case that needed human eyes)
            # other than by looking at the final state.
            en_curso = chk.scalars(select(TrackerPass).where(
                TrackerPass.wallet == mando["wallet_del_cobro"],
                TrackerPass.status == "pending")).first()
            mando["firma_en_la_fila_al_enviar"] = (
                en_curso.tx_signature if en_curso is not None else None)
        if isinstance(mando["enviar"], Exception):
            raise mando["enviar"]
        return mando["enviar"]

    async def _confirma(*a, **k):
        # Each call's kwargs are recorded so we can tell apart the reconciliation (which is given
        # a short budget) from the confirmation of the new charge (which really uses the
        # defaults, because a freshly sent one does need time to settle).
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
    c.doble_de_firmar = _firmar          # to retry after a real construction failure
    return c


@pytest.fixture()
def pase_entorno(monkeypatch):
    """App with the pass purchase ON (7 days = 10 USDC, 30 days = 30 USDC) and the money
    intercepted.

    `mando` controls the points where this can go wrong, and each fixture below moves only one
    of them. That way each test says exactly what fails.
    """
    return _crear_pase_entorno(monkeypatch)


@pytest.fixture()
def pase_precio_apagado(monkeypatch):
    """Same app as `pase_entorno`, but with both prices at 0.0: the pass OFF.
    `precio_base_units` treats 0 as a switch, not as "free" (app/services/tracker_pass.py), so
    with this the purchase does not exist: 503, not 200 at zero cost."""
    return _crear_pase_entorno(monkeypatch, s7=0.0, s30=0.0)


@pytest.fixture()
def pase_fee_dest_vacio(monkeypatch):
    """Same app as `pase_entorno`, but with no destination wallet configured for the charge:
    neither `fee_wallet_address` nor `privy_operator_address` has a value. With the new order
    this would no longer lock anyone out (there would be no row), but a 503 "misconfigured"
    still tells whoever deploys it where to look, instead of sending them to search the chain
    for a charge that was never attempted."""
    return _crear_pase_entorno(monkeypatch, operator_address="")


@pytest.fixture()
def pase_client(pase_entorno):
    return pase_entorno


@pytest.fixture()
def pase_cobro_ok(pase_entorno):
    return pase_entorno            # the default state is already the happy path


@pytest.fixture()
def pase_sin_saldo(pase_entorno):
    pase_entorno.mando["saldo"] = 1_000_000          # 1 USDC, and the pass costs 10
    return pase_entorno


@pytest.fixture()
def pase_saldo_reservado(pase_entorno):
    # Has 20 USDC but 15 are committed to a battle: 5 remain available.
    pase_entorno.mando["saldo"] = 20_000_000
    pase_entorno.mando["reservado"] = 15_000_000
    return pase_entorno


@pytest.fixture()
def pase_blockhash_revienta(pase_entorno):
    # Requesting the blockhash is the first thing that touches the network, and it happens
    # BEFORE any row exists: there is nothing to unlock and the player can retry right away.
    pase_entorno.mando["blockhash"] = httpx.ConnectError("the RPC did not answer")
    return pase_entorno


@pytest.fixture()
def pase_firma_rechazada(pase_entorno):
    # PrivySignerError: `sign_solana` failed. Also before any row exists.
    pase_entorno.mando["firmar"] = PrivySignerError("privy rpc unavailable")
    return pase_entorno


@pytest.fixture()
def pase_construccion_revienta(pase_entorno):
    # Any `ValueError` coming out of building: the family of failures that locked out wallets
    # three rounds in a row. With the new order it does not even need to be recognized by type.
    pase_entorno.mando["firmar"] = ValueError("String is the wrong size")
    return pase_entorno


@pytest.fixture()
def pase_envio_rechazado(pase_entorno):
    # RuntimeError FROM THE SEND: the RPC explicitly rejected `sendTransaction` (see
    # nft_transfer.py). The money did not move (a rejection from THAT node, not an absolute
    # network guarantee, but the best reading we have from what it answered).
    pase_entorno.mando["enviar"] = RuntimeError("sendTransaction failed")
    return pase_entorno


@pytest.fixture()
def pase_envio_indeterminado(pase_entorno):
    # Any other exception FROM THE SEND: a timeout, a 5xx from the proxy after retrying... We
    # do not know whether the transaction went through, because it already happened in the
    # `sendTransaction` POST.
    pase_entorno.mando["enviar"] = TimeoutError("the RPC did not answer")
    return pase_entorno


@pytest.fixture()
def pase_cobro_sin_confirmar(pase_entorno):
    # False: the chain executed it and REJECTED it (`err` present). Definitive rejection: it went
    # out and it fell, with a reason on record.
    pase_entorno.mando["confirma"] = False
    return pase_entorno


@pytest.fixture()
def pase_confirmacion_indeterminada(pase_entorno):
    # None: the retries ran out without ever seeing an `err` or a confirmation.
    pase_entorno.mando["confirma"] = None
    return pase_entorno


# ── REAL construction, unmocked: the failures no test could see ─────────────────────────────


@pytest.fixture()
def pase_construccion_real(monkeypatch):
    """Everything configured correctly, but building and signing FOR REAL. Only the send is
    intercepted."""
    return _crear_pase_entorno(monkeypatch, construccion_real=True)


@pytest.fixture()
def pase_mint_malo(monkeypatch):
    """The `cc_usdc_mint` misspelled, building for real: `Pubkey.from_string` raises
    `ValueError` INSIDE `build_token_transfer`, before signing or sending anything."""
    return _crear_pase_entorno(monkeypatch, construccion_real=True, cc_usdc_mint="no-es-un-mint")


@pytest.fixture()
def pase_operador_mal_escrito(monkeypatch):
    """The OPERATOR address with a typo (missing a character), building for real.

    The DESTINATION wallet is configured separately and correctly, so the configuration 503
    does not mask the case: what blows up here is the transaction's `fee_payer`, which the
    endpoint does not validate and which therefore only fails at build time (round 3's exact
    trap).
    """
    return _crear_pase_entorno(
        monkeypatch, construccion_real=True,
        fee_wallet_address="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
        operator_address="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncD")
