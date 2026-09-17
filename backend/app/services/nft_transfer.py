"""Multi-standard NFT transfer (escrow→winner). Pure builders + async resolvers.
Soporta pNFT (Metaplex Transfer), SPL, MPL Core y cNFT comprimidos (Bubblegum transfer_v2)."""
from __future__ import annotations
import base64
import re
import struct
import httpx
from typing import Optional

_SECRETO = re.compile(r"(api[-_]?key=)[^&\s\'\"]+", re.I)


def sin_secretos(texto) -> str:
    """Tapa la api-key de la URL del RPC en cualquier texto.

    Los errores de httpx incluyen la URL completa, y la del RPC lleva la clave de Helius. Ese texto
    acaba en logs y, peor, persistido en la base (`escrow_wallets.unavailable_reason`): sin esto un
    429 escribe la clave en disco. Comprobado en una ejecución real antes de añadirlo.
    """
    return _SECRETO.sub(r"\1[redactada]", str(texto))

from solders.pubkey import Pubkey
from solders.hash import Hash
from solders.instruction import Instruction, AccountMeta
from solders.message import Message
from solders.transaction import Transaction
from solders.token.associated import get_associated_token_address

METADATA_PROGRAM = Pubkey.from_string("metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s")
_META = bytes(METADATA_PROGRAM)


def metadata_pda(mint: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([b"metadata", _META, bytes(mint)], METADATA_PROGRAM)[0]


def master_edition_pda(mint: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([b"metadata", _META, bytes(mint), b"edition"], METADATA_PROGRAM)[0]


def token_record_pda(mint: Pubkey, ata: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(
        [b"metadata", _META, bytes(mint), b"token_record", bytes(ata)], METADATA_PROGRAM)[0]


TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
AUTH_RULES_PROGRAM = Pubkey.from_string("auth9SigNpDKz4sJJ1DfCTuZrZNSAgh9sFD3rboVmgg")
SYS_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
SYSVAR_INSTRUCTIONS = Pubkey.from_string("Sysvar1nstructions1111111111111111111111111")
COMPUTE_BUDGET = Pubkey.from_string("ComputeBudget111111111111111111111111111111")

# TransferV1 canonical flags (is_signer, is_writable), indices 0..16
_PNFT_FLAGS = [
    (False, True), (False, False), (False, True), (False, False), (False, False),
    (False, True), (False, False), (False, True), (False, True), (True, False),
    (True, True), (False, False), (False, False), (False, False), (False, False),
    (False, False), (False, False),
]


def build_pnft_transfer(escrow: str, winner: str, mint: str, recent_blockhash: str,
                        *, ruleset: str, fee_payer: Optional[str] = None) -> str:
    esc = Pubkey.from_string(escrow); win = Pubkey.from_string(winner); mnt = Pubkey.from_string(mint)
    payer = Pubkey.from_string(fee_payer) if fee_payer else esc   # operator-sponsored fee-payer, else owner
    esc_ata = get_associated_token_address(esc, mnt)
    win_ata = get_associated_token_address(win, mnt)
    accounts = [
        esc_ata,                              # 0 source token
        esc,                                  # 1 token_owner
        win_ata,                              # 2 destination token
        win,                                  # 3 destination_owner
        mnt,                                  # 4 mint
        metadata_pda(mnt),                    # 5 metadata
        master_edition_pda(mnt),              # 6 master edition
        token_record_pda(mnt, esc_ata),       # 7 owner token record
        token_record_pda(mnt, win_ata),       # 8 destination token record
        esc,                                  # 9 authority (owner)
        payer,                                # 10 payer (operator when sponsored, else owner)
        SYS_PROGRAM,                          # 11
        SYSVAR_INSTRUCTIONS,                  # 12
        TOKEN_PROGRAM,                        # 13
        ATA_PROGRAM,                          # 14
        AUTH_RULES_PROGRAM,                   # 15
        Pubkey.from_string(ruleset),          # 16
    ]
    metas = [AccountMeta(pubkey=accounts[i], is_signer=_PNFT_FLAGS[i][0], is_writable=_PNFT_FLAGS[i][1])
             for i in range(17)]
    data = bytes([49, 0]) + (1).to_bytes(8, "little") + bytes([0])  # Transfer, V1, amount=1, auth_data=None
    transfer_ix = Instruction(METADATA_PROGRAM, data, metas)
    cu_ix = Instruction(COMPUTE_BUDGET, bytes([2]) + (400000).to_bytes(4, "little"), [])
    msg = Message.new_with_blockhash([cu_ix, transfer_ix], payer, Hash.from_string(recent_blockhash))
    return base64.b64encode(bytes(Transaction.new_unsigned(msg))).decode()


def read_pnft_ruleset(data: bytes) -> Optional[Pubkey]:
    """Sequential Borsh walk of a Token Metadata account → programmable_config.ruleSet (or None).
    Returns None on any truncated/old-format buffer (caller voids rather than moving assets blindly)."""
    try:
        o = 1 + 32 + 32  # key + update_authority + mint
        for _ in range(3):  # name, symbol, uri (borsh String: u32 len + bytes)
            n = struct.unpack_from("<I", data, o)[0]; o += 4 + n
        o += 2  # seller_fee_basis_points u16
        if data[o] == 1:  # creators: Option<Vec<Creator>>
            n = struct.unpack_from("<I", data, o + 1)[0]; o = o + 1 + 4 + n * 34
        else:
            o += 1
        o += 1 + 1  # primary_sale_happened + is_mutable
        for _ in range(2):  # edition_nonce, token_standard (Option<u8>)
            o = o + 2 if data[o] == 1 else o + 1
        o = o + 1 + 33 if data[o] == 1 else o + 1  # collection Option<Collection>
        o = o + 1 + 17 if data[o] == 1 else o + 1  # uses Option<Uses>
        o = o + 1 + (1 + 8) if data[o] == 1 else o + 1  # collection_details Option (V1: u64)
        if data[o] == 1:  # programmable_config Option<ProgrammableConfig>
            o += 1  # Some
            o += 1  # variant (V1 = 0)
            if data[o] == 1:  # rule_set Option<Pubkey>
                return Pubkey.from_bytes(data[o + 1:o + 1 + 32])
        return None
    except (IndexError, struct.error):
        return None


# ---------------------------------------------------------------------------
# Detection + dispatcher + submit
# ---------------------------------------------------------------------------
MPL_CORE_PROGRAM = "CoREENxT6tW1HoK8ypY1SxRMZTcVPm7R94rH4PZNhX7d"
_MPL_CORE_PK = Pubkey.from_string(MPL_CORE_PROGRAM)


def read_core_owner(data: bytes) -> Optional[Pubkey]:
    """MPL Core AssetV1: key(1) + owner(32). El dueño vive en el PROPIO asset — un Core no tiene
    token account, así que es la única forma de saber quién lo tiene.
    Devuelve None si el buffer está truncado."""
    try:
        return Pubkey(data[1:33])
    except Exception:
        return None


def read_core_collection(data: bytes) -> Optional[Pubkey]:
    """MPL Core AssetV1: key(1) + owner(32) + update_authority enum.
    Variant 2 == Collection → next 32 bytes are the collection pubkey; variants 0/1 → None.
    Returns None on any truncated buffer (caller transfers without a collection)."""
    try:
        o = 1 + 32  # key + owner
        if data[o] == 2:  # Collection
            return Pubkey.from_bytes(data[o + 1:o + 1 + 32])
        return None
    except (IndexError, ValueError):
        return None


def build_core_transfer(escrow: str, winner: str, mint: str, recent_blockhash: str,
                        *, collection: Optional[str], fee_payer: Optional[str] = None) -> str:
    esc = Pubkey.from_string(escrow); win = Pubkey.from_string(winner); asset = Pubkey.from_string(mint)
    payer = Pubkey.from_string(fee_payer) if fee_payer else esc   # operator-sponsored fee-payer, else owner
    coll = Pubkey.from_string(collection) if collection else _MPL_CORE_PK  # None → program id
    metas = [
        AccountMeta(asset,    is_signer=False, is_writable=True),                       # 0 asset
        AccountMeta(coll,     is_signer=False, is_writable=(collection is not None)),   # 1 collection|None
        AccountMeta(payer,    is_signer=True,  is_writable=True),                       # 2 payer (operator when sponsored)
        AccountMeta(esc,      is_signer=True,  is_writable=False),                      # 3 authority (owner)
        AccountMeta(win,      is_signer=False, is_writable=False),                      # 4 new_owner
        AccountMeta(SYS_PROGRAM, is_signer=False, is_writable=False),                   # 5 system_program
        AccountMeta(_MPL_CORE_PK, is_signer=False, is_writable=False),                  # 6 log_wrapper (None)
    ]
    transfer_ix = Instruction(_MPL_CORE_PK, bytes([14, 0]), metas)  # TransferV1, compression_proof None
    cu_ix = Instruction(COMPUTE_BUDGET, bytes([2]) + (100000).to_bytes(4, "little"), [])
    msg = Message.new_with_blockhash([cu_ix, transfer_ix], payer, Hash.from_string(recent_blockhash))
    return base64.b64encode(bytes(Transaction.new_unsigned(msg))).decode()


class UnsupportedNftStandard(Exception):
    pass


# ── cNFT comprimidos: Bubblegum transfer_v2 ───────────────────────────────────
# Un cNFT no es una cuenta: es una hoja de un árbol de Merkle. Moverlo no es "transferir un token",
# es probarle al programa dónde está la hoja (root + proof) y reescribirla.
#
# El layout de esta instrucción NO está adivinado. Se sacó decodificando una transacción real de
# Collector Crypt (su buyback mueve estos mismos cNFT): de ahí salen el discriminador, las 11
# cuentas base y el orden de los campos, y cada campo se verificó contra lo que responde DAS. Los
# dos únicos cambios respecto a la de CC son quién paga y quién recibe.
BUBBLEGUM_PROGRAM = Pubkey.from_string("BGUMAp9Gq7iTEuizy4pqaxsTyUCBK68MDfK752saRPUY")
MPL_NOOP = Pubkey.from_string("mnoopTCrg4p8ry25e4bcWA9XZjbNjMTfgYVGGEdRsf3")
MPL_ACCOUNT_COMPRESSION = Pubkey.from_string("mcmt6YrQEMKw8Mw43FmpRLmf7BqRnFMKmAcbxE3xkAW")
_TRANSFER_V2_DISC = bytes.fromhex("772806ebeaddf831")   # sha256("global:transfer_v2")[:8]

# Límite de tamaño de una transacción de Solana. Importa aquí porque el proof son cuentas: 20 nodos
# ocupan 640 bytes solo en claves y la transacción no cabe.
MAX_TX_BYTES = 1232


def tree_config_pda(tree: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([bytes(tree)], BUBBLEGUM_PROGRAM)[0]


def _opt(value: Optional[bytes]) -> bytes:
    """Option<T> de Borsh: 1 byte de etiqueta y, si es Some, el contenido."""
    return b"\x00" if value is None else b"\x01" + value


def build_cnft_transfer(escrow: str, winner: str, recent_blockhash: str, *, tree: str,
                        collection: str, root: bytes, data_hash: bytes, creator_hash: bytes,
                        asset_data_hash: Optional[bytes], flags: Optional[int], leaf_id: int,
                        proof: list, fee_payer: Optional[str] = None) -> str:
    """transfer_v2 de Bubblegum. Constructor puro: el llamante trae los datos de DAS.

    `proof` son los nodos del camino de Merkle, de la hoja hacia la raíz. Se pasan como cuentas y no
    caben todos: el árbol guarda sus niveles superiores en un *canopy* y el programa completa desde
    ahí lo que no se le mande. Así que se envían los que quepan empezando por la hoja — pasar de más
    nunca estorba, pasar de menos solo funciona si el canopy cubre el resto (en el árbol de CC cubre
    14 de 20 niveles, y con 6 sobra sitio).
    """
    esc = Pubkey.from_string(escrow); win = Pubkey.from_string(winner)
    tre = Pubkey.from_string(tree); col = Pubkey.from_string(collection)
    payer = Pubkey.from_string(fee_payer) if fee_payer else esc

    data = (_TRANSFER_V2_DISC + root + data_hash + creator_hash
            + _opt(asset_data_hash)
            + _opt(None if flags is None else bytes([flags]))
            + leaf_id.to_bytes(8, "little")      # nonce
            + leaf_id.to_bytes(4, "little"))     # index (misma hoja)

    base = [
        AccountMeta(tree_config_pda(tre), is_signer=False, is_writable=True),   # 0 tree_config
        AccountMeta(payer, is_signer=True,  is_writable=True),                 # 1 payer
        AccountMeta(esc,   is_signer=True,  is_writable=False),                 # 2 leaf_owner
        AccountMeta(esc,   is_signer=True,  is_writable=False),                 # 3 ·
        AccountMeta(esc,   is_signer=True,  is_writable=False),                 # 4 ·
        AccountMeta(win,   is_signer=False, is_writable=False),                 # 5 new_leaf_owner
        AccountMeta(tre,   is_signer=False, is_writable=True),                  # 6 merkle_tree
        AccountMeta(col,   is_signer=False, is_writable=False),                 # 7 core_collection
        AccountMeta(MPL_NOOP, is_signer=False, is_writable=False),              # 8 log_wrapper
        AccountMeta(MPL_ACCOUNT_COMPRESSION, is_signer=False, is_writable=False),  # 9 compression
        AccountMeta(SYS_PROGRAM, is_signer=False, is_writable=False),           # 10 system
    ]
    # La verificación del proof consume mucho más que un traspaso normal.
    cu_ix = Instruction(COMPUTE_BUDGET, bytes([2]) + (250000).to_bytes(4, "little"), [])
    firmas = 2 if (fee_payer and fee_payer != escrow) else 1

    for n in range(len(proof), -1, -1):
        metas = base + [AccountMeta(Pubkey.from_string(p), is_signer=False, is_writable=False)
                        for p in proof[:n]]
        ix = Instruction(BUBBLEGUM_PROGRAM, data, metas)
        msg = Message.new_with_blockhash([cu_ix, ix], payer, Hash.from_string(recent_blockhash))
        raw = bytes(Transaction.new_unsigned(msg))
        if len(raw) + firmas * 64 <= MAX_TX_BYTES:
            return base64.b64encode(raw).decode()
    raise UnsupportedNftStandard("cnft: la transacción no cabe ni sin proof")


async def das_get_asset_proof(rpc_url: str, mint: str) -> Optional[dict]:
    """El camino de Merkle del asset según DAS, o None si DAS no lo conoce."""
    return await _das(rpc_url, "getAssetProof", mint)


def _b58(s: str) -> bytes:
    return bytes(Pubkey.from_string(s))     # los hashes de DAS vienen en base58, como las claves


async def resolve_cnft_transfer(rpc_url: str, escrow: str, winner: str, mint: str, blockhash: str,
                                *, fee_payer: Optional[str] = None) -> str:
    """Reúne de DAS lo que hace falta y construye el transfer_v2."""
    # Un DasUnavailable NO se convierte en UnsupportedNftStandard: sube tal cual para que el
    # reintento del settle lo vuelva a intentar. UnsupportedNftStandard significa "no insistas".
    asset = await das_get_asset(rpc_url, mint)
    prf = await das_get_asset_proof(rpc_url, mint)
    if asset is None or prf is None:
        # DAS contestó y no lo conoce: no hay ninguna carta detrás de este mint.
        raise UnsupportedNftStandard(f"el mint {mint} no existe on-chain (DAS no lo conoce)")

    comp = asset.get("compression") or {}
    coll = next((g.get("group_value") for g in (asset.get("grouping") or [])
                 if g.get("group_key") == "collection"), None)
    if not coll:
        # Sin colección no se sabe qué mandar en esa cuenta, y adivinarlo puede firmar cualquier
        # cosa. Mejor fallar y que se vea.
        raise UnsupportedNftStandard("cnft sin colección: no soportado")
    if (asset.get("ownership") or {}).get("delegate"):
        raise UnsupportedNftStandard("cnft con delegado: no soportado")

    adh = comp.get("asset_data_hash")
    return build_cnft_transfer(
        escrow, winner, blockhash,
        tree=comp["tree"], collection=coll,
        root=_b58(prf["root"]), data_hash=_b58(comp["data_hash"]),
        creator_hash=_b58(comp["creator_hash"]),
        asset_data_hash=_b58(adh) if adh else None,
        flags=comp.get("flags"), leaf_id=comp["leaf_id"],
        proof=prf["proof"], fee_payer=fee_payer)


async def _get_account(rpc_url: str, pubkey: str, *, commitment: Optional[str] = None) -> Optional[dict]:
    cfg = {"encoding": "base64"}
    if commitment:
        cfg["commitment"] = commitment
    async with httpx.AsyncClient() as c:
        r = await c.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                  "params": [pubkey, cfg]},
            timeout=20,
        )
        r.raise_for_status()
        return (r.json().get("result") or {}).get("value")


# Alias público: main.py lo usa para saber si existe una PDA o una ATA sin duplicar el RPC.
leer_cuenta = _get_account


class DasUnavailable(Exception):
    """No se pudo preguntar a DAS. NO significa que la carta no exista, solo que no lo sabemos.

    La diferencia con "DAS dice que no lo conoce" es de comportamiento, no de redacción: esto es
    transitorio y hay que reintentar, mientras que una carta que no existe no va a aparecer nunca.
    Confundirlos es lo que nos tuvo semanas persiguiendo "mints inexistentes" que resultaron ser
    cNFT perfectamente vivos.
    """


async def _das(rpc_url: str, method: str, mint: str) -> Optional[dict]:
    """Llama a un método DAS. Devuelve None si DAS contesta que no conoce el asset.
    Levanta DasUnavailable si no se pudo preguntar (RPC sin DAS, 429, timeout…).
    """
    async with httpx.AsyncClient() as c:
        try:
            r = await c.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                            "params": {"id": mint}}, timeout=20)
            r.raise_for_status()          # un RPC sin DAS responde 404; Helius limita con 429
        except Exception as exc:
            # La URL del RPC lleva la api-key y httpx la mete en su mensaje de error.
            raise DasUnavailable(f"{method}: {sin_secretos(exc)}") from exc
    d = r.json()
    if isinstance(d.get("result"), dict):
        return d["result"]
    err = d.get("error") or {}
    if "not found" in str(err.get("message", "")).lower():
        return None                       # DAS contestó y no lo conoce: el mint no existe
    raise DasUnavailable(f"{method}: {err or 'respuesta sin result'}")


async def das_get_asset(rpc_url: str, mint: str) -> Optional[dict]:
    """El asset según DAS, o None si DAS no lo conoce.

    Un cNFT comprimido no tiene cuenta propia: vive como una hoja de un árbol de Merkle, así que
    `getAccountInfo` responde null y por RPC normal es indistinguible de un mint que no existe.
    DAS (`getAsset`) es lo único que lo ve.
    """
    return await _das(rpc_url, "getAsset", mint)


def _token_standard(data: bytes) -> Optional[int]:
    """Walk Borsh-encoded Token Metadata account bytes to the token_standard Option<u8>."""
    o = 1 + 32 + 32  # key + update_authority + mint
    for _ in range(3):  # name, symbol, uri
        n = struct.unpack_from("<I", data, o)[0]; o += 4 + n
    o += 2  # seller_fee_basis_points
    if data[o] == 1:  # creators Option<Vec<Creator>>
        n = struct.unpack_from("<I", data, o + 1)[0]; o = o + 1 + 4 + n * 34
    else:
        o += 1
    o += 1 + 1  # primary_sale_happened + is_mutable
    o = o + 2 if data[o] == 1 else o + 1  # edition_nonce Option<u8>
    if data[o] == 1:  # token_standard Some
        return data[o + 1]
    return None


async def detect_standard(rpc_url: str, mint: str) -> str:
    """Return 'pnft' | 'standard' | 'cnft' | 'core' | 'unknown'."""
    info = await _get_account(rpc_url, mint)
    if info is None:
        return "cnft"  # no mint account → compressed NFT (lives in a Merkle tree)
    if info.get("owner") == MPL_CORE_PROGRAM:
        return "core"
    if info.get("owner") != str(TOKEN_PROGRAM):
        return "unknown"
    # Classic SPL mint → inspect metadata token_standard field
    meta = await _get_account(rpc_url, str(metadata_pda(Pubkey.from_string(mint))))
    if meta is None:
        return "standard"
    raw = base64.b64decode(meta["data"][0])
    return "pnft" if _token_standard(raw) == 4 else "standard"


async def build_transfer(rpc_url: str, escrow: str, winner: str, mint: str, blockhash: str,
                         *, fee_payer: Optional[str] = None) -> str:
    """Dispatch to the correct builder; raise UnsupportedNftStandard for unsupported standards.
    `fee_payer` (optional) sponsors the tx (operator pays gas + dest-ATA rent); default = owner."""
    std = await detect_standard(rpc_url, mint)
    if std == "pnft":
        meta = await _get_account(rpc_url, str(metadata_pda(Pubkey.from_string(mint))))
        ruleset = read_pnft_ruleset(base64.b64decode(meta["data"][0]))
        if ruleset is None:
            raise UnsupportedNftStandard("pnft with no ruleset is not supported in v1")
        return build_pnft_transfer(escrow, winner, mint, blockhash, ruleset=str(ruleset), fee_payer=fee_payer)
    if std == "standard":
        from app.services.solana_tx import build_nft_transfer
        return build_nft_transfer(escrow, winner, mint, blockhash, fee_payer=fee_payer)
    if std == "core":
        info = await _get_account(rpc_url, mint)
        coll = read_core_collection(base64.b64decode(info["data"][0])) if info else None
        return build_core_transfer(escrow, winner, mint, blockhash,
                                   collection=str(coll) if coll else None, fee_payer=fee_payer)
    if std == "cnft":
        return await resolve_cnft_transfer(rpc_url, escrow, winner, mint, blockhash,
                                           fee_payer=fee_payer)
    raise UnsupportedNftStandard(f"standard={std!r} is not supported")


async def nft_in_owner(rpc_url: str, owner: str, mint: str) -> bool:
    """True iff `owner` holds `mint` on-chain, sea cual sea el estándar.

    Los Metaplex Core NO tienen token account: son una cuenta única del programa Core que guarda
    su dueño dentro. Preguntando solo por token accounts (como hacía esto antes) un Core es
    invisible por mucho que se sondee, así que el settle esperaba en balde y daba la carta por no
    entregable — dejándola atrapada en el escrow aunque estuviera justo ahí.

    Un cNFT comprimido tiene el mismo problema pero peor: no tiene NI cuenta propia, así que ni
    `getAccountInfo` ni las token accounts lo ven. Se pregunta por DAS. Medido en devnet: 28 cartas
    daban "no está en el escrow" cuando su dueño ERA el escrow.

    Se pregunta con commitment `confirmed`, no con el que traiga el RPC por defecto (`finalized`):
    esto es siempre un "¿ya ha llegado?" sobre una carta recién movida, y con finalized la respuesta
    tarda un puñado de bloques de más en cambiar. Confirmed cuenta los votos de la supermayoría —
    para una transferencia de NFT ya entregada es lo bastante firme, y llega antes.
    """
    info = await _get_account(rpc_url, mint, commitment="confirmed")
    if info is not None and info.get("owner") == MPL_CORE_PROGRAM:
        holder = read_core_owner(base64.b64decode(info["data"][0]))
        return holder is not None and str(holder) == owner

    if info is None:
        # Sin cuenta: o es un cNFT, o el mint no existe. DAS lo distingue. Si no se puede preguntar
        # se cae al camino de abajo, que dirá False — el comportamiento de antes, no peor. Aquí sí
        # se traga el DasUnavailable a propósito: esto es un "¿la tiene?" y la respuesta honesta
        # cuando no se sabe es "no me consta", que es lo que el sondeo del settle espera.
        try:
            asset = await das_get_asset(rpc_url, mint)
        except DasUnavailable:
            asset = None
        if asset is not None:
            return (asset.get("ownership") or {}).get("owner") == owner

    # SPL / Token-2022: el saldo vive en una token account del dueño.
    async with httpx.AsyncClient() as c:
        r = await c.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
                                        "params": [owner, {"mint": mint},
                                                   {"encoding": "jsonParsed", "commitment": "confirmed"}]}, timeout=20)
        r.raise_for_status()
        # `.get("value", [])` NO protege de un null: si la clave existe con valor nulo devuelve
        # None y el for revienta. Un RPC que responda value:null tumbaría el settle.
        for a in ((r.json().get("result") or {}).get("value") or []):
            amt = a["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmountString"]
            if amt and float(amt) >= 1:
                return True
    return False


async def submit_signed_tx(rpc_url: str, signed_tx_b64: str) -> str:
    """POST sendTransaction; raise on RPC error; return the transaction signature."""
    async with httpx.AsyncClient() as c:
        r = await c.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                  "params": [signed_tx_b64, {"encoding": "base64"}]},
            timeout=30,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("error"):
            raise RuntimeError(f"sendTransaction failed: {d['error']}")
        return d["result"]
