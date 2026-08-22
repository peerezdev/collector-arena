"""
Solana NFT transfer transaction builder for BattleArena Pack Battle escrow→winner transfers.

Builds an unsigned legacy transaction that:
  1. Creates the destination ATA idempotently (CreateIdempotent)
  2. Transfers 1 unit of the NFT mint via transfer_checked

Scope: regular SPL Token NFTs (graded cards).
Compressed NFTs (cNFT / Bubblegum + DAS) are out of scope and need a different path.
"""

import asyncio
import base64
import json
from typing import Optional

import httpx
from solders.pubkey import Pubkey
from solders.hash import Hash
from solders.instruction import Instruction, AccountMeta
from solders.message import Message
from solders.signature import Signature
from solders.transaction import Transaction
from solders.token.associated import get_associated_token_address
from solders.system_program import transfer, TransferParams

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"   # classic SPL Token program
ATA_PROGRAM   = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"   # associated-token-account program
SYS_PROGRAM   = "11111111111111111111111111111111"


MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"


def build_memo_tx(payer: str, recent_blockhash: str, texto: str = "collector-arena") -> str:
    """Transacción sin firmar con una sola instrucción Memo, en base64.

    Nace para el canje de tiradas gratis: Collector Crypt pide una transacción firmada por la
    wallet como PRUEBA DE PROPIEDAD, y no llega a enviarla a la cadena (medido). Un memo es lo más
    barato que se puede firmar y no mueve nada, así que si algún día CC decidiera enviarla, lo
    peor que pasa es un memo y su fee.
    """
    payer_pk = Pubkey.from_string(payer)
    ix = Instruction(Pubkey.from_string(MEMO_PROGRAM), texto.encode(), [])
    msg = Message.new_with_blockhash([ix], payer_pk, Hash.from_string(recent_blockhash))
    return base64.b64encode(bytes(Transaction.new_unsigned(msg))).decode()


def build_free_pack_proof_tx(payer: str, recent_blockhash: str, nonce: str) -> str:
    """Prueba de propiedad para canjear una tirada gratis, en base64 y sin firmar.

    NO vale `build_memo_tx` aquí, aunque se le parezca. Collector Crypt endureció `/api/freePack`:
    la transacción ya no puede ser una cualquiera firmada por la wallet, tiene que llevar dentro
    el `nonce` que devuelve `/api/generateFreePack`. Sin él responde
    `400 {"error":"Missing or invalid nonce"}`, y lo comprueba ANTES que la firma.

    Se replica lo que hace la web de CC, instrucción por instrucción:

      1. Una transferencia de 0 lamports de la wallet a sí misma. No mueve nada; está para que la
         transacción tenga una instrucción de sistema y la wallet quede como firmante natural.
      2. Un memo cuyo CONTENIDO es el nonce, y que además lleva la wallet en sus cuentas marcada
         como firmante. Esa marca es la parte que ata el nonce a la wallet: sin ella el memo sería
         un texto que cualquiera podría haber escrito.

    Sigue sin enviarse a la cadena, así que lo peor que pasa si algún día CC la enviara es un memo
    y su fee.
    """
    payer_pk = Pubkey.from_string(payer)
    ix_pago = transfer(TransferParams(from_pubkey=payer_pk, to_pubkey=payer_pk, lamports=0))
    ix_memo = Instruction(Pubkey.from_string(MEMO_PROGRAM), nonce.encode(),
                          [AccountMeta(pubkey=payer_pk, is_signer=True, is_writable=False)])
    msg = Message.new_with_blockhash([ix_pago, ix_memo], payer_pk,
                                     Hash.from_string(recent_blockhash))
    return base64.b64encode(bytes(Transaction.new_unsigned(msg))).decode()


def build_token_transfer(
    source_address: str,
    dest_address: str,
    mint: str,
    recent_blockhash: str,
    *,
    amount: int = 1,
    decimals: int = 0,
    fee_payer: str = None,
    token_program: str = TOKEN_PROGRAM,
) -> str:
    """
    Return an unsigned legacy Solana transaction (base64-encoded) that transfers
    `amount` units of `mint` from the source's ATA to the destination's ATA,
    creating the destination ATA idempotently if needed.

    Fee payer = fee_payer if given, else source.  When fee_payer != source,
    both are marked is_signer=True (2-signer tx).

    Args:
        source_address:   Base58 pubkey of the source (token authority) account.
        dest_address:     Base58 pubkey of the destination wallet.
        mint:             Base58 pubkey of the token mint.
        recent_blockhash: Base58 blockhash string (e.g. from getLatestBlockhash).
        amount:           Number of base units to transfer (default=1 for NFTs).
        decimals:         Mint decimals for transfer_checked (default=0 for NFTs).
        fee_payer:        Fee payer pubkey (default=source).
        token_program:    Token program id (default = classic SPL Token).

    Returns:
        Base64-encoded bytes of the unsigned transaction.
    """
    src_pk         = Pubkey.from_string(source_address)
    dest_pk        = Pubkey.from_string(dest_address)
    mint_pk        = Pubkey.from_string(mint)
    token_prog_pk  = Pubkey.from_string(token_program)
    ata_prog_pk    = Pubkey.from_string(ATA_PROGRAM)
    sys_prog_pk    = Pubkey.from_string(SYS_PROGRAM)
    payer_pk       = Pubkey.from_string(fee_payer) if fee_payer else src_pk
    blockhash      = Hash.from_string(recent_blockhash)

    # -- Derive ATAs --
    src_ata  = get_associated_token_address(src_pk,  mint_pk, token_prog_pk)
    dest_ata = get_associated_token_address(dest_pk, mint_pk, token_prog_pk)

    # -- Instruction 1: CreateIdempotent ATA for destination --
    # discriminator 1 = CreateIdempotent (0 = Create, raises if already exists)
    create_ix = Instruction(
        ata_prog_pk,
        bytes([1]),
        [
            AccountMeta(payer_pk,      is_signer=True,  is_writable=True),   # payer
            AccountMeta(dest_ata,      is_signer=False, is_writable=True),   # ATA being created
            AccountMeta(dest_pk,       is_signer=False, is_writable=False),  # ATA owner
            AccountMeta(mint_pk,       is_signer=False, is_writable=False),
            AccountMeta(sys_prog_pk,   is_signer=False, is_writable=False),
            AccountMeta(token_prog_pk, is_signer=False, is_writable=False),
        ],
    )

    # -- Instruction 2: transfer_checked (discriminator 12) --
    # data: [12] + amount(u64 LE) + decimals(u8)
    transfer_data = bytes([12]) + amount.to_bytes(8, "little") + bytes([decimals])
    transfer_ix = Instruction(
        token_prog_pk,
        transfer_data,
        [
            AccountMeta(src_ata,  is_signer=False, is_writable=True),   # source ATA
            AccountMeta(mint_pk,  is_signer=False, is_writable=False),
            AccountMeta(dest_ata, is_signer=False, is_writable=True),
            AccountMeta(src_pk,   is_signer=True,  is_writable=False),  # source owner = transfer authority
        ],
    )

    # -- Assemble transaction --
    message = Message.new_with_blockhash([create_ix, transfer_ix], payer_pk, blockhash)
    tx = Transaction.new_unsigned(message)
    return base64.b64encode(bytes(tx)).decode()


def build_nft_transfer(
    escrow_address: str,
    dest_address: str,
    mint: str,
    recent_blockhash: str,
    token_program: str = TOKEN_PROGRAM,
    *,
    fee_payer: str = None,
) -> str:
    """
    Thin wrapper around build_token_transfer for NFT (amount=1, decimals=0).
    Fee payer = fee_payer if given (operator-sponsored), else the escrow/owner.
    """
    return build_token_transfer(
        escrow_address, dest_address, mint, recent_blockhash,
        amount=1, decimals=0, token_program=token_program, fee_payer=fee_payer,
    )


def build_create_ata(
    owner_address: str,
    mint: str,
    recent_blockhash: str,
    *,
    payer: str = None,
    token_program: str = TOKEN_PROGRAM,
) -> str:
    """Unsigned legacy tx with a single CreateIdempotent ATA instruction for
    owner_address's associated token account of `mint`. Fee payer = payer if given else owner.
    Used to pre-create the escrow's USDC ATA so CC's turbo auto-buyback payout does not revert."""
    owner_pk      = Pubkey.from_string(owner_address)
    mint_pk       = Pubkey.from_string(mint)
    token_prog_pk = Pubkey.from_string(token_program)
    ata_prog_pk   = Pubkey.from_string(ATA_PROGRAM)
    sys_prog_pk   = Pubkey.from_string(SYS_PROGRAM)
    payer_pk      = Pubkey.from_string(payer) if payer else owner_pk
    ata = get_associated_token_address(owner_pk, mint_pk, token_prog_pk)
    create_ix = Instruction(
        ata_prog_pk,
        bytes([1]),  # CreateIdempotent
        [
            AccountMeta(payer_pk,      is_signer=True,  is_writable=True),
            AccountMeta(ata,           is_signer=False, is_writable=True),
            AccountMeta(owner_pk,      is_signer=False, is_writable=False),
            AccountMeta(mint_pk,       is_signer=False, is_writable=False),
            AccountMeta(sys_prog_pk,   is_signer=False, is_writable=False),
            AccountMeta(token_prog_pk, is_signer=False, is_writable=False),
        ],
    )
    message = Message.new_with_blockhash([create_ix], payer_pk, Hash.from_string(recent_blockhash))
    return base64.b64encode(bytes(Transaction.new_unsigned(message))).decode()


def build_token_multi_transfer(
    source_address: str,
    transfers: list,
    mint: str,
    recent_blockhash: str,
    *,
    decimals: int,
    fee_payer: str,
    token_program: str = TOKEN_PROGRAM,
) -> str:
    """Transfer from ONE source to SEVERAL destinations in a single atomic tx, creating each
    destination ATA idempotently. `transfers` is a list of (dest_address, amount). `fee_payer`
    pays gas + ATA rent (2-signer: source authority + fee_payer). Used for the USDC withdraw fee
    split (net→dest, fee→fee_wallet) so the user never gets a partial withdrawal."""
    src_pk        = Pubkey.from_string(source_address)
    mint_pk       = Pubkey.from_string(mint)
    token_prog_pk = Pubkey.from_string(token_program)
    ata_prog_pk   = Pubkey.from_string(ATA_PROGRAM)
    sys_prog_pk   = Pubkey.from_string(SYS_PROGRAM)
    payer_pk      = Pubkey.from_string(fee_payer)
    blockhash     = Hash.from_string(recent_blockhash)
    src_ata       = get_associated_token_address(src_pk, mint_pk, token_prog_pk)

    ixs = []
    for dest_address, amount in transfers:
        dest_pk  = Pubkey.from_string(dest_address)
        dest_ata = get_associated_token_address(dest_pk, mint_pk, token_prog_pk)
        ixs.append(Instruction(ata_prog_pk, bytes([1]), [   # CreateIdempotent dest ATA
            AccountMeta(payer_pk,      is_signer=True,  is_writable=True),
            AccountMeta(dest_ata,      is_signer=False, is_writable=True),
            AccountMeta(dest_pk,       is_signer=False, is_writable=False),
            AccountMeta(mint_pk,       is_signer=False, is_writable=False),
            AccountMeta(sys_prog_pk,   is_signer=False, is_writable=False),
            AccountMeta(token_prog_pk, is_signer=False, is_writable=False),
        ]))
        ixs.append(Instruction(token_prog_pk, bytes([12]) + amount.to_bytes(8, "little") + bytes([decimals]), [
            AccountMeta(src_ata,  is_signer=False, is_writable=True),
            AccountMeta(mint_pk,  is_signer=False, is_writable=False),
            AccountMeta(dest_ata, is_signer=False, is_writable=True),
            AccountMeta(src_pk,   is_signer=True,  is_writable=False),   # source owner = transfer authority
        ]))
    message = Message.new_with_blockhash(ixs, payer_pk, blockhash)
    return base64.b64encode(bytes(Transaction.new_unsigned(message))).decode()


def leer_firma(signed_tx_b64: str) -> str:
    """La firma (el "txid") de una transacción YA FIRMADA, leída EN LOCAL y sin tocar la red.

    POR QUÉ EXISTE. El identificador de una transacción de Solana ES su primera firma, y esa firma
    viaja DENTRO de los bytes que se mandan: no la inventa el RPC al recibirla. Poder leerla antes
    de enviar es lo que permite anotar en la base "voy a enviar ESTA transacción" ANTES de
    enviarla — y por tanto no depender de que el envío conteste para saber qué mirar en un
    explorador si el envío se queda a medias.

    Es la primera firma y no otra porque `account_keys[0]` de un mensaje de Solana es siempre
    quien paga la fee, y `signatures[i]` corresponde a `account_keys[i]`: la ranura 0 es la del
    fee payer, que es la que el RPC devuelve como resultado de `sendTransaction`.

    UNA TX SIN FIRMAR NO ES UN ERROR SILENCIOSO. `Transaction.new_unsigned` deja esa ranura a
    ceros, y una firma de ceros se serializa a un base58 `1111…` que parece una cadena
    perfectamente válida. Guardarla sería peor que fallar: dejaría en la base una firma que no
    existe en ninguna cadena y que nadie podría reconciliar nunca. Por eso se rechaza a propósito.
    """
    try:
        tx = Transaction.from_bytes(base64.b64decode(signed_tx_b64))
    except Exception as e:                       # base64 roto, bytes que no son una transacción…
        raise ValueError("no se pudo interpretar la transacción firmada: %s" % e)
    firmas = tx.signatures
    if not firmas or firmas[0] == Signature.default():
        raise ValueError("la transacción no está firmada: la ranura del fee payer está a ceros")
    return str(firmas[0])


async def confirmar_firma(rpc_url: str, firma: str, *, intentos: int = 10,
                          espera_s: float = 1.5, timeout_s: float = 20.0) -> Optional[bool]:
    """Si esa transacción llegó a la cadena y salió BIEN. Tri-estado, no booleano.

    Existe porque `submit_signed_tx` solo envía: devuelve la firma sin esperar nada. Dar por
    cobrado un envío es regalar el pase cada vez que una transacción se cae después de salir.

    Devuelve tres cosas distintas, y quien llama las trata distinto:

      · `True`: confirmada o finalizada, sin error. El dinero se movió.
      · `False`: RECHAZO DEFINITIVO. La cadena la aceptó y la ejecutó MAL (`err` presente). El
        dinero NO se movió, y eso es una certeza, no una sospecha.
      · `None`: INDETERMINADO. Se agotaron los intentos sin ver ni un `err` ni una confirmación:
        red que nunca respondió, RPC que nunca vio la firma, o una transacción que se quedó en
        `processed` sin asentarse. El dinero PUEDE haberse movido.

    Antes esto devolvía `False` también para el caso indeterminado, y eso mentía: juntaba "seguro
    que no" con "ni idea" en el mismo valor, y quien llamaba no podía tratarlas distinto —
    exactamente el fallo que dejaba a alguien cobrado, sin acceso y sin ninguna firma que
    reconciliar (nada distinguía ese caso de un rechazo limpio). Aquí solo se cuenta lo que se
    sabe; decidir qué hacer con la incertidumbre es cosa de quien llama.

    Dos cosas más que parecen detalles y no lo son:

      · `processed` no basta: puede revertirse. Solo valen `confirmed` y `finalized`; lo demás
        (incluido quedarse en `processed` hasta agotar los intentos) es indeterminado.
      · Un cuerpo que no es JSON (200 con HTML de un proxy caído, respuesta vacía bajo carga...)
        cuenta como el mismo "todavía no lo sé": `r.json()` puede lanzar `json.JSONDecodeError`,
        que NO es subclase de `httpx.HTTPError`, así que hay que capturarla aparte para no
        dejarla escapar.

    `intentos` / `espera_s` / `timeout_s` son ajustables porque quien llama sabe mejor que esta
    función cuánto presupuesto total puede permitirse: una petición HTTP normal (sin proxy que
    aguante minutos delante) necesita un total mucho más corto que confirmar con calma una
    reconciliación en segundo plano.
    """
    for intento in range(intentos):
        if intento:
            await asyncio.sleep(espera_s)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(rpc_url, json={"jsonrpc": "2.0", "id": 1,
                                                "method": "getSignatureStatuses",
                                                "params": [[firma], {"searchTransactionHistory": True}]},
                                 timeout=timeout_s)
                r.raise_for_status()
                d = r.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            continue
        valor = ((d.get("result") or {}).get("value") or [None])[0]
        if not valor:
            continue
        if valor.get("err"):
            return False            # se ejecutó y falló: el dinero NO se movió
        if valor.get("confirmationStatus") in ("confirmed", "finalized"):
            return True
    return None                     # agotados los intentos sin veredicto: INDETERMINADO
