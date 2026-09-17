"""Datos REALES de mainnet, capturados el 2026-09-08.

El test dorado es el primero: si el hashing del árbol se desvía lo más mínimo, deja de
casar con la root que está en la cadena y este test se pone rojo. Sin él, un cambio en
esas 20 líneas no rompe nada visible hasta que un jugador le da al botón y la cadena le
rechaza la transacción.
"""
from solders.pubkey import Pubkey

from app.services.cards_airdrop import claim_status_pda, hoja, verificar_proof

DISTRIBUTOR = "H6k7zSjCn2w5Q4em3b3E7iaPQfLrxVsF6u1bK6kD1Bhq"
MINT = "CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp"
ROOT = bytes.fromhex("edef2b3b6e41e35a7843cd3521490a576651df938ffc40fb3e79c9de146503bd")

WALLET = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"
INDEX = 1687
AMOUNT = 1_483_000_000
PROOF = [
    "9Ad5fSi8QPvs8CimVj8vFwSXJkkZ5fgvnhmefmr6QEKv",
    "AyiMrqFWe4hSPXmxuMqAzVLWExLuCH4LiRBQZnWNdnzM",
    "AS2kiSZyCnX31sYKDMxnxCXcyuhXR1VRvSRLJPHFpYUW",
    "E6xE2sR5PztQDaRZZ3eNBGEiuE48Mk7JegjkWH8x3RmK",
    "6AoBdX8FvPH4cQ1XShbT3oeJCsqCCehtbQx3EFzz7neu",
    "8HbxK8ihpGv2nSQ9fXZ8Wiqogn51YnyTLqDbYVGQKYsh",
    "PrrjJUtKGF5fmTR9Yae6J6QbCKz2SzrvhqEmPVQ2XgZ",
    "4utS1WxK3ox27bMrx5uxrBQnWCX2bwFYTqK8f6doNsg9",
    "GV61PisXTh1C6f9PVWnnX9LJWwGyDcfKmXz69yq6nSsg",
    "Hg1tmDKuGtTZ4sEvZR9YDR3pqmQ9VVg3YmUJHosZ2SG",
    "HV2qmcXHb6cm7eZ1m6faMP79rR132jDJyWjcHSxVZsJ",
    "5ye3ARu1aNukAebBW8jGFLgYktiCR4C4et8ipEuQzaAo",
    "FXyuDoR4Vczd2RUfPCkQT7bviNZwDz1T3k8KWfq77mkm",
]


def proof_bytes() -> list[bytes]:
    return [bytes(Pubkey.from_string(p)) for p in PROOF]


def test_la_hoja_real_valida_contra_la_root_de_mainnet():
    assert verificar_proof(hoja(INDEX, WALLET, MINT, AMOUNT), proof_bytes(), ROOT)


def test_una_cantidad_cambiada_no_valida():
    # Es la mitad que importa: si esto pasara, cualquiera podría pedir lo que quisiera.
    assert not verificar_proof(hoja(INDEX, WALLET, MINT, AMOUNT + 1), proof_bytes(), ROOT)


def test_otra_wallet_con_el_mismo_proof_no_valida():
    otra = "FzRt4Pnh6tBpavXqkwQH1WVByeotDSefyyACKXC5kGHZ"
    assert not verificar_proof(hoja(INDEX, otra, MINT, AMOUNT), proof_bytes(), ROOT)


def test_la_hoja_tiene_la_forma_que_espera_gumdrop():
    b = hoja(INDEX, WALLET, MINT, AMOUNT)
    assert len(b) == 80                                  # 8 + 32 + 32 + 8
    assert b[:8] == INDEX.to_bytes(8, "little")
    assert b[8:40] == bytes(Pubkey.from_string(WALLET))
    assert b[40:72] == bytes(Pubkey.from_string(MINT))
    assert b[72:] == AMOUNT.to_bytes(8, "little")


def test_la_pda_de_claim_status_es_la_de_la_cadena():
    pda, bump = claim_status_pda(INDEX, DISTRIBUTOR)
    assert str(pda) == "E1frLrGw1V1mVN6R7KcTwvBYD87ezWZKrspbs5dWJH6g"
    assert bump == 255


import json

from app.services.cards_airdrop import cargar_asignaciones


def test_carga_el_fichero_de_asignaciones(tmp_path):
    f = tmp_path / "a.json"
    f.write_text(json.dumps({WALLET: {"i": INDEX, "a": AMOUNT, "p": PROOF}}))
    d = cargar_asignaciones(str(f))
    assert d[WALLET]["i"] == INDEX


def test_sin_ruta_devuelve_vacio_en_vez_de_reventar():
    # Es el estado normal en devnet: la función está apagada, no rota.
    assert cargar_asignaciones("") == {}


def test_un_fichero_que_no_existe_devuelve_vacio(tmp_path):
    assert cargar_asignaciones(str(tmp_path / "no-esta.json")) == {}


def test_un_fichero_corrupto_devuelve_vacio(tmp_path):
    # Vacío hace que los endpoints respondan 503. Lo que NO puede pasar es que un
    # fichero ilegible acabe diciéndole a un jugador elegible que no lo es.
    f = tmp_path / "roto.json"
    f.write_text("{esto no es json")
    assert cargar_asignaciones(str(f)) == {}


def test_json_valido_pero_lista_devuelve_vacio(tmp_path):
    # JSON válido que no es un dict es tan inutilizable como uno roto. No puede pasar
    # que un fichero mal formado acabe diciéndole a un jugador elegible que no lo es.
    f = tmp_path / "lista.json"
    f.write_text(json.dumps([1, 2, 3]))
    assert cargar_asignaciones(str(f)) == {}


def test_json_valido_pero_string_devuelve_vacio(tmp_path):
    # JSON válido que no es un dict es tan inutilizable como uno roto.
    f = tmp_path / "string.json"
    f.write_text(json.dumps("hello"))
    assert cargar_asignaciones(str(f)) == {}


def test_json_valido_pero_numero_devuelve_vacio(tmp_path):
    # JSON válido que no es un dict es tan inutilizable como uno roto.
    f = tmp_path / "numero.json"
    f.write_text(json.dumps(42))
    assert cargar_asignaciones(str(f)) == {}


import base64

from solders.transaction import Transaction as SoldersTx

from app.services.cards_airdrop import ata, build_claim_tx, instrucciones_claim

VAULT = "5TBR7KQHbPsf3wHZ11dyL9iifztCnN9Ccr6rzoCvYqW7"
OPERADOR = "3q6Ucr1s7Knkp5nRQKQe3dYPzoh72XQGnn2oCgSS9S34"
BLOCKHASH = "11111111111111111111111111111111"

GUMDROP = "gdrpGjVffourzkdDRrQmySw4aTHr8a3xmQzzxSwFD1a"
SYS = "11111111111111111111111111111111"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def _ixs(crear_ata=True):
    return instrucciones_claim(
        claimant=WALLET, index=INDEX, amount=AMOUNT, proof=PROOF,
        distributor=DISTRIBUTOR, vault=VAULT, mint=MINT,
        operador=OPERADOR, crear_ata=crear_ata,
    )


def test_las_cuentas_del_claim_van_en_el_orden_del_idl():
    ix = _ixs()[-1]
    assert str(ix.program_id) == GUMDROP
    cuentas = [str(a.pubkey) for a in ix.accounts]
    claim_status, _ = claim_status_pda(INDEX, DISTRIBUTOR)
    assert cuentas == [
        DISTRIBUTOR, str(claim_status), VAULT,
        str(ata(Pubkey.from_string(WALLET), Pubkey.from_string(MINT))),
        WALLET, OPERADOR, SYS, TOKEN,
    ]


def test_firma_el_jugador_como_temporal_y_el_operador_como_payer():
    # Es LA decisión del diseño: Gumdrop admite que `payer` sea otro, y por eso el
    # jugador no necesita SOL. Comprobado por simulación contra mainnet.
    ix = _ixs()[-1]
    assert [a.is_signer for a in ix.accounts] == [False, False, False, False, True, True, False, False]
    assert str(ix.accounts[4].pubkey) == WALLET      # temporal
    assert str(ix.accounts[5].pubkey) == OPERADOR    # payer, y es quien suelta el rent


def test_los_argumentos_llevan_discriminador_bump_index_amount_wallet_y_proof():
    import hashlib
    ix = _ixs()[-1]
    d = bytes(ix.data)
    _, bump = claim_status_pda(INDEX, DISTRIBUTOR)
    assert d[:8] == hashlib.sha256(b"global:claim").digest()[:8]
    assert d[8] == bump
    assert d[9:17] == INDEX.to_bytes(8, "little")
    assert d[17:25] == AMOUNT.to_bytes(8, "little")
    assert d[25:57] == bytes(Pubkey.from_string(WALLET))
    assert d[57:61] == len(PROOF).to_bytes(4, "little")
    assert len(d) == 61 + 32 * len(PROOF)


def test_la_ata_la_paga_el_operador_y_la_posee_el_jugador():
    ix = _ixs()[0]
    assert bytes(ix.data) == bytes([1])              # CreateIdempotent
    assert str(ix.accounts[0].pubkey) == OPERADOR and ix.accounts[0].is_signer
    assert str(ix.accounts[2].pubkey) == WALLET


def test_sin_crear_ata_solo_va_la_instruccion_del_claim():
    ixs = _ixs(crear_ata=False)
    assert len(ixs) == 1
    assert str(ixs[0].program_id) == GUMDROP


def test_el_fee_payer_de_la_transaccion_es_el_operador():
    b64 = build_claim_tx(
        claimant=WALLET, index=INDEX, amount=AMOUNT, proof=PROOF,
        distributor=DISTRIBUTOR, vault=VAULT, mint=MINT,
        operador=OPERADOR, blockhash=BLOCKHASH, crear_ata=True,
    )
    msg = SoldersTx.from_bytes(base64.b64decode(b64)).message
    assert str(msg.account_keys[0]) == OPERADOR
    assert msg.header.num_required_signatures == 2
