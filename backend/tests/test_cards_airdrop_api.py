import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.db import init_db, make_session_factory
from app.main import create_app
from tests.test_chain_mock import MockChainSource

APP_ID = "testapp"
WALLET = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"
WALLET_ID = "wallet-id-aaa"
AJENA = "FzRt4Pnh6tBpavXqkwQH1WVByeotDSefyyACKXC5kGHZ"
DISTRIBUTOR = "H6k7zSjCn2w5Q4em3b3E7iaPQfLrxVsF6u1bK6kD1Bhq"
VAULT = "5TBR7KQHbPsf3wHZ11dyL9iifztCnN9Ccr6rzoCvYqW7"
MINT = "CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp"
OPERADOR = "3q6Ucr1s7Knkp5nRQKQe3dYPzoh72XQGnn2oCgSS9S34"
PROOF = ["9Ad5fSi8QPvs8CimVj8vFwSXJkkZ5fgvnhmefmr6QEKv"]

ASIGNACIONES = {WALLET: {"i": 1687, "a": 1_483_000_000, "p": PROOF}}


def _headers(priv, addr=WALLET, wallet_id=WALLET_ID):
    now = int(time.time())
    cuenta = {"type": "wallet", "chain_type": "solana", "connector_type": None,
              "wallet_client_type": "privy", "address": addr, "id": wallet_id}
    payload = {"aud": APP_ID, "iss": "privy.io", "sub": f"did:privy:{addr[:8]}",
               "iat": now, "exp": now + 3600, "linked_accounts": json.dumps([cuenta])}
    tok = jwt.encode(payload, priv, algorithm="ES256", headers={"kid": "test-kid", "alg": "ES256"})
    return {"Authorization": f"Bearer {tok}"}


class FakeSigner:
    """Firma sin red y recuerda con qué wallet_id se le pidió cada firma."""
    def __init__(self):
        self.firmas: list[tuple[str, str]] = []
        self.enabled = True

    async def sign_solana(self, wallet_id: str, tx: str) -> str:
        self.firmas.append((wallet_id, tx))
        return f"signed::{tx}"

    async def podemos_firmar(self, wallet_id: str) -> bool:
        return True


class FakePrivy:
    def __init__(self, priv):
        self._pub = priv.public_key()

    def embedded_solana_wallet(self, token: str) -> str:
        import jwt as _jwt
        d = _jwt.decode(token, self._pub, algorithms=["ES256"], audience=APP_ID)
        return json.loads(d["linked_accounts"])[0]["address"]

    def embedded_solana_wallet_id(self, token: str) -> str:
        import jwt as _jwt
        d = _jwt.decode(token, self._pub, algorithms=["ES256"], audience=APP_ID)
        return json.loads(d["linked_accounts"])[0]["id"]


def _cliente(**over):
    priv = ec.generate_private_key(ec.SECP256R1())
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)
    kwargs = dict(
        privy=FakePrivy(priv), privy_signer=FakeSigner(),
        privy_operator_wallet_id="op-wallet-id", privy_operator_address=OPERADOR,
        cards_airdrop=dict(ASIGNACIONES),
        cards_airdrop_distributor=DISTRIBUTOR, cards_airdrop_vault=VAULT,
        cards_airdrop_mint=MINT, cards_airdrop_round="2026-09",
        solana_rpc_url="https://api.devnet.solana.com",
    )
    kwargs.update(over)
    app = create_app(sf, MockChainSource(), **kwargs)
    return TestClient(app), priv, kwargs


@pytest.fixture
def sin_pda(monkeypatch):
    """Por defecto, la cuenta de ClaimStatus no existe: nadie ha reclamado."""
    async def _cuenta(rpc_url, pubkey, **kw):
        return None
    monkeypatch.setattr("app.main._airdrop_cuenta", _cuenta)
    return _cuenta


def test_elegible_sin_reclamar(sin_pda):
    c, priv, _ = _cliente()
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.status_code == 200
    assert r.json() == {"eligible": True, "amount": 1_483_000_000,
                        "claimed": False, "signature": None}


def test_no_elegible(sin_pda):
    c, priv, _ = _cliente()
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv, addr=AJENA, wallet_id="otro"))
    assert r.status_code == 200
    assert r.json()["eligible"] is False


def test_ya_reclamado_lo_dice_la_cadena(monkeypatch):
    async def _cuenta(rpc_url, pubkey, **kw):
        return {"lamports": 1}
    monkeypatch.setattr("app.main._airdrop_cuenta", _cuenta)
    c, priv, _ = _cliente()
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.json()["claimed"] is True


def test_sin_fichero_es_503_y_no_no_elegible(sin_pda):
    # La distinción importa: decirle "no eres elegible" a alguien que sí lo es por una
    # avería nuestra es el peor fallo posible aquí.
    c, priv, _ = _cliente(cards_airdrop={})
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.status_code == 503


def test_sin_operador_es_503(sin_pda):
    c, priv, _ = _cliente(privy_operator_wallet_id="", privy_operator_address="")
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.status_code == 503


def test_sin_ronda_es_503(sin_pda):
    # Las filas de AirdropClaim se escriben y se leen con la ronda como parte de la clave:
    # una ronda vacía es una configuración a medias, igual que si faltara el fichero.
    c, priv, _ = _cliente(cards_airdrop_round="")
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.status_code == 503


def test_si_el_rpc_falla_es_502_y_no_no_elegible(monkeypatch):
    async def _cuenta(rpc_url, pubkey, **kw):
        raise RuntimeError("rpc caído")
    monkeypatch.setattr("app.main._airdrop_cuenta", _cuenta)
    c, priv, _ = _cliente()
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.status_code == 502


@pytest.fixture
def cadena_falsa(monkeypatch):
    """Sin red: blockhash fijo, la ATA no existe, y el submit devuelve una firma."""
    async def _bh(rpc_url):
        return "11111111111111111111111111111111"
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)

    enviadas = []

    async def _submit(rpc_url, tx_b64):
        enviadas.append(tx_b64)
        return "firma-de-mentira-1"
    monkeypatch.setattr("app.main.submit_signed_tx", _submit)
    return enviadas


def test_el_claim_firma_primero_el_jugador_y_luego_el_operador(sin_pda, cadena_falsa):
    c, priv, kw = _cliente()
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 200
    assert r.json() == {"signature": "firma-de-mentira-1", "amount": 1_483_000_000}
    # El orden importa: el dueño autoriza y el operador paga, nunca al revés.
    firmantes = [w for w, _ in kw["privy_signer"].firmas]
    assert firmantes == [WALLET_ID, "op-wallet-id"]


def test_el_claim_deja_constancia_en_la_tabla(sin_pda, cadena_falsa):
    from app.models import AirdropClaim
    c, priv, _ = _cliente()
    c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.json()["signature"] == "firma-de-mentira-1"


def test_reclamar_dos_veces_da_409(cadena_falsa, monkeypatch):
    async def _cuenta(rpc_url, pubkey, **kw):
        return {"lamports": 1}
    monkeypatch.setattr("app.main._airdrop_cuenta", _cuenta)
    c, priv, _ = _cliente()
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 409


def test_un_no_elegible_no_puede_reclamar(sin_pda, cadena_falsa):
    c, priv, _ = _cliente()
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv, addr=AJENA, wallet_id="otro"))
    assert r.status_code == 403


def test_sin_operador_no_se_reclama(sin_pda, cadena_falsa):
    c, priv, _ = _cliente(privy_operator_wallet_id="", privy_operator_address="")
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 503


def test_sin_delegar_es_409_con_instrucciones_y_no_un_502_pelado(sin_pda, cadena_falsa):
    # Sin delegación no podemos firmar por él. Que se entere con el mensaje que ya usa el
    # juego, y no con un 502 que no le dice qué hacer.
    firmante = FakeSigner()

    async def _no(wallet_id):
        return False
    firmante.podemos_firmar = _no

    c, priv, _ = _cliente(privy_signer=firmante)
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 409


def test_sin_delegar_lleva_detail_needs_delegation(sin_pda, cadena_falsa):
    # El cliente tiene que diferenciar entre "ya reclamaste" (409 con otro detail) y
    # "no autorizaste firma" (409 con detail needs_delegation), porque la pantalla tiene que
    # contar historias completamente diferentes: una es "tu dinero ya se movió" y la otra es
    # "nos di permiso para firmar tus tiradas, ahora hazlo también para tu airdrop".
    firmante = FakeSigner()

    async def _no(wallet_id):
        return False
    firmante.podemos_firmar = _no

    c, priv, _ = _cliente(privy_signer=firmante)
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 409
    assert r.json()["detail"] == "needs_delegation"


def test_si_otra_pestana_se_adelanta_sale_ya_reclamado(monkeypatch):
    # La PDA no existía al comprobar, pero para cuando llega la tx sí. No nos fiamos del
    # TEXTO del error del submit para saberlo —eso ataría el comportamiento a cómo redacte
    # su mensaje el proveedor de RPC de turno—: se le vuelve a preguntar a la cadena, y
    # esta vez dice que la PDA existe. Para el jugador eso NO es un fallo: sus tokens
    # están en su sitio.
    estado = {"reclamado": False}

    async def _cuenta(rpc_url, pubkey, **kw):
        return {"lamports": 1} if estado["reclamado"] else None
    monkeypatch.setattr("app.main._airdrop_cuenta", _cuenta)

    async def _bh(rpc_url):
        return "11111111111111111111111111111111"
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)

    async def _submit(rpc_url, tx_b64):
        estado["reclamado"] = True   # la tx de la OTRA pestaña ya cuajó justo antes que esta
        raise RuntimeError("cualquier error de RPC — el texto no debe importarle al endpoint")
    monkeypatch.setattr("app.main.submit_signed_tx", _submit)

    c, priv, _ = _cliente()
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 409


def test_si_el_submit_falla_y_la_pda_sigue_sin_existir_es_502(sin_pda, monkeypatch):
    # Compañero del test anterior: si al volver a preguntar la cadena sigue diciendo que
    # la PDA NO existe, no es una carrera ganada por otra pestaña, es un fallo real, y
    # tiene que seguir siendo 502 y no un falso "ya reclamado". De paso comprueba que el
    # cuerpo del 502 no repite el texto crudo del RPC (podría traer su api-key).
    async def _bh(rpc_url):
        return "11111111111111111111111111111111"
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)

    async def _submit(rpc_url, tx_b64):
        raise RuntimeError("fallo real — url secreta: https://rpc.example.com/?api-key=zzz")
    monkeypatch.setattr("app.main.submit_signed_tx", _submit)

    c, priv, _ = _cliente()
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 502
    assert "api-key=zzz" not in r.text


def test_pubkey_de_configuracion_invalida_da_503_no_500(sin_pda, cadena_falsa):
    # Un typo en CARDS_AIRDROP_VAULT/MINT no puede tumbar el endpoint con un 500: es un
    # problema de configuración, no del jugador, y como tal debe ser reintentable.
    c, priv, _ = _cliente(cards_airdrop_vault="esto-no-es-una-pubkey")
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 503


def test_si_falla_la_fila_igual_se_responde_200(sin_pda, cadena_falsa, monkeypatch, caplog):
    # El dinero ya se movió on-chain cuando llegamos a guardar la fila: que esa escritura
    # falle no puede volverse un error para el jugador, la misma decisión que ya existe en
    # /tip. Se responde 200 con la firma y se deja constancia a voces en el log.
    def _roto(*a, **kw):
        raise RuntimeError("db caída")
    monkeypatch.setattr("app.main.AirdropClaim", _roto)

    c, priv, _ = _cliente()
    with caplog.at_level("ERROR"):
        r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 200
    assert r.json() == {"signature": "firma-de-mentira-1", "amount": 1_483_000_000}
    assert any(rec.levelname == "ERROR" for rec in caplog.records)


def test_502_de_ya_reclamado_no_incluye_el_texto_crudo_del_error(monkeypatch):
    # _ya_reclamado la dispara CADA carga de /claim, para todo jugador elegible: es el
    # camino con más probabilidad real de disparar un 429/401 del proveedor de RPC, y su
    # texto puede traer la URL entera con el ?api-key= puesto.
    async def _cuenta(rpc_url, pubkey, **kw):
        raise RuntimeError("429 from https://rpc.example.com/?api-key=fugado-de-verdad")
    monkeypatch.setattr("app.main._airdrop_cuenta", _cuenta)

    c, priv, _ = _cliente()
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.status_code == 502
    assert "fugado-de-verdad" not in r.text


def test_distributor_invalido_da_503_no_500(sin_pda):
    # Un typo en CARDS_AIRDROP_DISTRIBUTOR hace que claim_status_pda reviente con ValueError.
    # Antes de esta comprobación eso salía como 500 porque la llamada vivía fuera del try de
    # _ya_reclamado; es un problema de configuración, así que tiene que ser 503, reintentable.
    c, priv, _ = _cliente(cards_airdrop_distributor="esto-no-es-una-pubkey")
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.status_code == 503


def test_si_falla_el_blockhash_del_claim_es_502_no_500(sin_pda, monkeypatch):
    # fetch_latest_blockhash no llevaba ninguna guarda: un fallo de RPC ahí tumbaba el
    # endpoint entero con un 500. Tiene que ser 502 (problema de cadena, reintentable) y sin
    # el texto crudo de la excepción, que puede traer la url con api-key.
    async def _bh(rpc_url):
        raise RuntimeError("rpc caído — url secreta: https://rpc.example.com/?api-key=otro-mas")
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)

    c, priv, _ = _cliente()
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 502
    assert "otro-mas" not in r.text


def test_el_claim_reusa_el_throttle_del_withdraw(sin_pda, cadena_falsa):
    # El operador paga la renta de la ATA nueva en cada claim, igual que en /withdraw, así
    # que sin límite un jugador podría vaciarle el SOL a base de reclamar en bucle (aquí la
    # PDA nunca "se marca" en la cadena falsa, así que sin throttle esto pasaría siempre).
    c, priv, _ = _cliente(withdraw_rate_limit=1, withdraw_rate_window_s=60.0)
    r1 = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r1.status_code == 200, r1.text
    r2 = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r2.status_code == 429


def test_502_del_check_de_ata_no_incluye_el_texto_crudo_del_error(monkeypatch):
    # Un 429/401 del proveedor de RPC puede traer la URL entera, api-key incluida, en el
    # texto de la excepción. Esa cadena no puede llegar nunca al cuerpo de la respuesta.
    # La PRIMERA llamada a _airdrop_cuenta es la de _ya_reclamado (debe decir "no, todavía
    # no"); la SEGUNDA es el check de si hace falta crear la ATA, y es la que se rompe —
    # ese es el sitio que toca esta tarea.
    llamadas = {"n": 0}

    async def _cuenta(rpc_url, pubkey, **kw):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return None
        raise RuntimeError("429 from https://rpc.example.com/?api-key=secreto-de-verdad")
    monkeypatch.setattr("app.main._airdrop_cuenta", _cuenta)

    c, priv, _ = _cliente()
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 502
    assert "secreto-de-verdad" not in r.text
