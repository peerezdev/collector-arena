"""Battle Royale USDC funding: buy-in math + pool distribute/collect/confirm. Pulls themselves are
paid by each player's wallet (funded just-in-time from the pool)."""
from __future__ import annotations
import httpx
from solders.pubkey import Pubkey
from solders.token.associated import get_associated_token_address
from app.services.solana_tx import build_token_transfer, build_token_multi_transfer, TOKEN_PROGRAM
from app.services.nft_transfer import submit_signed_tx


def total_pulls(n: int) -> int:
    return n * (n + 1) // 2 - 1


def royale_buyin(n: int, price_base: int) -> int:
    # integer ceiling (no float): round up so the pool always covers the pulls; remainder → winner
    total = total_pulls(n) * price_base
    return (total + n - 1) // n


async def confirm_usdc(rpc_url: str, owner: str, usdc_mint: str, min_base_units: int) -> bool:
    ata = str(get_associated_token_address(Pubkey.from_string(owner), Pubkey.from_string(usdc_mint)))
    # Network/RPC errors → treat as "not confirmed yet" (False), never crash the polling gate.
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": "getTokenAccountBalance",
                                            "params": [ata, {"commitment": "confirmed"}]}, timeout=20)
            r.raise_for_status(); d = r.json()
    except httpx.HTTPError:
        return False
    if "error" in d:
        return False
    v = (d.get("result") or {}).get("value")
    try:
        return v is not None and int(v["amount"]) >= min_base_units
    except (KeyError, ValueError, TypeError):
        return False


async def distribute_usdc(rpc_url, signer, escrow_wallet_id, escrow_address, player_address,
                          usdc_mint, amount, blockhash, *,
                          operator_wallet_id, operator_address) -> str:
    # 2-signer: escrow = USDC authority, operator = fee-payer. The escrow never needs SOL, which
    # avoids the devnet "debit an account but found no record of a prior credit" race on a freshly
    # seeded escrow. Same pattern as collect_buyin / refund_buyin.
    tx = build_token_transfer(escrow_address, player_address, usdc_mint, blockhash,
                              amount=amount, decimals=6, fee_payer=operator_address)
    signed = await signer.sign_solana(escrow_wallet_id, tx)        # escrow authorizes the USDC move
    signed = await signer.sign_solana(operator_wallet_id, signed)  # operator pays the fee
    return await submit_signed_tx(rpc_url, signed)


async def construir_y_firmar_cobro(signer, player_wallet_id, player_address, operator_wallet_id,
                                   operator_address, escrow_address, usdc_mint, amount,
                                   blockhash) -> str:
    """La MITAD de `collect_buyin` que todavía NO ha mandado nada a la red: construir la
    transacción y firmarla. Devuelve la transacción firmada en base64.

    POR QUÉ ESTÁ PARTIDA. Construir puede reventar (una dirección mal escrita en la
    configuración, un blockhash que no se puede interpretar) y firmar puede reventar (Privy no
    contesta), y ninguno de esos fallos ha difundido nada: son enteramente reintentables. Quien
    llama puede por tanto hacer todo esto ANTES de escribir en su base, y así un fallo aquí no
    deja rastro que luego haya que limpiar. Lo que no se puede deshacer empieza en `enviar_cobro`.

    2-signer: el jugador es la autoridad del USDC, el operador paga la fee (el jugador no tiene
    SOL).
    """
    tx = build_token_transfer(player_address, escrow_address, usdc_mint, blockhash,
                              amount=amount, decimals=6, fee_payer=operator_address)
    signed = await signer.sign_solana(player_wallet_id, tx)        # player authorizes the USDC move
    signed = await signer.sign_solana(operator_wallet_id, signed)  # operator pays the fee
    return signed


async def enviar_cobro(rpc_url, signed) -> str:
    """La otra mitad: difundir la transacción ya firmada. Devuelve su firma.

    Es la línea que separa lo reintentable de lo que ya no se puede deshacer: a partir del POST de
    `sendTransaction`, un error de red no significa que no haya salido.
    """
    return await submit_signed_tx(rpc_url, signed)


async def collect_buyin(rpc_url, signer, player_wallet_id, player_address, operator_wallet_id,
                        operator_address, escrow_address, usdc_mint, amount, blockhash) -> str:
    """Cobrar el buy-in de un jugador: construir, firmar y enviar, en una sola llamada.

    Envoltorio fino sobre las dos mitades de arriba, y a propósito: Pack Battle y Royale cobran
    dentro de una máquina de estados que no tiene dónde guardar una transacción a medio camino,
    así que para ellos "cobra esto" en un solo paso sigue siendo la forma correcta. Quien necesite
    anotar la firma antes de enviar (la compra del pase del tracker) llama a las dos por separado.
    """
    signed = await construir_y_firmar_cobro(signer, player_wallet_id, player_address,
                                            operator_wallet_id, operator_address, escrow_address,
                                            usdc_mint, amount, blockhash)
    return await enviar_cobro(rpc_url, signed)


async def withdraw_usdc(rpc_url, signer, player_wallet_id, player_address, operator_wallet_id,
                        operator_address, dest_address, usdc_mint, amount, blockhash) -> str:
    """User-initiated withdrawal: move USDC from the player's wallet to an EXTERNAL address.
    Same shape as collect_buyin (player = USDC authority, operator = fee-payer; the destination ATA
    is created idempotently and the operator pays its rent). Requires the player's wallet to be
    delegated so the server signer can authorize on their behalf."""
    tx = build_token_transfer(player_address, dest_address, usdc_mint, blockhash,
                              amount=amount, decimals=6, fee_payer=operator_address)
    signed = await signer.sign_solana(player_wallet_id, tx)        # player authorizes the USDC move
    signed = await signer.sign_solana(operator_wallet_id, signed)  # operator pays the fee
    return await submit_signed_tx(rpc_url, signed)


async def withdraw_usdc_with_fee(rpc_url, signer, player_wallet_id, player_address, operator_wallet_id,
                                 operator_address, dest_address, fee_dest, usdc_mint,
                                 net_amount, fee_amount, blockhash) -> str:
    """User withdrawal WITH a platform fee, in ONE atomic tx: net_amount → dest_address and
    fee_amount → fee_dest, both from the player's wallet. 2-signer: player authorizes, operator
    pays the fee. Atomic so the user never gets the net without the fee being collected."""
    tx = build_token_multi_transfer(player_address,
                                    [(dest_address, net_amount), (fee_dest, fee_amount)],
                                    usdc_mint, blockhash, decimals=6, fee_payer=operator_address)
    signed = await signer.sign_solana(player_wallet_id, tx)        # player authorizes the USDC moves
    signed = await signer.sign_solana(operator_wallet_id, signed)  # operator pays the fee
    return await submit_signed_tx(rpc_url, signed)


async def refund_buyin(rpc_url, signer, escrow_wallet_id, escrow_address, operator_wallet_id,
                       operator_address, player_address, usdc_mint, amount, blockhash) -> str:
    """Refund a buy-in escrow→player. 2-signer: escrow = USDC authority, operator = fee-payer
    (the escrow may hold no SOL when a royale is cancelled before it ran/seeded)."""
    tx = build_token_transfer(escrow_address, player_address, usdc_mint, blockhash,
                              amount=amount, decimals=6, fee_payer=operator_address)
    signed = await signer.sign_solana(escrow_wallet_id, tx)
    signed = await signer.sign_solana(operator_wallet_id, signed)
    return await submit_signed_tx(rpc_url, signed)
