"""Claim del airdrop $CARDS de Collector Crypt, que es un Metaplex Gumdrop.

Las piezas puras van separadas de todo lo que habla con la red porque son las únicas que
se pueden probar de verdad: el hashing del árbol se contrasta contra una entrada real de
mainnet sin abrir un socket.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import struct

from Crypto.Hash import keccak
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

logger = logging.getLogger(__name__)

GUMDROP_PROGRAM = Pubkey.from_string("gdrpGjVffourzkdDRrQmySw4aTHr8a3xmQzzxSwFD1a")
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYS_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
_DISC_CLAIM = hashlib.sha256(b"global:claim").digest()[:8]


def _keccak256(data: bytes) -> bytes:
    h = keccak.new(digest_bits=256)
    h.update(data)
    return h.digest()


def hoja(index: int, claimant: str, mint: str, amount: int) -> bytes:
    """La hoja del árbol: index_le8 || claimant(32) || mint(32) || amount_le8.

    El orden y el endianness no son negociables: es lo que hashea el programa on-chain
    para comprobar el proof, así que cualquier variación lo invalida.
    """
    return (
        struct.pack("<Q", index)
        + bytes(Pubkey.from_string(claimant))
        + bytes(Pubkey.from_string(mint))
        + struct.pack("<Q", amount)
    )


def verificar_proof(hoja_bytes: bytes, proof: list[bytes], root: bytes) -> bool:
    """Rehace el camino de la hoja hasta la raíz y compara.

    Gumdrop prefija 0x00 a las hojas y 0x01 a los nodos internos (para que una hoja no
    pueda hacerse pasar por un nodo), y ordena los dos hijos byte a byte antes de
    juntarlos, así que el proof no necesita decir si cada hermano va a izquierda o derecha.
    """
    h = _keccak256(b"\x00" + hoja_bytes)
    for hermano in proof:
        izq, der = sorted([h, hermano])
        h = _keccak256(b"\x01" + izq + der)
    return h == root


def claim_status_pda(index: int, distributor: str) -> tuple[Pubkey, int]:
    """Que esta cuenta exista es la ÚNICA señal fiable de "ya reclamado".

    El saldo de la wallet no vale: los tokens se pueden haber vendido y seguiría siendo
    cierto que ya se reclamaron. Y nuestra propia tabla tampoco, porque alguien puede
    haber reclamado en la web de CC sin pasar por aquí.
    """
    return Pubkey.find_program_address(
        [b"ClaimStatus", struct.pack("<Q", index), bytes(Pubkey.from_string(distributor))],
        GUMDROP_PROGRAM,
    )


def cargar_asignaciones(path: str) -> dict[str, dict]:
    """Lee el fichero de asignaciones una sola vez, al arrancar.

    Devuelve un dict vacío ante cualquier problema, y eso hace que los endpoints
    respondan 503. La alternativa —seguir con media lista— sería peor: le diría a un
    jugador elegible que no lo es, que es exactamente el error que no nos podemos
    permitir aquí.
    """
    if not path:
        return {}
    try:
        with open(path, encoding="utf8") as f:
            data = json.load(f)
            # Un fichero bien formado pero con forma equivocada es tan inutilizable
            # como uno que no parsa: no puede pasar que un fichero roto le diga a un
            # jugador elegible que no lo es.
            if not isinstance(data, dict):
                logger.error("airdrop: el fichero de asignaciones no es un dict %s", path)
                return {}
            return data
    except (OSError, ValueError):
        logger.exception("airdrop: no se pudo leer el fichero de asignaciones %s", path)
        return {}


def ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)], ATA_PROGRAM
    )[0]


def instrucciones_claim(*, claimant: str, index: int, amount: int, proof: list[str],
                        distributor: str, vault: str, mint: str, operador: str,
                        crear_ata: bool) -> list[Instruction]:
    """Las instrucciones del claim: la ATA idempotente delante (si hace falta) y el
    `claim` de Gumdrop detrás.

    El operador va de `payer` en las dos, y el jugador solo firma como `temporal`. El
    programa admite que sean cuentas distintas —comprobado por simulación contra
    mainnet— y de ahí sale que el jugador no necesite tener SOL.
    """
    jugador = Pubkey.from_string(claimant)
    op = Pubkey.from_string(operador)
    mint_pk = Pubkey.from_string(mint)
    destino = ata(jugador, mint_pk)
    claim_status, bump = claim_status_pda(index, distributor)

    data = (
        _DISC_CLAIM
        + bytes([bump])
        + struct.pack("<Q", index)
        + struct.pack("<Q", amount)
        + bytes(jugador)                      # claimantSecret: para method=wallets es la propia wallet
        + struct.pack("<I", len(proof))
        + b"".join(bytes(Pubkey.from_string(p)) for p in proof)
    )

    ixs: list[Instruction] = []
    if crear_ata:
        ixs.append(Instruction(ATA_PROGRAM, bytes([1]), [   # 1 = CreateIdempotent
            AccountMeta(op, is_signer=True, is_writable=True),
            AccountMeta(destino, is_signer=False, is_writable=True),
            AccountMeta(jugador, is_signer=False, is_writable=False),
            AccountMeta(mint_pk, is_signer=False, is_writable=False),
            AccountMeta(SYS_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(TOKEN_PROGRAM, is_signer=False, is_writable=False),
        ]))
    ixs.append(Instruction(GUMDROP_PROGRAM, data, [
        AccountMeta(Pubkey.from_string(distributor), is_signer=False, is_writable=True),
        AccountMeta(claim_status, is_signer=False, is_writable=True),
        AccountMeta(Pubkey.from_string(vault), is_signer=False, is_writable=True),
        AccountMeta(destino, is_signer=False, is_writable=True),
        AccountMeta(jugador, is_signer=True, is_writable=False),
        AccountMeta(op, is_signer=True, is_writable=True),
        AccountMeta(SYS_PROGRAM, is_signer=False, is_writable=False),
        AccountMeta(TOKEN_PROGRAM, is_signer=False, is_writable=False),
    ]))
    return ixs


def build_claim_tx(*, claimant: str, index: int, amount: int, proof: list[str],
                   distributor: str, vault: str, mint: str, operador: str,
                   blockhash: str, crear_ata: bool) -> str:
    """La misma transacción, legacy y sin firmar, en base64. Fee payer: el operador."""
    ixs = instrucciones_claim(
        claimant=claimant, index=index, amount=amount, proof=proof,
        distributor=distributor, vault=vault, mint=mint,
        operador=operador, crear_ata=crear_ata,
    )
    msg = Message.new_with_blockhash(ixs, Pubkey.from_string(operador), Hash.from_string(blockhash))
    return base64.b64encode(bytes(Transaction.new_unsigned(msg))).decode()
