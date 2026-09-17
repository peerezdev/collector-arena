from __future__ import annotations

import asyncio
import base64
import logging
import math
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from fastapi import FastAPI, Depends, Header, HTTPException, Path, Request, Query, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from . import log_redaccion
from .config import get_settings, avisar_precios_raros
from .db import make_engine, make_session_factory, init_db
from .privy import PrivyVerifier, PrivyAuthError
from .chain.base import ChainSource
from .chain.mock import MockChainSource
from .services.users import (
    read_unseen_battles, mark_battles_seen,
    get_or_create_user, read_user_view, read_user_stats, read_user_battles, set_alias,
    set_withdraw_address, leaderboard, history, AliasTakenError, buscar_usuarios,
)
from .services.matches import register_match, list_open, sync_match, MatchError
from .services.referrals import apply_referral_code, ReferralError
from .elo import gap_label
from .services.gacha import GachaService, GachaDisabled, GachaUpstreamError, tiradas_gratis
from .services import pool_ingest, tracker_access, tracker_pass, winners_ingest, winners_store
from .services.ev_view import fila_ev
from .services.tier_gaps import rachas_por_tier
from .services.privy_signer import PrivySigner, PrivyNoVerificable
from .services import escrow_pool, machine_visibility
from .models import (GachaPack, PackBattle, BattlePlayer, BattlePack, BattlePull, Tip, User,
                     TrackerPass, AirdropClaim)
from .chat import (ConnectionManager, ChatBuffer, abbreviate, save_chat_message,
                   recent_chat_messages, big_hit_multiple)
from .services.pack_lobby import (
    create_battle, join_battle, join_event,
    list_open as lobby_list_open,
    list_battles as lobby_list_battles,
    get_battle, cancel_battle, verification, LobbyError,
)
from .services.pack_orchestration import (
    run_pack_battle_live, resume_pack_battle_live, run_royale_live, resume_royale_live,
    usdc_balance_base_units, fetch_latest_blockhash,
    reconcile_voided_battle_live,
)
from .services.solana_tx import build_memo_tx, build_free_pack_proof_tx, confirmar_firma, leer_firma
from .services.royale_funding import (royale_buyin, collect_buyin, construir_y_firmar_cobro,
                                      enviar_cobro, distribute_usdc, refund_buyin, withdraw_usdc,
                                      withdraw_usdc_with_fee)
from .services.nft_transfer import (submit_signed_tx, build_transfer, nft_in_owner,
                                    UnsupportedNftStandard, leer_cuenta)
from .services.cards_airdrop import ata, build_claim_tx, cargar_asignaciones, claim_status_pda
from solders.pubkey import Pubkey
from .services.reservations import (reserve, reserved_total, royale_locked_total,
                                     release_reservations, battle_in_progress, royale_in_progress)
from .services import emotes as emote_service
from .services.bots import load_bots, pick_bot

logger = logging.getLogger(__name__)


async def _airdrop_cuenta(rpc_url: str, pubkey: str):
    """¿Existe esta cuenta en la cadena? Devuelve la cuenta o None.

    Vive AQUÍ y no dentro de `create_app` a propósito: un closure no se puede sustituir
    desde un test, y esta es la única llamada a la red de todo el airdrop. A nivel de
    módulo, `monkeypatch.setattr("app.main._airdrop_cuenta", ...)` funciona porque
    Python resuelve el global en el momento de la llamada.
    """
    return await leer_cuenta(rpc_url, pubkey)


# Tope de menciones por mensaje. Con la lista de conectados en la mano, sin tope bastaría un
# mensaje para avisar a toda la sala: un `@todos` que nadie ha decidido ofrecer.
MAX_MENCIONES = 5


def _menciones_validas(crudas, conectados) -> list[dict]:
    """Menciones que el servidor acepta de un mensaje de chat.

    Se filtran contra QUIÉN ESTÁ CONECTADO en este instante, porque esta lista la manda el
    cliente: sin filtro, cualquiera podría mencionar a media base de usuarios a mano, o a gente
    desconectada que no se enteraría. Lo que no pasa se descarta en silencio y el mensaje se envía
    igual: una mención mal puesta no es motivo para tragarse lo que el jugador escribió.

    Vive a nivel de módulo, fuera de `create_app`, para poder probarla sin montar la aplicación.
    """
    permitidas = {u["wallet"] for u in conectados}
    out: list[dict] = []
    for m in crudas or []:
        if not isinstance(m, dict):
            continue
        w, label = m.get("wallet"), m.get("label")
        if w in permitidas and isinstance(label, str) and label:
            out.append({"wallet": w, "label": label[:40]})
        if len(out) >= MAX_MENCIONES:
            break
    return out


def _configure_app_logging() -> None:
    """Make app.* logs (INFO+) visible on stderr. Uvicorn configures only its own loggers, so
    without this the engine's warnings (e.g. an undelivered card at settle) never surface — which
    is exactly what hid the cNFT settle failure. Idempotent. Left to propagate so pytest's caplog
    still sees these records (uvicorn adds no root handler, so there is no double line)."""
    applog = logging.getLogger("app")
    if applog.handlers:
        return
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    applog.addHandler(h)
    applog.setLevel(logging.INFO)

# Live Drops are broadcast to everyone with a delay so a drop never spoils the
# opener's own reveal (the delay applies to the opener too).
LIVE_DROP_DELAY_S = 30


class AliasBody(BaseModel):
    alias: str = Field(min_length=3, max_length=20, pattern=r"^[A-Za-z0-9_]+$")


class ReferralBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class WithdrawAddressBody(BaseModel):
    # Base58 Solana address (32–44 chars). Empty string clears it.
    address: str = Field(max_length=64, pattern=r"^$|^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class WithdrawBody(BaseModel):
    address: str = Field(pattern=r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # destination Solana wallet
    amount: float = Field(gt=0)  # USDC (dollars), o CARDS si token="cards"
    # Qué token retirar. Literal ya deja que FastAPI/Pydantic devuelvan 422 solos ante
    # cualquier otro valor, sin necesidad de comprobarlo a mano en el endpoint.
    token: Literal["usdc", "cards"] = "usdc"


class TipBody(BaseModel):
    to: str
    amount: float = Field(gt=0, allow_inf_nan=False)  # USDC (dollars); NaN/Infinity rompían el round()
    source: str = "profile"     # "profile" | "chat"; solo para el historial


class NftWithdrawBody(BaseModel):
    # Transfer an NFT out of the player's embedded wallet to an EXTERNAL Solana address.
    nft_address: str = Field(pattern=r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # mint to send
    address: str = Field(pattern=r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")      # destination Solana wallet


class EmoteSlotsBody(BaseModel):
    slots: list[str] = Field(max_length=8)  # up to 3 kept; codes not owned are dropped server-side


class EmoteThrowBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class SignTxBody(BaseModel):
    transaction: str = Field(min_length=1, max_length=8192)  # base64 (partially-)unsigned tx


class GeneratePackBody(BaseModel):
    pack_type: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_]+$")


class TrackerPassBody(BaseModel):
    days: int


class DevAnnounceBody(BaseModel):
    """DEV/TEST: fire a sample chat announcement so the render can be iterated without
    waiting for a real hit/winner/created event. Broadcast-only by default (persist=False)
    so it never pollutes the persisted history."""
    text: str = Field(default="", max_length=200)
    user: str = Field(default="📢 Arena", max_length=64)
    event: Optional[str] = Field(default=None, pattern=r"^(hit|winner|created)$")
    amountUsd: Optional[float] = None
    mode: Optional[str] = Field(default=None, pattern=r"^(pack|royale)$")
    machine: Optional[str] = Field(default=None, max_length=64)   # gacha machine name (hit events)
    mult: Optional[float] = None                                  # hit multiple, e.g. 10.0 → "(x10)"
    action_label: Optional[str] = Field(default=None, max_length=24)
    battle_id: str = Field(default="demo", max_length=64)
    persist: bool = False


class MarkRevealedBody(BaseModel):
    memos: list[str] = Field(max_length=50)


class MarkSeenBody(BaseModel):
    battle_ids: list[str] = Field(max_length=50)


class SubmitTxBody(BaseModel):
    signed_transaction: str = Field(min_length=1, max_length=3000)
    # Memo del sobre que se está pagando, cuando la tx es la compra de un sobre. Opcional porque
    # esta ruta también envía buybacks y transferencias, que no tienen memo asociado.
    memo: Optional[str] = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def check_base64(self) -> "SubmitTxBody":
        try:
            base64.b64decode(self.signed_transaction, validate=True)
        except Exception:
            raise ValueError("signed_transaction must be valid base64")
        return self


class OpenPackBody(BaseModel):
    memo: str = Field(min_length=1, max_length=128)


class YoloBody(BaseModel):
    pack_type: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_]+$")
    count: int = Field(ge=1, le=10)
    turbo: bool = False


class BuybackBody(BaseModel):
    nft_address: str


class CreateMatchBody(BaseModel):
    battle_pubkey: str = Field(min_length=32, max_length=44)
    min_elo: Optional[int] = Field(default=None, ge=0, le=9999)
    max_elo: Optional[int] = Field(default=None, ge=0, le=9999)

    @model_validator(mode="after")
    def check_elo_range(self) -> "CreateMatchBody":
        if self.min_elo is not None and self.max_elo is not None:
            if self.min_elo > self.max_elo:
                raise ValueError("min_elo cannot be greater than max_elo")
        return self


class PackSel(BaseModel):
    machine_code: str
    count: int

class CreateBattleBody(BaseModel):
    machine_code: Optional[str] = None     # legacy single-pack / royale
    max_players: int
    mode: str = "pack"
    packs: Optional[list[PackSel]] = None  # multi-pack bundle (pack mode only)


def create_app(session_factory, chain: ChainSource,
               elo_start: int = 1200, elo_k: int = 32,
               cors_origins: list[str] | None = None,
               gacha: GachaService | None = None,
               gacha_rate_limit: int = 10,
               # Apagado por defecto: los tests construyen la app con un gacha de mentira y no deben
               # abrir un socket a producción. El arranque real lo enciende explícitamente.
               ev_tracker_enabled: bool = False,
               privy: PrivyVerifier | None = None,
               privy_signer: PrivySigner | None = None,
               solana_rpc_url: str = "",
               cc_usdc_mint: str = "",
               privy_operator_wallet_id: str = "",
               privy_operator_address: str = "",
               cards_airdrop: dict | None = None,
               cards_airdrop_distributor: str = "",
               cards_airdrop_vault: str = "",
               cards_airdrop_mint: str = "",
               cards_airdrop_round: str = "",
               escrow_seed_lamports: int = 10_000_000,
               dev_endpoints_enabled: bool = False,
               min_withdraw_usdc: float = 1.0,
               min_withdraw_cards: float = 1.0,
               tips_enabled: bool = False,
               min_tip_usdc: float = 1.0,
               tip_rate_limit: int = 10,
               tip_rate_window_s: float = 60.0,
               withdraw_rate_limit: int = 5,
               withdraw_rate_window_s: float = 60.0,
               withdraw_fee_pct: float = 0.0,
               fee_wallet_address: str = "",
               hit_announce_mult: float = 3.0,
               winner_announce_mult: float = 4.0,
               royale_creator_allowlist: set[str] | None = None,
               tracker_access_allowlist: set[str] | None = None,
               tracker_pass_7d_usdc: float = 0.0,
               tracker_pass_30d_usdc: float = 0.0,
               tracker_pass_rate_limit: int = 5,
               tracker_pass_rate_window_s: float = 60.0,
               referral_payout_wallet_id: str = "",
               referral_payout_address: str = "",
               referral_claim_min_base_units: int = 5_000_000) -> FastAPI:
    # Antes de que se sirva la primera petición: el log de acceso de uvicorn escribe la URL
    # entera, y el token del chat viaja en la query string. Ver app/log_redaccion.py.
    log_redaccion.instalar()

    app = FastAPI(title="Battle Arena — Backend")

    def _json_safe(value):
        # NaN/Infinity/-Infinity son floats válidos en Python pero JSON no los admite; Starlette
        # serializa las respuestas con allow_nan=False. Pydantic, al rechazar uno de estos
        # valores (p.ej. un Field con allow_inf_nan=False), lo deja TAL CUAL en el "input" del
        # error de validación — así que sin este saneo el propio manejador de errores de FastAPI
        # revienta al construir el 422, y lo que el cliente ve es un 500.
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        if isinstance(value, dict):
            return {k: _json_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_json_safe(v) for v in value]
        return value

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content=_json_safe(jsonable_encoder({"detail": exc.errors()})))

    # Wallets allowed to CREATE Battle Royale (empty = open to all). Captured by the
    # /pack-battles handler below. See docs/superpowers/specs/2026-07-17-royale-create-allowlist-design.md.
    _royale_allow: set[str] = set(royale_creator_allowlist or ())
    # Copia propia, como la de arriba: quien construye la app no puede cambiar la lista después
    # pasándole otro conjunto por referencia.
    _tracker_allow: set[str] = set(tracker_access_allowlist or ())

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def db() -> Session:
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    def current_user(authorization: Optional[str] = Header(None)) -> str:
        if privy is None:
            raise HTTPException(503, "privy not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing token")
        try:
            return privy.embedded_solana_wallet(authorization[len("Bearer "):])
        except PrivyAuthError as e:
            # El MOTIVO viaja en el detalle, y no es un descuido de seguridad: "el token no
            # verifica" y "el token verifica pero no trae wallet embebida" son dos problemas con
            # dos soluciones distintas, y sin distinguirlos el jugador solo ve "vuelve a entrar",
            # que en el segundo caso no arregla nada. Ninguno de los motivos revela nada del token.
            logger.warning("auth rechazada (%s): %s", type(e).__name__, e)
            raise HTTPException(401, f"invalid identity token: {e}")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/users/me/alias")
    async def me_alias(body: AliasBody, wallet: str = Depends(current_user), s: Session = Depends(db)):
        get_or_create_user(s, wallet, elo_start)
        try:
            set_alias(s, wallet, body.alias)
            s.commit()
        except AliasTakenError:
            raise HTTPException(409, "username_taken")
        except IntegrityError:
            s.rollback()
            raise HTTPException(409, "username_taken")
        return {"wallet": wallet, "alias": body.alias}

    @app.get("/users/me/balance")
    async def me_balance(wallet: str = Depends(current_user), s: Session = Depends(db)):
        # `reserved` = pack-battle soft holds (still in the wallet) → drives available balance.
        # `locked_royale` = royale buy-ins already collected on-chain to escrow → display only,
        # so the "reserved" UI reflects every open battle without double-debiting available.
        return {"reserved": reserved_total(s, wallet), "locked_royale": royale_locked_total(s, wallet)}

    @app.get("/users/me/usdc")
    async def me_usdc(wallet: str = Depends(current_user)):
        # Raw on-chain USDC balance of the caller's embedded wallet, read server-side.
        # The browser cannot query the RPC directly on mainnet: the public endpoint
        # (api.mainnet-beta.solana.com) returns 403 to browser Origins, and pointing the
        # client at the wrong-network mint returns nothing. The backend has no Origin header
        # and already holds the per-network rpc_url + usdc mint, so it reads the balance
        # reliably for both devnet and mainnet. `reserved` (soft holds) is exposed separately
        # by /users/me/balance and subtracted client-side, matching the previous behavior.
        base_units = await usdc_balance_base_units(solana_rpc_url, wallet, cc_usdc_mint)
        return {"base_units": base_units, "usdc": base_units / 1e6}

    @app.get("/users/me/cards")
    async def me_cards(wallet: str = Depends(current_user)):
        # Igual que /users/me/usdc y por la misma razón (el browser no puede leer el RPC de
        # mainnet directamente: el endpoint público devuelve 403 a los Origins de navegador, y
        # apuntar el cliente a la red equivocada no devuelve nada). Aquí se lee el mint del
        # airdrop $CARDS en vez del de USDC; usdc_balance_base_units ya toma el mint como
        # parámetro, así que sirve tal cual (ver services/pack_orchestration.py).
        if not cards_airdrop_mint:
            raise HTTPException(503, "cards_unavailable")
        base_units = await usdc_balance_base_units(solana_rpc_url, wallet, cards_airdrop_mint)
        return {"base_units": base_units, "cards": base_units / 1e6}

    @app.get("/users/search")
    def users_search(q: str = "", limit: int = 8, wallet: str = Depends(current_user),
                     s: Session = Depends(db)):
        """Jugadores cuyo alias o wallet empieza por `q`, para el autocompletado de `/tip`.

        `def` y NO `async def`: con la base síncrona, un `async def` que consulta bloquea el bucle
        de eventos y deja el proceso sin atender nada, ni /health. Así FastAPI lo ejecuta en su
        pool de hilos. Ver el spec.

        Con sesión, a diferencia de `GET /users/{wallet}`: aquella devuelve UNA fila que ya
        conoces, esta ejecuta una búsqueda, y abierta es una consulta al alcance de cualquiera.

        Declarado ANTES que `/users/{wallet}`: si fuera después, FastAPI casaría "search" como si
        fuera una wallet y este endpoint no se alcanzaría nunca.

        Los conectados van SIEMPRE primero y salen aunque no tengan alias: se filtran en memoria
        contra `_chat_mgr.online_users()` ANTES de recortar a `limit`, no después. Ordenar
        después del recorte no basta: si la página ya se llenó con `limit` alias que preceden al
        conectado en el alfabeto, un `.sort()` posterior ya no tiene sitio donde meterlo y se
        queda fuera. Tampoco cuesta consulta: la presencia ya está en memoria.
        """
        _search_throttle(wallet)
        limit = max(1, min(limit, 8))
        q_norm = q.strip().lower()

        conectados = [
            u for u in _chat_mgr.online_users()
            if not q_norm or u["wallet"].lower().startswith(q_norm)
            or (u.get("name") or "").lower().startswith(q_norm)
        ][:limit]
        de_conectados = [{"wallet": u["wallet"], "alias": u.get("name"), "online": True}
                         for u in conectados]
        vistos = {u["wallet"] for u in de_conectados}

        restantes = limit - len(de_conectados)
        resto = []
        if restantes > 0:
            # `buscar_usuarios` excluye a quien no tiene alias cuando `q` está vacía (ver su
            # docstring): no es un problema aquí porque a esos ya los hemos puesto arriba si
            # estaban conectados, y si no lo están, no son un destino de propina alcanzable.
            encontrados = buscar_usuarios(s, q.strip(), limit)
            resto = [{**u, "online": False} for u in encontrados
                     if u["wallet"] not in vistos][:restantes]

        return de_conectados + resto

    @app.get("/users/{wallet}")
    async def get_user(wallet: str, s: Session = Depends(db)):
        return read_user_view(s, wallet, elo_start)

    @app.get("/users/{wallet}/stats")
    async def get_user_stats(wallet: str, s: Session = Depends(db)):
        return read_user_stats(s, wallet)

    @app.get("/users/{wallet}/battles")
    async def get_user_battles(wallet: str, s: Session = Depends(db)):
        return read_user_battles(s, wallet)

    @app.get("/users/me/battles/unseen")
    async def me_unseen_battles(wallet: str = Depends(current_user), s: Session = Depends(db)):
        """Batallas terminadas o anuladas en las que el jugador participó y aún no ha visto.
        Sin throttle: se consulta al entrar, no en bucle."""
        return read_unseen_battles(s, wallet)

    @app.post("/users/me/battles/seen")
    async def me_mark_battles_seen(body: MarkSeenBody, wallet: str = Depends(current_user),
                                   s: Session = Depends(db)):
        """Marca batallas como ya vistas por el jugador. Idempotente y acotado a su wallet."""
        return {"marked": mark_battles_seen(s, wallet, body.battle_ids)}

    @app.post("/users/me/withdraw-address")
    async def me_withdraw_address(body: WithdrawAddressBody, wallet: str = Depends(current_user), s: Session = Depends(db)):
        get_or_create_user(s, wallet, elo_start)
        set_withdraw_address(s, wallet, body.address or None)
        s.commit()
        return {"wallet": wallet, "withdraw_address": body.address or None}

    # ── Emotes ──────────────────────────────────────────────────────────────
    @app.get("/emotes/catalog")
    async def emotes_catalog():
        return emote_service.catalog()

    @app.get("/users/me/emotes")
    async def me_emotes(wallet: str = Depends(current_user), s: Session = Depends(db)):
        return emote_service.read_user_emotes(s, wallet)

    @app.put("/users/me/emotes/slots")
    async def me_emote_slots(body: EmoteSlotsBody, wallet: str = Depends(current_user), s: Session = Depends(db)):
        return emote_service.set_emote_slots(s, wallet, body.slots, elo_start)

    @app.get("/users/{wallet}/history")
    async def get_history(wallet: str, s: Session = Depends(db)):
        return [{"battle_pubkey": h.battle_pubkey, "elo_before": h.elo_before,
                 "elo_after": h.elo_after, "result": h.result} for h in history(s, wallet)]

    @app.post("/matches")
    async def post_match(body: CreateMatchBody, wallet: str = Depends(current_user), s: Session = Depends(db)):
        try:
            m = await register_match(s, chain, creator=wallet, battle_pubkey=body.battle_pubkey,
                                     min_elo=body.min_elo, max_elo=body.max_elo, elo_start=elo_start)
        except MatchError as e:
            raise HTTPException(409, str(e))
        s.commit()
        return {"battle_pubkey": m.battle_pubkey, "status": m.status, "stake": m.stake,
                "min_elo": m.min_elo, "max_elo": m.max_elo}

    @app.get("/matches/open")
    async def get_open(viewer: Optional[str] = None, s: Session = Depends(db)):
        rows = list_open(s, viewer=viewer)
        return rows

    @app.post("/matches/{battle_pubkey}/sync")
    async def post_sync(battle_pubkey: str, wallet: str = Depends(current_user), s: Session = Depends(db)):
        try:
            m = await sync_match(s, chain, battle_pubkey, elo_start=elo_start, k=elo_k)
        except MatchError as e:
            raise HTTPException(404, str(e))
        s.commit()
        return {"battle_pubkey": m.battle_pubkey, "status": m.status, "winner": m.winner,
                "is_draw": m.is_draw, "elo_applied": m.elo_applied}

    @app.get("/elo/compare")
    async def elo_compare(a: str, b: str, s: Session = Depends(db)):
        va = read_user_view(s, a, elo_start)["elo"]
        vb = read_user_view(s, b, elo_start)["elo"]
        diff = va - vb
        return {"elo_a": va, "elo_b": vb, "diff": diff, "gap_label": gap_label(diff)}

    @app.post("/users/{wallet}/referral")
    async def post_referral(wallet: str, body: ReferralBody,
                            authed: str = Depends(current_user), s: Session = Depends(db)):
        if wallet != authed:
            raise HTTPException(403, "wallet mismatch")
        get_or_create_user(s, authed, elo_start)
        try:
            out = apply_referral_code(s, authed, body.code)
            s.commit()
        except ReferralError as e:
            s.rollback()
            raise HTTPException(409, str(e))
        return out

    # ── Panel del referidor: rev-share del rake ─────────────────────────────
    # Un claim en vuelo por wallet: dos pulsaciones seguidas no pueden pagar dos veces.
    _claim_locks: set = set()

    @app.get("/users/me/referrer")
    async def me_referrer(wallet: str = Depends(current_user), s: Session = Depends(db)):
        """Panel del referidor. Sin códigos en propiedad devuelve ceros, no 404: el frontend
        decide con esto si enseña el panel."""
        from .services.referral_earnings import referrer_summary
        out = referrer_summary(s, wallet)
        out["claim_min_base_units"] = referral_claim_min_base_units
        return out

    @app.post("/users/me/referrer/claim")
    async def me_referrer_claim(wallet: str = Depends(current_user), s: Session = Depends(db)):
        """Paga el rev-share pendiente desde la wallet de payouts.

        Esa wallet es la misma a la que aterriza el rake y está POR DECIDIR: mientras no se
        configure, esto responde 503 en vez de tirar del operador. El operador ya paga el rent de
        cada carta y la siembra de escrows; hacerlo además la caja de los referidos lo convertiría
        en punto único de fallo de tres cosas a la vez.
        """
        from .services.referral_earnings import (claim_earnings, mark_payout_failed,
                                                 mark_payout_sent, referrer_summary)
        pending = referrer_summary(s, wallet)["unclaimed_base_units"]
        if pending < referral_claim_min_base_units:
            raise HTTPException(409, "below_minimum")
        if not (referral_payout_wallet_id and referral_payout_address):
            raise HTTPException(503, "payouts_unavailable")
        if wallet in _claim_locks:
            raise HTTPException(409, "claim_in_progress")
        _claim_locks.add(wallet)
        try:
            payout, earning_ids = claim_earnings(s, wallet)
            if payout is None:
                raise HTTPException(409, "nothing_to_claim")
            s.commit()
            try:
                bh = await fetch_latest_blockhash(solana_rpc_url)
                # withdraw_usdc crea el ATA destino de forma idempotente (el pagador cubre la
                # renta): un referidor puede no tener cuenta USDC todavía. La wallet de payouts va
                # como origen Y como fee-payer; al ser el mismo firmante, la segunda firma es un no-op.
                sig = await withdraw_usdc(
                    solana_rpc_url, privy_signer,
                    referral_payout_wallet_id, referral_payout_address,   # origen del dinero
                    referral_payout_wallet_id, referral_payout_address,   # fee-payer
                    wallet, cc_usdc_mint, payout.amount_base_units, bh)
            except Exception as exc:
                mark_payout_failed(s, payout)
                logger.error("rev-share: claim de %s falló: %s", wallet, exc)
                raise HTTPException(502, "payout_failed")
            mark_payout_sent(s, payout, earning_ids, sig)
            return {"signature": sig, "amount_base_units": payout.amount_base_units}
        finally:
            _claim_locks.discard(wallet)

    @app.get("/leaderboard")
    async def get_leaderboard(limit: int = Query(default=50, ge=1, le=200), s: Session = Depends(db)):
        return [{"wallet": u.wallet, "alias": u.alias, "gimmighouls": u.gimmighouls, "elo": u.elo}
                for u in leaderboard(s, limit)]

    # ── Gacha (proxy a Collector Crypt; la x-api-key vive solo aquí) ─────────
    _gacha_hits: dict[str, list[float]] = {}

    def _gacha_throttle(wallet: str) -> None:
        now = _time.time()
        hits = [t for t in _gacha_hits.get(wallet, []) if now - t < 60.0]
        if len(hits) >= gacha_rate_limit:
            raise HTTPException(429, "too many gacha requests")
        hits.append(now)
        _gacha_hits[wallet] = hits

    def _gacha_or_503() -> GachaService:
        if gacha is None or not gacha.enabled:
            raise HTTPException(503, "gacha_disabled")
        return gacha

    # El replay es público (no hay wallet a la que cobrarle el límite), así que se limita por IP.
    # Sin esto seríamos un proxy gratis a Collector Crypt: una llamada suya por cada visita.
    _replay_hits: dict[str, list[float]] = {}

    def _replay_throttle(ip: str) -> None:
        now = _time.time()
        hits = [t for t in _replay_hits.get(ip, []) if now - t < 60.0]
        if len(hits) >= gacha_rate_limit:
            raise HTTPException(429, "too many replay requests")
        hits.append(now)
        _replay_hits[ip] = hits

    # ── ¿Es suya la carta que quiere vender? ─────────────────────────────────
    # La respuesta honesta la tiene la cadena, pero llega TARDE: la carta acaba de aterrizar en la
    # wallet y ni el RPC ni el índice de DAS la ven todavía. Medido en mainnet: hasta 5 s de "no
    # eres dueño de este NFT" justo después de una tirada — o sea, exactamente en el momento en el
    # que el jugador tiene delante el botón de vender. Un "todavía no lo veo" NO es un "no es
    # tuya", y tratarlos igual es lo que convirtió una comprobación de seguridad en un error de
    # cara al usuario.
    #
    # Se responde en dos tiempos:
    #   1. Nuestro propio libro. Si somos NOSOTROS quienes acabamos de entregarle esa carta a esa
    #      wallet —un sobre que abrimos para él, o el botín de una partida que ganó— ya sabemos de
    #      quién es sin preguntarle a nadie. Instantáneo y sin depender de índices ajenos.
    #   2. Si el libro no dice nada (carta vieja, o llegada de fuera), se pregunta a la cadena con
    #      reintentos cortos antes de dar un 403.
    #
    # El libro solo vale un rato (_LEDGER_TTL): pasada la ventana de indexado ya no aporta nada
    # —la cadena responde bien— y así el permiso no sobrevive a que la carta cambie de manos.
    _LEDGER_TTL = timedelta(minutes=15)
    # Cartas que han SALIDO de esa wallet por una vía nuestra: un retiro a una dirección externa, o
    # un buyback que ya le hemos construido. El libro dice "se la entregamos", no "sigue siendo
    # suya", así que en cuanto la movemos el atajo tiene que morir para esa carta.
    #
    # El caso que lo obliga: el jugador vende la carta, CC la recompra y la devuelve a la máquina,
    # y a los diez minutos le toca a OTRO. Si el primero volviera a pedir el buyback, su fila del
    # libro seguiría diciendo "es suya" y le construiríamos la venta de una carta que ya es de un
    # tercero — apoyándonos en que CC lo rechace, que es justo la dependencia de terceros que la
    # comprobación de propiedad venía a quitar. Quince minutos dan de sobra para esa vuelta.
    #
    # La clave es (wallet, mint), no el mint a secas: invalidar la carta del que la vendió no puede
    # quitarle el atajo al siguiente que la saque, que la acaba de recibir de verdad.
    # En memoria a propósito: un reinicio solo devuelve esa carta al camino normal —el sondeo
    # on-chain— y el atajo caduca solo a los 15 minutos, así que no hace falta persistirlo.
    _nft_moved_out: dict[tuple[str, str], float] = {}

    def _marcar_fuera(wallet: str, mint: str) -> None:
        """Esa carta ya no cuenta como recién entregada a esa wallet.

        De paso se limpian las marcas viejas. Podarlas es seguro porque el atajo se mide desde la
        ENTREGA y la salida es siempre posterior: cuando una marca cumple el TTL, la entrega que
        podría amparar ya lo había cumplido antes.
        """
        ahora = _time.time()
        ttl = _LEDGER_TTL.total_seconds()
        for k in [k for k, t in _nft_moved_out.items() if ahora - t > ttl]:
            _nft_moved_out.pop(k, None)
        _nft_moved_out[(wallet, mint)] = ahora

    def _reciente(ts: Optional[datetime]) -> bool:
        if ts is None:
            return False
        # SQLite devuelve datetimes sin tzinfo aunque la columna sea timezone=True.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts) <= _LEDGER_TTL

    def _entregada_por_nosotros(s: Session, wallet: str, mint: str) -> bool:
        """¿Consta en NUESTRO libro que acabamos de entregarle esa carta a esa wallet?"""
        if not mint or (wallet, mint) in _nft_moved_out:
            return False
        # Gacha: CC entrega la carta directamente a la wallet del jugador al abrir el sobre.
        pack = (s.query(GachaPack)
                .filter(GachaPack.wallet == wallet, GachaPack.nft_address == mint)
                .first())
        if pack is not None and not pack.auto_sold and _reciente(pack.opened_at):
            return True
        # Batallas: el botín va del escrow al ganador, y `transferred` marca que ese envío salió.
        won = (s.query(BattlePull, PackBattle)
               .join(PackBattle, PackBattle.id == BattlePull.battle_id)
               .filter(BattlePull.nft_address == mint,
                       BattlePull.transferred.is_(True),
                       BattlePull.auto_sold.is_(False),
                       PackBattle.winner == wallet)
               .first())
        return won is not None and _reciente(won[1].settled_at)

    async def _owns_onchain(wallet: str, mint: str, *, attempts: int = 4) -> bool:
        """`nft_in_owner` con reintentos cortos (~1,75 s en total).

        Un solo sondeo confunde "el índice va con retraso" con "no es suya". Solo se reintenta el
        NO: en cuanto la cadena confirma que es suya, se sale. Si todos los intentos revientan, el
        fallo sube y la venta se corta con un 502 — ante la duda no se vende.
        """
        delay, last_exc = 0.25, None
        for i in range(attempts):
            try:
                if await nft_in_owner(solana_rpc_url, wallet, mint):
                    return True
                last_exc = None
            except Exception as exc:      # noqa: BLE001 — se reintenta y, si persiste, se relanza
                last_exc = exc
            if i + 1 < attempts:
                await asyncio.sleep(delay)
                delay *= 2
        if last_exc is not None:
            raise last_exc
        return False

    @app.get("/gacha/machines")
    async def gacha_machines(s: Session = Depends(db)):
        svc = _gacha_or_503()
        try:
            return machine_visibility.visible(s, await svc.machines())
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")

    @app.get("/gacha/winners")
    async def gacha_winners(machine: Optional[str] = None,
                            rarity: Optional[str] = None,
                            count: int = Query(default=10, ge=1, le=200)):
        """Últimos ganadores del gacha de toda la plataforma.

        `count` llega a 200 como mucho porque ese es el techo de CC. Epic se pide con el filtro
        propio de la API — solo 1 de cada 100 tiradas lo es, así que traer una página y quedarse con
        los Epic devolvería dos o tres. Las demás rarezas se recortan aquí, y por eso pueden salir
        MENOS de `count`: es el precio de que upstream no las filtre.
        """
        svc = _gacha_or_503()
        pedida = (rarity or "").strip().lower()
        try:
            filas = await svc.winners(pack_type=machine, count=count,
                                      epic_only=(pedida == "epic"))
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")
        if pedida and pedida != "epic":
            filas = [w for w in filas if (w.get("rarity") or "").lower() == pedida]
        return filas

    @app.get("/gacha/winners/gaps")
    async def gacha_rarity_gaps(machine: str):
        """Cuántas tiradas lleva cada rareza sin salir en una máquina.

        Siempre sobre las 200 últimas de ESA máquina y SIN filtrar por rareza: el hueco es una
        posición dentro del feed, así que recortarlo por rareza o por el `count` de la pantalla
        daría un número que no significa nada.

        Una rareza que no aparece en las 200 vuelve como null, no como 200: no se ha medido su
        hueco, solo se sabe que es mayor que la muestra.
        """
        svc = _gacha_or_503()
        try:
            filas = await svc.winners(pack_type=machine, count=200)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")
        from .services.rarity_gaps import gaps
        return {"machine": machine, "sampled": len(filas), "gaps": gaps(filas)}

    @app.get("/gacha/machines/{code}/cards")
    async def gacha_machine_cards(code: str,
                                  rarity: Optional[str] = None,
                                  page: int = Query(default=1, ge=1, le=1000),
                                  limit: int = Query(default=24, ge=1, le=100)):
        svc = _gacha_or_503()
        try:
            return await svc.get_nfts(code=code, rarity=rarity, page=page, limit=limit)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")

    @app.get("/gacha/nft/{mint}")
    async def gacha_nft(mint: str = Path(min_length=32, max_length=44,
                                         pattern=r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")):
        # Per-mint card metadata for the inventory modal. The mint is strictly validated (base58)
        # before it's interpolated into the upstream URL, so it can't be used for path traversal/SSRF.
        svc = _gacha_or_503()
        try:
            return await svc.nft_metadata(mint)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")

    @app.post("/gacha/generate-pack")
    async def gacha_generate(body: GeneratePackBody,
                             wallet: str = Depends(current_user),
                             s: Session = Depends(db)):
        svc = _gacha_or_503()
        _gacha_throttle(wallet)
        price = await _machine_price(body.pack_type)
        await _require_available(wallet, price, s)
        try:
            out = await svc.generate_pack(player_address=wallet, pack_type=body.pack_type)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")
        if not out.get("memo"):
            raise HTTPException(502, "gacha upstream unavailable")
        existing = s.get(GachaPack, out["memo"])
        if existing is not None:
            if existing.wallet != wallet:
                raise HTTPException(502, "gacha upstream unavailable")
            # mismo wallet: el pack ya existe, devolver sin re-insertar
        else:
            s.add(GachaPack(memo=out["memo"], wallet=wallet, pack_type=body.pack_type))
            s.commit()
        return out

    @app.get("/users/me/free-spins")
    async def me_free_spins(wallet: str = Depends(current_user)):
        """Cuántas tiradas gratis tiene el jugador y cuánto le falta para la siguiente.

        Los puntos los lleva Collector Crypt, no nosotros: se le preguntan a él en cada consulta.
        """
        svc = _gacha_or_503()
        try:
            return await svc.free_spins(wallet)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")

    _airdrop = cards_airdrop or {}

    def _airdrop_o_503() -> None:
        """Apagado y averiado se responden igual, con 503, y por la misma razón: en
        ninguno de los dos casos sabemos si el jugador es elegible."""
        if not (_airdrop and cards_airdrop_distributor and cards_airdrop_vault
                and cards_airdrop_mint and cards_airdrop_round):
            raise HTTPException(503, "airdrop_unavailable")
        if not (privy_operator_wallet_id and privy_operator_address):
            raise HTTPException(503, "airdrop_unavailable")
        if privy_signer is None:
            raise HTTPException(503, "airdrop_unavailable")

    async def _ya_reclamado(index: int) -> bool:
        try:
            pda, _ = claim_status_pda(index, cards_airdrop_distributor)
        except ValueError as exc:
            # Un typo en CARDS_AIRDROP_DISTRIBUTOR: problema de configuración, no del
            # jugador ni de la cadena, así que 503 y no el 500 que se llevaba antes.
            logger.error("airdrop: pubkey de configuración inválida (distributor): %s", exc)
            raise HTTPException(503, "airdrop_unavailable")
        try:
            return await _airdrop_cuenta(solana_rpc_url, str(pda)) is not None
        except Exception:
            # Reintentable a propósito. Si el RPC no contesta no sabemos si reclamó, y
            # contestar "no" haría que le saliera el botón para reclamar dos veces. El
            # cuerpo del 502 no lleva la excepción cruda: en mainnet `solana_rpc_url`
            # lleva un ?api-key= del proveedor, y volcarla en la respuesta se la mandaría
            # al navegador del jugador. Este es el camino que se dispara en CADA carga de
            # /claim, así que es el más probable de todos de llegar a disparar esto de verdad.
            logger.exception("airdrop: check de ya-reclamado falló para index=%s", index)
            raise HTTPException(502, "airdrop check failed")

    @app.get("/users/me/airdrop/cards")
    async def me_airdrop_cards(wallet: str = Depends(current_user), s: Session = Depends(db)):
        _airdrop_o_503()
        e = _airdrop.get(wallet)
        if e is None:
            return {"eligible": False, "amount": 0, "claimed": False, "signature": None}
        reclamado = await _ya_reclamado(int(e["i"]))
        fila = s.query(AirdropClaim).filter(
            AirdropClaim.wallet == wallet, AirdropClaim.ronda == cards_airdrop_round
        ).first()
        return {"eligible": True, "amount": int(e["a"]), "claimed": reclamado,
                "signature": fila.signature if fila else None}

    @app.post("/gacha/submit-tx")
    async def gacha_submit(body: SubmitTxBody, wallet: str = Depends(current_user),
                           s: Session = Depends(db)):
        svc = _gacha_or_503()
        # NOT rate-limited: submit-tx is a mechanical follow-up of an already-throttled pull
        # initiation (generate-pack / yolo), and a YOLO of N packs calls it once per pack.
        # Throttling it here made a single multi-pack pull trip the per-wallet limit.
        try:
            out = await svc.submit_tx(signed_transaction=body.signed_transaction)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")
        # El pago acaba de cuajar: se marca el sobre como pagado. La fila de GachaPack se crea al
        # GENERAR, antes de pagar, así que `opened_at IS NULL` por sí solo no distingue un sobre
        # pagado y pendiente de uno que se generó y nunca se llegó a comprar. Sin esta marca, la
        # lista de pendientes le diría al usuario que tiene sobres que jamás pagó.
        if body.memo:
            pack = s.get(GachaPack, body.memo)
            if pack is not None and pack.wallet == wallet and pack.submitted_at is None:
                pack.submitted_at = datetime.now(timezone.utc)
                s.commit()
        return out

    @app.get("/gacha/buyback/available")
    async def gacha_buyback_available(wallet: str, nft: str):
        svc = _gacha_or_503()
        try:
            return await svc.buyback_available(wallet=wallet, nft=nft)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")

    @app.get("/gacha/packs/pending")
    async def gacha_pending_packs(wallet: str = Depends(current_user), s: Session = Depends(db)):
        """Sobres que el usuario ya PAGÓ y todavía no ha abierto.

        Sin throttle: se consulta al entrar al gacha, no en bucle.

        El filtro exige `submitted_at` además de `opened_at IS NULL`, y eso es lo que hace la
        lista honesta: la fila se crea al generar el sobre, antes de pagarlo, así que hay filas
        de tiradas que el usuario abandonó sin comprar nada. Listarlas sería decirle que tiene
        sobres —y por tanto dinero— que nunca gastó.
        """
        rows = (s.query(GachaPack)
                .filter(GachaPack.wallet == wallet,
                        GachaPack.submitted_at.isnot(None),
                        GachaPack.revealed_at.is_(None))
                .order_by(GachaPack.submitted_at)
                .limit(50)
                .all())
        # Se incluyen también los que CC ya resolvió: para el jugador siguen pendientes mientras
        # no los haya VISTO. Si nft_address ya está, el cliente no necesita volver a abrir nada —
        # reproduce el reveal con lo guardado.
        return [{"memo": p.memo, "pack_type": p.pack_type,
                 "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
                 "nft_address": p.nft_address, "name": p.name,
                 "rarity": p.rarity, "insured_value": p.insured_value,
                 "auto_sold": bool(p.auto_sold), "buyback_amount": p.buyback_amount}
                for p in rows]

    @app.post("/gacha/packs/revealed")
    async def gacha_mark_revealed(body: MarkRevealedBody,
                                  wallet: str = Depends(current_user),
                                  s: Session = Depends(db)):
        """Marca sobres como YA VISTOS por el jugador. Idempotente y acotado a su propia wallet."""
        now = datetime.now(timezone.utc)
        marked = 0
        for memo in body.memos:
            pack = s.get(GachaPack, memo)
            if pack is not None and pack.wallet == wallet and pack.revealed_at is None:
                pack.revealed_at = now
                marked += 1
        if marked:
            s.commit()
        return {"marked": marked}

    @app.post("/gacha/buyback")
    async def gacha_buyback(body: BuybackBody, wallet: str = Depends(current_user),
                            s: Session = Depends(db)):
        """Vender una carta a CC. `nft_address` lo elige el CLIENTE, así que la propiedad se
        comprueba aquí, igual que en /users/me/nft/withdraw.

        Sin esta comprobación la única barrera contra "vender la carta de otro" era que CC
        validase en su `/api/buyback` y que el asset no tuviera un transfer delegate permanente
        — dos propiedades de un tercero, fuera de nuestro control y sin aviso si cambian. Y la
        firma no protege: firmamos con la wallet delegada del que pide, así que si CC llegara a
        construir una tx que no necesita la firma del dueño real, la venta saldría. Este es el
        único punto por el que pasan TODAS las pantallas de venta (winnings, vault, inventario),
        así que basta con cerrarlo una vez.

        Un fallo del RPC deja la venta en 502 en vez de dejarla pasar: ante la duda no se vende.

        Lo que NO puede hacer es cobrarle el retraso al jugador: preguntar a la cadena por una
        carta recién entregada devuelve "no es suya" durante unos segundos. Por eso primero se mira
        nuestro propio libro (ver `_entregada_por_nosotros`), que es justo el caso de vender nada
        más abrir el sobre o ganar la partida, y solo si el libro calla se va a la cadena.

        Y una vez construida la venta, esa carta sale del atajo: a la segunda petición se le vuelve
        a preguntar a la cadena. Es el único momento en el que el atajo podría amparar una carta
        que ya cambió de manos —vendida, recomprada por CC y sacada por otro dentro de la ventana—
        y aquí sí sobra tiempo para el sondeo, porque el jugador ya no está esperando el reveal.
        """
        svc = _gacha_or_503()
        _gacha_throttle(wallet)
        if not _entregada_por_nosotros(s, wallet, body.nft_address):
            try:
                owns = await _owns_onchain(wallet, body.nft_address)
            except Exception as exc:
                raise HTTPException(502, f"ownership check failed: {exc}")
            if not owns:
                raise HTTPException(403, "you do not own this NFT")
        try:
            out = await svc.buyback(player_address=wallet, nft_address=body.nft_address)
            # Se marca con la tx ya construida, no con la venta confirmada: el submit va por otra
            # ruta (/gacha/submit-tx, un blob firmado sin memo) y desde aquí no hay forma de saber
            # si llegó a salir. Marcar de más solo cuesta un sondeo si la venta se queda a medias;
            # marcar de menos deja abierto el agujero.
            _marcar_fuera(wallet, body.nft_address)
            return out
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")

    def _wallet_opcional(authorization: Optional[str]) -> Optional[str]:
        """La wallet del token de Privy, o `None` si no hay sesión.

        Sin sesión no es un error: hay pantallas (`/gacha/tracker-access`, y ahora el propio
        tracker) que tienen que poder distinguir "no ha entrado" de "se ha roto algo", y para eso
        necesitan seguir respondiendo sin exigir el token. Un token caducado se trata igual que
        no tener ninguno, por lo mismo.
        """
        if authorization and authorization.startswith("Bearer ") and privy is not None:
            try:
                return privy.embedded_solana_wallet(authorization[len("Bearer "):])
            except PrivyAuthError as e:
                # El token caducado se trata como "sin sesión", pero SE ANOTA. Sin esta línea, "no
                # manda token" y "manda uno que no verifica" producen la misma pantalla —"llevas 0
                # USDC apostados"— y desde el servidor no hay forma de distinguirlas. Pasó: un
                # jugador con 525 USDC apostados veía 0 y hubo que descartar a mano el cálculo, la
                # ventana y la base antes de mirar aquí. El motivo va al log, no a la respuesta.
                logger.warning("wallet opcional: token presente pero rechazado (%s): %s",
                               type(e).__name__, e)
                return None
        elif authorization:
            # Cabecera que no es un Bearer: casi siempre un cliente mal montado, y en silencio se
            # ve igual que no haber entrado.
            logger.warning("wallet opcional: Authorization presente pero no es 'Bearer …'")
        return None

    def _exigir_tracker(authorization: Optional[str], s: Session) -> None:
        """403 si quien pregunta no puede ver el tracker.

        Antes esto era público, y el comentario de entonces decía que cerrarlo "daría una falsa
        sensación de exclusividad a cambio de romper los enlaces que se comparten". Eso valía
        mientras el tracker era gratis. Desde que se cobra por él, dejarlo abierto sería cobrar
        por algo que un `curl` regala.

        Y lo de los enlaces resultó no ser cierto: el único consumidor de `/gacha/ev` es la propia
        pantalla, que ya no lo llama con la puerta puesta. Compartir `/machine-tracker` con
        alguien sin acceso YA le enseñaba la puerta.
        """
        wallet = _wallet_opcional(authorization)
        pase = tracker_pass.pase_vigente(s, wallet) if wallet else None
        if not tracker_access.acceso(s, wallet, lista_blanca=_tracker_allow,
                                     pase_hasta=pase)["allowed"]:
            raise HTTPException(403, "tracker_locked")

    # El bootstrap cuesta segundos por máquina, así que NO se calcula por petición: se cachea y se
    # rehace como mucho una vez por minuto. Los datos entran de forma continua, pero un intervalo
    # sobre 16.000 tiradas no se mueve de forma apreciable en sesenta segundos.
    _ev_cache: dict = {"t": 0.0, "filas": []}
    _EV_CACHE_TTL = 60.0

    @app.get("/gacha/ev")
    async def gacha_ev(hours: int = Query(default=48, ge=1, le=168),
                       authorization: Optional[str] = Header(None),
                       s: Session = Depends(db)):
        """Cuánto paga de verdad cada máquina del gacha, medido sobre el feed público de CC.

        YA NO es público: hace falta acceso al Machine Tracker (wager reciente, pase o lista de
        la casa — ver `_exigir_tracker`). Cada fila lleva por delante el estado de su cobertura, y
        el veredicto se RETIRA si la ventana no está completa o tiene huecos: ver `ev_view`.
        """
        _exigir_tracker(authorization, s)
        svc = _gacha_or_503()
        ahora = _time.time()
        if _ev_cache["filas"] and ahora - _ev_cache["t"] < _EV_CACHE_TTL and hours == 48:
            return {"rows": _ev_cache["filas"], "updated_at": int(_ev_cache["t"])}
        try:
            maquinas = await svc.machines()
        except GachaDisabled:
            # El kill-switch es una decisión NUESTRA, no una caída de CC. Si aquí se sirviera el
            # respaldo, apagar el gacha a mano no apagaría el tracker.
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            # CC no contesta. Las mediciones son NUESTRAS y están en nuestra base de datos: lo
            # único que nos falta de ellos es la lista de máquinas con su precio y su recompra.
            # Devolver un 502 aquí tiraba datos que teníamos delante y dejaba la pantalla entera
            # en "Couldn't load the tracker", protegida solo por una caché de sesenta segundos.
            #
            # Se sirve lo último bueno CON SU HORA, sin fingir que se acaba de medir: la pantalla
            # compara ese sello con el reloj y lo marca STALE a los cinco minutos.
            if _ev_cache["filas"]:
                logger.warning("gacha/ev: CC no responde (%s); se sirve la medición de hace %.0f s",
                               e, ahora - _ev_cache["t"])
                return {"rows": _ev_cache["filas"], "updated_at": int(_ev_cache["t"]),
                        "stale": True}
            # Sin nada medido no hay respaldo, y una lista vacía se leería como "esta máquina no
            # tiene datos" en vez de "no se ha podido preguntar".
            raise HTTPException(502, str(e) or "gacha upstream unavailable")
        def _calcular_filas() -> list:
            filas = []
            with session_factory() as s:
                # Solo las que se pueden jugar AHORA. Se descartan por dos motivos distintos y los dos
                # cuentan: las que hemos apagado nosotros (`machine_visibility`, el mismo filtro que el
                # catálogo) y las que Collector Crypt tiene cerradas (`available`). Medir el EV de una
                # máquina a la que nadie puede tirar es peor que no medirlo: ocupa sitio y sugiere una
                # decisión que no se puede tomar.
                #
                # Se filtra al PUBLICAR y no al ingerir: sus tiradas se siguen guardando, así que si
                # vuelve a abrirse no arranca desde cero.
                for m in machine_visibility.visible(s, maquinas):
                    code = m.get("code")
                    if not code or not m.get("price"):
                        continue
                    if m.get("available") is False:
                        continue
                    f = fila_ev(s, code, precio=float(m["price"]),
                                buyback_pct=(m.get("instantBuyback") or 0) / 100.0 or None,
                                horas=hours)
                    f["name"] = m.get("name") or code
                    filas.append(f)
            return filas

        # FUERA DEL BUCLE DE EVENTOS, y no por elegancia. `fila_ev` hace 4.000 remuestreos por
        # máquina: medido sobre pokemon_50 con sus 16.000 tiradas de 48 h, 1.000 remuestreos cuestan
        # 27 s, o sea ~108 s los 4.000 — de UNA máquina. El backend corre en UN proceso, así que
        # ejecutar eso aquí dentro deja al resto del mundo sin backend durante ese rato: ni saldo,
        # ni máquinas, ni chat, ni liquidar una batalla en curso. Es el mismo cuelgue del 11/08,
        # solo que aquel fue una ráfaga accidental y este vendría cada vez que caduque la caché.
        #
        # `run_in_threadpool` lo aparta a un hilo: sigue costando lo mismo, pero lo paga quien pidió
        # la página y no todos los demás. El trabajo de base va dentro del hilo a propósito — la
        # sesión se abre y se cierra ahí, sin cruzar la frontera.
        filas = await run_in_threadpool(_calcular_filas)
        filas.sort(key=lambda f: (f["realized_edge_pct"] is None, -(f["realized_edge_pct"] or 0)))
        if hours == 48:
            _ev_cache.update(t=ahora, filas=filas)
        return {"rows": filas, "updated_at": int(ahora)}

    # El carril rápido. Lleva SOLO las rachas, y esa frontera está puesta a conciencia: son las dos
    # únicas cosas que cambian con cada tirada y a la vez cuestan una consulta, mientras que el
    # intervalo cuesta 4.000 remuestreos por máquina (~9 s las 48) y no se mueve.
    #
    # Medido en mainnet: en diez segundos `pokemon_50` hace un par de tiradas y su edge se desplaza
    # 0.027 pp, cuando la tarjeta lo enseña con una décima y el intervalo mide 2 pp de ancho. O sea
    # que refrescarlo rápido no enseñaría un número nuevo, enseñaría el temblor del mismo.
    #
    # Aquí NO se llama a Collector Crypt: se leen las máquinas que ya tienen datos nuestros. Un
    # sondeo cada diez segundos contra su API sería maleducado y además haría depender el refresco
    # de que ellos respondan.
    _ev_vivo_cache: dict = {"t": 0.0, "filas": []}
    _EV_VIVO_TTL = 5.0

    @app.get("/gacha/tracker-access")
    async def gacha_tracker_access(authorization: Optional[str] = Header(None),
                                   s: Session = Depends(db)):
        """Si quien pregunta puede ver el Machine Tracker, y cuánto le falta si no.

        La sesión es OPCIONAL a propósito: quien no ha entrado no recibe un 401 sino la misma
        respuesta con `allowed: false`, que es lo que permite explicarle qué es esto y qué hace
        falta. Un error le diría que algo se ha roto, y no se ha roto nada.

        Esto solo CONSULTA el acceso; quien de verdad lo exige es `/gacha/ev` y `/gacha/ev/live`
        (`_exigir_tracker`), que ahora responden 403 sin él. El tracker dejó de ser público en
        cuanto se empezó a cobrar por él: hasta entonces esta pantalla era la única puerta, y el
        dato en sí quedaba abierto detrás para quien le mandara un `curl`.
        """
        wallet = _wallet_opcional(authorization)
        pase = tracker_pass.pase_vigente(s, wallet) if wallet else None
        precios = {}
        if tracker_pass.precio_base_units(7, tracker_pass_7d_usdc, tracker_pass_30d_usdc):
            precios["7"] = tracker_pass_7d_usdc
        if tracker_pass.precio_base_units(30, tracker_pass_7d_usdc, tracker_pass_30d_usdc):
            precios["30"] = tracker_pass_30d_usdc
        return tracker_access.acceso(s, wallet, lista_blanca=_tracker_allow,
                                     pase_hasta=pase, precios=precios)

    @app.get("/gacha/ev/live")
    async def gacha_ev_live(authorization: Optional[str] = Header(None),
                            s: Session = Depends(db)):
        """Lo que cambia tirada a tirada: la racha de cada rareza por máquina.

        Complementa a `/gacha/ev`, no lo sustituye. El edge, el intervalo y el veredicto siguen
        viniendo de allí, porque son caros y lentos de mover. Lleva el mismo cierre que aquel: es
        medición nuestra igual que el edge, no un dato ajeno.
        """
        _exigir_tracker(authorization, s)
        ahora = _time.time()
        if _ev_vivo_cache["filas"] and ahora - _ev_vivo_cache["t"] < _EV_VIVO_TTL:
            return {"rows": _ev_vivo_cache["filas"], "updated_at": int(_ev_vivo_cache["t"])}
        def _rachas() -> list:
            with session_factory() as s:
                return [{"machine": code, "tiers": rachas_por_tier(s, code)}
                        for code in winners_store.maquinas_con_datos(s)]

        # También a un hilo, aunque aquí sea una consulta por máquina y no 4.000 remuestreos: son
        # tantas consultas como máquinas con datos, la página lo pide cada pocos segundos, y el
        # backend es un solo proceso. Lo barato repetido a menudo también bloquea.
        filas = await run_in_threadpool(_rachas)
        _ev_vivo_cache.update(t=ahora, filas=filas)
        return {"rows": filas, "updated_at": int(ahora)}

    @app.get("/gacha/replay/{memo}")
    async def gacha_replay(memo: str, request: Request):
        """Vuelve a montar una tirada ya hecha, para poder enseñarla.

        PÚBLICO A PROPÓSITO. El sentido de esto es pegar el enlace en un vídeo o en X y que se vea
        sin cuenta; con autenticación no serviría para lo que existe. Y no expone nada nuevo: el
        memo ya está en la cadena y en el feed público de Collector Crypt, que además tiene su
        propio replay abierto sobre el mismo dato.

        NO ESCRIBE NADA. `openPack` es idempotente —repetirlo sobre un memo ya abierto devuelve la
        misma carta—, así que esto es una lectura por mucho que al otro lado sea un POST. En
        particular NO toca `opened_at`, ni los gimmighouls, ni el precio: repetir una tirada no es
        volver a hacerla.

        SOLO MEMOS NUESTROS. Se exige que el memo esté en nuestra base —de gacha o de batalla—
        antes de preguntarle a CC. Sin ese filtro cualquiera podría usarnos de proxy para consultar
        memos ajenos, y el límite por IP no llegaría a tiempo.
        """
        svc = _gacha_or_503()
        _replay_throttle(request.client.host if request.client else "?")
        # De qué máquina salió. Hace falta de verdad: sin esto la pantalla no puede saber la
        # recompra de ESTA tirada y acababa enseñando la de la máquina que el jugador tuviera
        # abierta —una tirada del 90% se veía al 85%—, o ninguna si no tenía ninguna abierta.
        with session_factory() as s:
            pack = s.get(GachaPack, memo)
            codigo = pack.pack_type if pack else None
            if pack is None:
                tirada = s.query(BattlePull).filter_by(memo=memo).first()
                if tirada is None:
                    raise HTTPException(404, "unknown pull")
                # Las tiradas de batalla no guardan la máquina: se llega por la partida.
                batalla = s.get(PackBattle, tirada.battle_id)
                codigo = batalla.machine_code if batalla else None
        try:
            out = await svc.open_pack(memo=memo)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")
        if out.get("pending") or not out.get("nft_address"):
            # Un sobre generado y nunca abierto no tiene nada que repetir. Se distingue de "no
            # existe" porque son cosas distintas para quien abrió el enlace.
            raise HTTPException(409, "this pull was never opened")
        # La máquina viaja con la tirada. Si no se puede resolver, va a `None` y la pantalla no
        # enseña recompra: mejor no decir nada que decir el número de otra máquina.
        out["machine"] = codigo
        out["buyback_pct"] = None
        out["machine_name"] = None
        out["pack_price"] = None
        if codigo:
            try:
                m = next((x for x in await svc.machines() if x.get("code") == codigo), None)
            except Exception:
                # La carta es lo que importa; la recompra es un extra. Cualquier problema al
                # resolverla se traga y se devuelve `None`, que la pantalla ya sabe leer.
                m = None
            if m:
                out["buyback_pct"] = m.get("instantBuyback")
                out["machine_name"] = m.get("shortName") or m.get("name")
                out["pack_price"] = m.get("price")
        return out

    @app.post("/gacha/open-pack")
    async def gacha_open(body: OpenPackBody,
                         wallet: str = Depends(current_user),
                         s: Session = Depends(db)):
        svc = _gacha_or_503()
        # NOT rate-limited: the client POLLS this endpoint (pollOpenPack, up to ~8 attempts per
        # pack) while CC settles the pack, so a single pull hits it several times by design.
        # Throttling it here burned the per-wallet budget and made the *next* pull 429 ("I click
        # open and nothing happens"). Pull initiation (generate-pack / yolo) is what's throttled;
        # ownership is still enforced below, so polling is safe and cannot spend money.
        pack = s.get(GachaPack, body.memo)
        if pack is None or pack.wallet != wallet:
            raise HTTPException(403, "this memo does not belong to this wallet")
        try:
            out = await svc.open_pack(memo=body.memo)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")
        if not out.get("pending") and out.get("nft_address"):
            first_open = pack.opened_at is None
            pack.opened_at = datetime.now(timezone.utc)
            pack.nft_address = out["nft_address"]
            # Persist what it cost + what came out so the profile can track gacha (wager + history).
            pack.insured_value = out.get("insured_value")
            pack.name = out.get("name")
            pack.rarity = out.get("rarity")
            pack.auto_sold = bool(out.get("auto_sold"))
            pack.buyback_amount = out.get("buyback_amount")
            try:
                # Histórico: el sobre YA se abrió. No puede depender de si la máquina sigue
                # ofreciéndose — si dependiera, apagarla le quitaría los gimmighouls al jugador.
                pack.price = await _machine_price_historico(pack.pack_type)
            except Exception:
                pass  # best-effort; the open already succeeded
            # Loyalty: award gimmighouls once, at the gacha rate (lower than battles).
            if first_open and pack.price:
                from .services.referrals import award_gimmighouls
                award_gimmighouls(s, wallet, float(pack.price), ratio=get_settings().gimmighoul_per_usdc_gacha)
            s.commit()
            username = read_user_view(s, wallet, elo_start).get("alias")
            drop = {
                "type": "drop",
                "id": out.get("nft_address"),
                "wallet": wallet,
                "username": username,
                "name": out.get("name"),
                "valueUsd": out.get("insured_value"),
                "rarity": out.get("rarity"),
                "image": out.get("image"),
                "ts": int(_time.time()),
            }
            asyncio.create_task(_broadcast_drop_later(drop, cost_base=pack.price, machine_code=pack.pack_type))
        return out

    async def _machine_name(machine_code: Optional[str]) -> Optional[str]:
        """Display name (short name preferred) of a gacha machine, or None. Best-effort."""
        if not machine_code:
            return None
        try:
            # SIN filtrar por visibilidad: esto nombra drops ya ocurridos. Apagar una máquina no
            # puede borrar el nombre de lo que ya se jugó con ella.
            machines = await gacha.machines()
            m = next((x for x in machines if x.get("code") == machine_code), None)
            return (m.get("shortName") or m.get("name")) if m else None
        except Exception:
            return None

    async def _broadcast_drop_later(drop: dict, cost_base: Optional[int] = None,
                                    machine_code: Optional[str] = None) -> None:
        # Hold the drop so it never spoils the opener's own reveal.
        try:
            await asyncio.sleep(LIVE_DROP_DELAY_S)
            _drops_buf.add(drop)
            await _chat_mgr.broadcast(drop)
            mult = big_hit_multiple(drop.get("valueUsd"), cost_base)
            if mult is not None and mult >= hit_announce_mult:
                who = drop.get("username") or abbreviate(drop.get("wallet") or "")
                name = drop.get("name") or "una carta"
                extra = {"event": "hit", "amountUsd": drop["valueUsd"], "mult": round(mult, 2)}
                machine = await _machine_name(machine_code)
                if machine:
                    extra["machine"] = machine
                await _announce(f"pulled {name}", user=who, extra=extra, persist=True,
                                wallet=drop.get("wallet"))
        except Exception:
            logger.exception("live drop broadcast failed")

    async def _broadcast_battle_drops(battle_id: str) -> None:
        """Surface a settled battle's pulls in the global Recent Drops feed.

        Mirrors the gacha drop broadcast, but one drop per pull and without the
        anti-spoiler delay (participants already watched the reveal). Staggered so
        they stream into the feed instead of arriving as one burst.
        """
        try:
            from .models import BattlePull
            s3 = session_factory()
            try:
                pulls = (s3.query(BattlePull)
                         .filter(BattlePull.battle_id == battle_id,
                                 BattlePull.nft_address.isnot(None))
                         .all())
                alias_cache: dict = {}
                drops = []
                for p in pulls:
                    if p.player_wallet not in alias_cache:
                        alias_cache[p.player_wallet] = read_user_view(s3, p.player_wallet, elo_start).get("alias")
                    drops.append({
                        "type": "drop",
                        "id": p.nft_address,
                        "wallet": p.player_wallet,
                        "username": alias_cache[p.player_wallet],
                        "name": p.name,
                        "valueUsd": p.insured_value,
                        "rarity": p.rarity,
                        "image": f"https://nft-dev.collectorcrypt.com/front/{p.nft_address}",
                        "ts": int(_time.time()),
                    })
            finally:
                s3.close()
            for d in drops:
                _drops_buf.add(d)
                await _chat_mgr.broadcast(d)
                await asyncio.sleep(0.5)
        except Exception:
            logger.exception("battle drops broadcast failed")

    async def _maybe_announce_winner(battle_id: str) -> None:
        """Highlight (persisted) a battle whose winner's haul >= winner_announce_mult × the entry.
        Take = total insured value of all pulls (the winner's pot); entry = per-player buy-in."""
        try:
            from .models import BattlePull
            with session_factory() as s:
                b = s.get(PackBattle, battle_id)
                if not b or b.status != "settled" or not b.winner:
                    return
                mode = b.mode
                entry_base = royale_buyin(b.max_players, b.price) if mode == "royale" else b.price
                entry = (entry_base or 0) / 1_000_000
                take = sum(p.insured_value or 0
                           for p in s.query(BattlePull).filter_by(battle_id=battle_id).all())
                if entry <= 0 or take < winner_announce_mult * entry:
                    return
                ganador = b.winner
                who = read_user_view(s, ganador, elo_start).get("alias") or abbreviate(ganador)
            # Se saca aquí dentro, como `mode` y `take`: fuera del `with`, `b` está desligado de la
            # sesión y leerle un atributo puede reventar.
            label = "Battle Royale" if mode == "royale" else "Pack Battle"
            await _announce(f"won a {label}", user=who, wallet=ganador,
                            extra={"event": "winner", "amountUsd": take, "mode": mode, "mult": round(take / entry, 2)},
                            action={"label": "View", "battleId": battle_id, "mode": mode}, persist=True)
        except Exception:
            logger.exception("winner announce failed")

    @app.post("/gacha/yolo")
    async def gacha_yolo(body: YoloBody,
                         wallet: str = Depends(current_user),
                         s: Session = Depends(db)):
        svc = _gacha_or_503()
        _gacha_throttle(wallet)
        try:
            out = await svc.generate_yolo_packs(player_address=wallet, pack_type=body.pack_type,
                                                count=body.count, turbo=body.turbo)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            raise HTTPException(502, str(e) or "gacha upstream unavailable")
        if not out.get("transactions"):
            raise HTTPException(502, "gacha upstream unavailable")
        for tx in out["transactions"]:
            memo = tx["memo"]
            existing = s.get(GachaPack, memo)
            if existing is not None:
                if existing.wallet != wallet:
                    raise HTTPException(502, "gacha upstream unavailable")
            else:
                s.add(GachaPack(memo=memo, wallet=wallet, pack_type=body.pack_type))
        s.commit()
        return out

    @app.get("/auth/privy/me")
    async def privy_me(authorization: Optional[str] = Header(None)):
        if privy is None:
            raise HTTPException(503, "privy not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing token")
        try:
            claims = privy.verify(authorization[len("Bearer "):])
        except PrivyAuthError:
            raise HTTPException(401, "invalid Privy token")
        return {"sub": claims.get("sub")}

    # ── Pack Battle lobby endpoints ───────────────────────────────────────────

    def current_user_id(authorization: Optional[str] = Header(None)) -> str:
        if privy is None:
            raise HTTPException(503, "privy not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing token")
        try:
            return privy.embedded_solana_wallet_id(authorization[len("Bearer "):])
        except PrivyAuthError:
            raise HTTPException(401, "invalid identity token")

    async def _require_available(wallet: str, amount: int, s: Session):
        bal = await usdc_balance_base_units(solana_rpc_url, wallet, cc_usdc_mint)
        avail = bal - reserved_total(s, wallet)
        if avail < amount:
            raise HTTPException(402, "not enough available USDC")

    # Una `pending` más vieja que esto ya no es "la petición en curso". El peor caso real de la
    # ruta de compra nueva: el envío (30 s, timeout de `submit_signed_tx`) + hasta 19 s en
    # `confirmar_firma` con el presupuesto corto de `_CONFIRMACION_*` de abajo (4 intentos de 4 s
    # cada uno, más 3 esperas de 1 s entre ellos) — unos 49 s. Se deja en 180 s igualmente: sigue
    # dando margen de sobra (más de 3x) y bajarlo más solo serviría para etiquetar de "atascada"
    # alguna compra que iba lenta.
    _PENDING_ATASCADA_S = 180

    # La autorreconciliación (punto 1 de abajo) llama a `confirmar_firma` con un presupuesto
    # CORTO a propósito: es una compra NUEVA la que está preguntando por una firma VIEJA, y no
    # tiene sentido que se quede colgada minutos —bloqueando una conexión— antes de poder decidir
    # su propio 409. Si en este presupuesto corto tampoco hay veredicto, la fila sigue `pending`
    # y la siguiente compra la vuelve a intentar.
    _RECONCILIACION_INTENTOS = 2
    _RECONCILIACION_ESPERA_S = 1.0

    # La confirmación del cobro RECIÉN enviado (paso 8: la firma NUEVA, no la reconciliación de
    # una `pending` vieja) TAMBIÉN necesita presupuesto corto, y antes no lo tenía: los valores
    # por defecto de `confirmar_firma` (10 intentos de hasta 20 s, más 9 esperas de 1.5 s = unos
    # 213 s) no caben en una petición HTTP normal, y ningún proxy delante (ngrok, Cloudflare...)
    # aguanta esa espera. En la práctica, eso le enseñaba un error a quien acababa de pagar con
    # éxito, y su reintento se topaba con el 409. Bajarlo es estrictamente mejor, no un riesgo
    # nuevo: la red de seguridad ya existe (una fila `pending` con firma se reconcilia sola en la
    # visita siguiente, punto 1), así que agotar este presupuesto corto solo significa "se sabrá
    # en la próxima petición", nunca "se perdió algo".
    _CONFIRMACION_INTENTOS = 4
    _CONFIRMACION_ESPERA_S = 1.0
    _CONFIRMACION_TIMEOUT_S = 4.0

    # Freno de peticiones de la compra del pase, con contadores PROPIOS (mismo motivo que los del
    # tip y el withdraw: compartirlos haría que comprar un pase te dejara sin poder retirar). Este
    # endpoint mueve USDC de verdad y además hace que el operador pague gas y, la primera vez, la
    # renta de la ATA de destino, así que sin freno un bucle de reintentos —propio o ajeno— sale
    # caro aunque cada intento acabe fallando.
    _pase_hits: dict[str, list[float]] = {}

    def _pase_throttle(wallet: str) -> None:
        now = _time.time()
        hits = [t for t in _pase_hits.get(wallet, []) if now - t < tracker_pass_rate_window_s]
        if len(hits) >= tracker_pass_rate_limit:
            raise HTTPException(429, "too many pass purchases, try again later")
        hits.append(now)
        _pase_hits[wallet] = hits

    @app.post("/gacha/tracker-pass")
    async def gacha_tracker_pass(body: TrackerPassBody,
                                 wallet: str = Depends(current_user),
                                 wallet_id: str = Depends(current_user_id),
                                 s: Session = Depends(db)):
        """Comprar un pase del Machine Tracker.

        EL ORDEN ES LO IMPORTANTE, porque aquí se mueve dinero real de un usuario y las dos cosas
        que tienen que pasar (cobrar en la cadena, anotar en nuestra base) no pueden ser atómicas.

          1. Que no haya YA una compra en curso de esta wallet (`pending`). La `pending` hace de
             candado: mientras exista, esta wallet no puede comprar otra vez. Si tiene firma se
             intenta reconciliar sola (presupuesto corto, ver `_RECONCILIACION_*` arriba) antes de
             rendirse: si se resuelve a `active`, ESTA petición devuelve ESE pase y no cobra nada
             más — un reintento tras un 502 (la reacción normal ante un error) no puede
             convertirse en una segunda compra.
          2. Que la wallet de destino del cobro esté configurada. Es un problema NUESTRO, no del
             jugador, así que se corta con un 503 en vez de con un 502 que parecería un cobro
             fallido.
          3. Saldo DISPONIBLE, que es el on-chain menos lo reservado. Sin esto, un pase podría
             gastarse el dinero comprometido en una batalla y dejarla sin fondos al liquidar.
          4. Pedir el blockhash, CONSTRUIR la transacción y FIRMARLA. Todavía no hay fila.
          5. Leer la firma de la transacción ya firmada. En local, sin red.
          6. Insertar la fila `pending` YA CON SU FIRMA, y commit.
          7. ENVIAR.
          8. Confirmar que llegó, y solo entonces `active`.

        POR QUÉ FIRMAR VA ANTES DE ESCRIBIR LA FILA, que es lo que de verdad decide el diseño. La
        fila `pending` hace dos trabajos a la vez: es el registro de auditoría (tiene que
        SOBREVIVIR al fallo) y es el candado (tiene que LIBERARSE en todos los caminos de fallo).
        Los dos trabajos son incompatibles en cuanto la fila existe antes de que exista dinero en
        movimiento: cualquier excepción de la que no se sepa decir "esto no llegó a salir" deja la
        fila puesta, y la wallet encerrada. Adivinarlo por el TIPO de la excepción falló tres
        veces seguidas (el blockhash, la wallet de destino, y una dirección de operador o un mint
        mal escritos), siempre igual: un fallo de CONFIGURACIÓN, que no depende de quién compra,
        encerraba a TODAS las wallets en su primer intento.

        Poniendo la fila DESPUÉS de firmar, esa familia entera de fallos deja de existir por
        construcción, no por acertar con una lista de `except`: construir y firmar ocurren sin
        fila, así que no hay nada que desbloquear — un 502 limpio y reintentable, y a otra cosa.

        Y como la firma de una transacción de Solana viaja DENTRO de la propia transacción (no la
        inventa el RPC; ver `leer_firma`), se puede anotar antes de enviar. Consecuencia directa:
        TODA `pending` tiene firma. Ya no existe la `pending` sin firma, que era la celda donde
        alguien podía quedarse cobrado sin recurso Y sin ni una firma que mirar.

        Tener firma NO es sinónimo de autorreconciliable, y esto NO queda arreglado del todo:
        si `enviar_cobro` revienta con un `httpx.ConnectError` (la conexión ni se llegó a abrir,
        así que no salió ni un byte de esta máquina), cae en el mismo `except Exception` genérico
        que un timeout normal, y la fila queda `pending` CON firma — pero esa firma nunca viajó a
        ningún RPC y no va a aparecer en NINGUNA cadena. `confirmar_firma` la sondeará para
        siempre sin encontrarla y devolverá `None` para siempre, así que ninguna reconciliación
        futura (punto 1) la va a resolver sola: es la única celda que sigue necesitando ojos
        humanos. Arreglarlo de raíz exige distinguir "el RPC contestó y no conoce esa firma" de
        "no pude ni preguntar", que el `except Exception` de hoy no distingue; queda fuera de
        alcance aquí porque el arreglo obvio (caducar la `pending` vieja a `failed` sin más)
        reabre el doble cobro: con el presupuesto corto de la reconciliación, dos sondeos que
        fallan por red dan el mismo `None` que una firma que de verdad nunca se difundió.

        DÓNDE EMPIEZA LO INDETERMINADO. En el POST de `sendTransaction` (paso 7) y no antes: ahí
        SÍ pudo haber salido aunque la respuesta que veamos sea un error de red. Todo lo anterior
        —blockhash, construcción, firma— es reintentable sin consecuencias. Por eso la ventana en
        la que el candado está tomado es solo el envío más la confirmación, no toda la petición.

        Al revés (activar y cobrar después) regala el acceso cuando el cobro falla, y ese fallo lo
        provoca cualquiera con la cuenta vacía.

        Qué significa cada estado final, para quien tenga que mirarlo a mano:

          · `active`: pase válido, fin de la historia.
          · `failed`: RECHAZO. El RPC rechazó el envío de forma explícita (`RuntimeError` de
            `submit_signed_tx`) o la cadena la ejecutó y falló (`confirmar_firma` viendo un
            `err`). El dinero no se movió — aunque "el RPC rechazó" es el rechazo DE ESE nodo, no
            una garantía de red: es la mejor lectura que tenemos con lo que nos dijo.
          · `pending`: una compra a medias, SIEMPRE con firma. Para encontrar las que llevan un
            rato así: `SELECT * FROM tracker_passes WHERE status = 'pending' AND created_at <
            <ahora - unos minutos>`. Entre esas filas hay dos casos que NO se distinguen mirando
            la base, solo preguntándole a la cadena por la firma (`getSignatureStatuses` o un
            explorador):
              - Se va a resolver sola: el envío no dio veredicto, o `confirmar_firma` agotó su
                presupuesto corto sin ver ni confirmación ni error, pero la transacción SÍ salió
                de esta máquina. La compra siguiente de esa wallet vuelve a preguntar (punto 1) y
                la cierra en cuanto la cadena tenga veredicto. Es el caso normal y no necesita que
                nadie la mire.
              - NO se va a resolver nunca sola: la transacción nunca llegó a difundirse (por
                ejemplo, un `httpx.ConnectError` al enviarla — la conexión ni se abrió), así que
                esa firma no está ni va a estar en ninguna cadena y ninguna reconciliación futura
                la va a encontrar. Esta es la única celda que de verdad necesita ojos humanos hoy.
                QUÉ HACER, sin riesgo: comprobar la firma (`tx_signature`) contra el RPC o un
                explorador. Si de verdad no aparece Y ya ha pasado tiempo de sobra sobre la
                validez de un blockhash (con margen: varios minutos, no segundos), es seguro
                marcar esa fila `failed` a mano — un blockhash caduca en unos 60-90 s, así que
                pasado ese margen la transacción NUNCA va a poder llegar a landear, y Solana no
                ejecuta nada parcial: si no aterrizó, el dinero no se movió. Marcarla `failed`
                solo libera el candado de esa wallet; no hace falta ninguna devolución porque
                nunca hubo cobro.

        Queda un hueco irreducible: cobrado y confirmado, y morirse antes del paso 8. La fila se
        queda `pending` con su firma, y la reconciliación del punto 1 la cierra sola. No hay
        reversión automática de una transferencia on-chain, y un camino de devolución que no se
        ejecuta casi nunca estaría roto el día que hiciera falta.
        """
        if body.days not in tracker_pass.DIAS_VALIDOS:
            raise HTTPException(422, "invalid pass duration")
        precio = tracker_pass.precio_base_units(body.days, tracker_pass_7d_usdc,
                                                tracker_pass_30d_usdc)
        if precio is None:
            raise HTTPException(503, "tracker_pass_disabled")

        # El freno va DESPUÉS de las dos comprobaciones de arriba (que no cuestan ni red ni base
        # de datos, así que no hay nada que frenar) y ANTES de todo lo demás, que sí: la
        # reconciliación pregunta a la cadena y el cobro mueve dinero.
        _pase_throttle(wallet)

        # Comprobación barata antes de tocar la cadena. NO basta por sí sola contra dos peticiones
        # a la vez (es un check-then-act, y entre el SELECT y el INSERT cabe una carrera): la
        # garantía real es el índice único parcial de app/db.py (uq_tracker_passes_pending_wallet),
        # que el INSERT de abajo puede violar y por eso está dentro de un try/except.
        ya_pendiente = s.scalars(select(TrackerPass).where(
            TrackerPass.wallet == wallet, TrackerPass.status == "pending")).first()
        if ya_pendiente is not None and ya_pendiente.tx_signature:
            # Autorreconciliable: hay firma, así que no hace falta que la mire una persona, solo
            # volver a preguntarle a la cadena, con un presupuesto corto (`_RECONCILIACION_*`
            # arriba). `confirmar_firma` es idempotente (solo sondea `getSignatureStatuses`), así
            # que llamarla de más no cuesta dinero, solo tiempo.
            veredicto = await confirmar_firma(solana_rpc_url, ya_pendiente.tx_signature,
                                              intentos=_RECONCILIACION_INTENTOS,
                                              espera_s=_RECONCILIACION_ESPERA_S)
            if veredicto is True:
                # Se resolvió sola A FAVOR del jugador: YA TIENE el pase que pagó. Devolverlo aquí
                # y NO seguir con la compra es lo que hace esto idempotente — sin esto, reintentar
                # tras un 502 (la reacción normal ante un error) cobraría un SEGUNDO pase encima
                # del primero, que sí se cobró.
                #
                # Y la ventana se recalcula SIEMPRE, no solo si ya había caducado entera. Una fila
                # `pending` NO da acceso (solo `active` lo da), así que todo el tiempo que estuvo
                # indeterminada es tiempo que el jugador pagó y no pudo usar: dárselo por
                # consumido sería cobrarle por nada. `periodo` devuelve exactamente lo que hay que
                # poner —ahora, o el final del pase que ya esté vigente, para no solaparse con él—
                # y en el caso normal (una `pending` de segundos) es un no-op de milisegundos.
                desde_r, hasta_r = tracker_pass.periodo(s, wallet, ya_pendiente.days)
                ya_pendiente.starts_at = desde_r
                ya_pendiente.ends_at = hasta_r
                ya_pendiente.status = "active"
                s.commit()
                return {"pass_until": int(hasta_r.timestamp()),
                       "days": ya_pendiente.days,
                       "price_usdc": ya_pendiente.price_base_units / 1_000_000}
            elif veredicto is False:
                ya_pendiente.status = "failed"
                s.commit()
            # veredicto is None: sigue indeterminada, cae al 409 de abajo tal cual.
        if ya_pendiente is not None and ya_pendiente.status == "pending":
            creada = ya_pendiente.created_at
            if creada.tzinfo is None:              # SQLite devuelve fechas sin zona
                creada = creada.replace(tzinfo=timezone.utc)
            atascada = (datetime.now(timezone.utc) - creada).total_seconds() > _PENDING_ATASCADA_S
            raise HTTPException(409, "tracker_pass_pending_stuck" if atascada else "tracker_pass_pending")

        # La wallet de destino del cobro (fee_dest) es config, no algo que dependa del jugador.
        # Ya no es esto lo que impide encerrar wallets —de eso se encarga el orden de abajo, que
        # construye y firma antes de escribir nada—, pero sigue mereciendo la pena: un 503
        # "tracker_pass_misconfigured" le dice a quien despliega dónde mirar, y un 502 "charge
        # failed" le haría buscar en la cadena un cobro que nunca se intentó.
        fee_dest = fee_wallet_address or privy_operator_address
        try:
            if not fee_dest:
                raise ValueError("fee_dest vacío")
            Pubkey.from_string(fee_dest)
        except ValueError:
            raise HTTPException(503, "tracker_pass_misconfigured")

        await _require_available(wallet, precio, s)          # 402 si no llega. Nada escrito aún.

        # ── DESDE AQUÍ HASTA EL `s.add(fila)` NO HAY FILA, Y ESO ES EL DISEÑO ────────────────
        # Pedir el blockhash, construir la transacción y firmarla no mandan NADA a la red de
        # Solana. Cualquier cosa que reviente aquí —el RPC caído, una dirección o un mint mal
        # escritos en la configuración (`ValueError` de solders), un blockhash ininteligible
        # (`ParseHashError`), Privy sin contestar (`PrivySignerError`), o cualquier otra— no ha
        # difundido una transacción, así que no hay nada que reconciliar y no hace falta saber de
        # qué tipo era: un solo `except Exception`, un 502, y el jugador puede reintentar de
        # inmediato porque no ha quedado ninguna fila haciéndole de candado.
        try:
            blockhash = await fetch_latest_blockhash(solana_rpc_url)
            firmada = await construir_y_firmar_cobro(privy_signer, wallet_id, wallet,
                                                     privy_operator_wallet_id,
                                                     privy_operator_address,
                                                     fee_dest, cc_usdc_mint, precio, blockhash)
            # La firma vive DENTRO de la transacción firmada: leerla no toca la red. Es lo que
            # permite que la fila nazca ya con ella. Si Privy devolviera algo sin firmar,
            # `leer_firma` levanta ValueError y caemos aquí mismo, sin fila y sin envío.
            firma = leer_firma(firmada)
        except Exception as e:
            logger.warning("tracker-pass: no se pudo preparar el cobro de %s (nada enviado, "
                           "sin fila): %s", wallet, e, exc_info=True)
            raise HTTPException(502, "charge failed") from e

        desde, hasta = tracker_pass.periodo(s, wallet, body.days)
        fila = TrackerPass(id=str(uuid.uuid4()), wallet=wallet, days=body.days,
                           price_base_units=precio, status="pending",
                           tx_signature=firma, starts_at=desde, ends_at=hasta)
        s.add(fila)
        try:
            s.commit()
        except IntegrityError:
            # Perdió la carrera contra otra petición de la misma wallet que se coló entre el
            # SELECT de arriba y este INSERT. El índice único es quien de verdad lo impide. Lo que
            # se tira es una transacción FIRMADA y NUNCA ENVIADA: no mueve dinero y caduca sola
            # con su blockhash. Recién llegada por definición: nunca "atascada".
            s.rollback()
            raise HTTPException(409, "tracker_pass_pending")

        # A partir de aquí sí es irreversible: la transacción puede haber salido aunque veamos un
        # error. La fila ya existe y ya tiene la firma, así que pase lo que pase hay algo concreto
        # que mirar en un explorador y algo concreto que reconciliar.
        try:
            await enviar_cobro(solana_rpc_url, firmada)
        except RuntimeError as e:
            # El RPC rechazó `sendTransaction` de forma explícita (`error` en la respuesta): dijo
            # que NO la acepta, así que el dinero no se movió y la fila puede cerrarse en `failed`
            # —lo que además libera el candado y deja reintentar al momento.
            fila.status = "failed"
            s.commit()
            logger.warning("tracker-pass: el envío fue RECHAZADO para %s: %s", wallet, e)
            raise HTTPException(502, "charge failed") from e
        except Exception as e:
            # Cualquier OTRA cosa (un timeout, un 5xx del proxy tras reenviar, un cuerpo sin
            # `result`) es INDETERMINADA: pudo salir igual. Ni `active` (regalaría acceso) ni
            # `failed` (mentiría que el dinero no se movió). Se queda `pending` CON su firma, que
            # es justamente lo que la siguiente compra de esta wallet reconcilia sola.
            logger.critical("tracker-pass: envío INDETERMINADO para %s, firma %s: %s",
                            wallet, firma, e, exc_info=True)
            raise HTTPException(502, "charge failed") from e

        resultado = await confirmar_firma(solana_rpc_url, firma,
                                          intentos=_CONFIRMACION_INTENTOS,
                                          espera_s=_CONFIRMACION_ESPERA_S,
                                          timeout_s=_CONFIRMACION_TIMEOUT_S)
        if resultado is False:
            # Rechazo: la cadena la ejecutó y falló (`err` presente). El dinero no se movió.
            fila.status = "failed"
            s.commit()
            logger.warning("tracker-pass: la cadena RECHAZÓ el cobro de %s, firma %s", wallet, firma)
            raise HTTPException(502, "charge not confirmed")
        if resultado is None:
            # Indeterminado: se agotaron los intentos sin ver ni confirmación ni error. La fila se
            # queda en `pending` CON firma — la próxima compra de esta wallet la reconcilia sola
            # (punto 1 de arriba) en vez de necesitar que alguien la mire a mano.
            logger.critical("tracker-pass: confirmación INDETERMINADA para %s, firma %s",
                            wallet, firma)
            raise HTTPException(502, "charge not confirmed")

        fila.status = "active"
        s.commit()
        return {"pass_until": int(hasta.timestamp()), "days": body.days,
                "price_usdc": precio / 1_000_000}

    async def _machine_price(machine_code: str) -> int:
        """Precio como PUERTA: sobre el catálogo filtrado. Una máquina apagada a mano no puede
        estrenar partidas ni tiradas. Se apoya en el mismo 409 que ya usaba la indisponibilidad de
        CC. Para anotar lo que costó algo YA hecho, usar `_machine_price_historico`."""
        with session_factory() as s:
            machines = machine_visibility.visible(s, await gacha.machines())
        m = next((x for x in machines if x.get("code") == machine_code), None)
        if not m or not m.get("available", True):
            raise HTTPException(409, "machine unavailable")
        return int(m["price"]) * 1_000_000   # USDC base units

    async def _machine_price_historico(machine_code: str) -> Optional[int]:
        """Lo que cuesta esa máquina, SIN filtrar por visibilidad ni disponibilidad.

        Es para registrar el coste de un sobre ya abierto. Con el filtro puesto, apagar una máquina
        hacía que su precio dejara de resolverse y el jugador perdía en silencio los gimmighouls de
        esa tirada — un pago retroactivo por una decisión de catálogo posterior a su compra.
        """
        machines = await gacha.machines()
        m = next((x for x in machines if x.get("code") == machine_code), None)
        return int(m["price"]) * 1_000_000 if m and m.get("price") is not None else None

    _RECONCILE_DELAY_S = 300   # reintento de reconciliación tras un void en caliente

    async def _reconcile_voided_later(battle_id: str, delay_s: float = _RECONCILE_DELAY_S):
        """Tras un void en caliente puede quedar una pull pagada sin resolver (CC lento). Reintenta
        la reconciliación + refund con sesión fresca cuando CC haya tenido tiempo de resolver."""
        try:
            await asyncio.sleep(delay_s)
            s3 = session_factory()
            try:
                b = s3.get(PackBattle, battle_id)
                if b is not None and b.status == "voided":
                    await reconcile_voided_battle_live(
                        s3, b, gacha=gacha, signer=privy_signer, rpc_url=solana_rpc_url,
                        usdc_mint=cc_usdc_mint, operator_wallet_id=privy_operator_wallet_id,
                        operator_address=privy_operator_address)
            finally:
                s3.close()
        except Exception:
            logger.exception("deferred reconcile failed for %s", battle_id)

    # Referencia fuerte a las tareas de fondo de batalla. El event loop solo mantiene referencias
    # DÉBILES a las tareas creadas con create_task, así que una sin referenciar puede ser recogida
    # por el GC en pleno vuelo — y como se cancelaría con CancelledError (un BaseException que el
    # `except Exception` del worker no captura), moriría en silencio dejando la batalla en
    # 'running' sin rastro en el log. Guardarlas aquí y soltarlas al terminar lo impide.
    _bg_tasks: set = set()

    def _spawn(coro):
        t = asyncio.create_task(coro)
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
        return t

    async def _run_bg(battle_id: str):
        """Background task for pack battles."""
        s2 = session_factory()
        try:
            b = s2.get(PackBattle, battle_id)
            result = await run_pack_battle_live(s2, b, gacha=gacha, signer=privy_signer,
                rpc_url=solana_rpc_url, usdc_mint=cc_usdc_mint,
                min_usdc_base_units=b.price, operator_wallet_id=privy_operator_wallet_id,
                operator_address=privy_operator_address, seed_lamports=escrow_seed_lamports)
            if result == "voided":
                asyncio.create_task(_reconcile_voided_later(battle_id))
            asyncio.create_task(_broadcast_battle_drops(battle_id))
            asyncio.create_task(_maybe_announce_winner(battle_id))
        except Exception:
            logger.warning("background run failed for %s", battle_id)
        finally:
            release_reservations(s2, battle_id)
            s2.close()

    async def _run_royale_bg(battle_id: str):
        """Background task for royale battles.

        Diverges from _run_bg: calls run_royale_live with price_base=battle.price.
        The escrow wallet was already created at lobby-creation time (escrow-at-create),
        so run_royale_live does not create a new escrow — it uses the pre-created one.
        """
        s2 = session_factory()
        try:
            b = s2.get(PackBattle, battle_id)
            result = await run_royale_live(s2, b, gacha=gacha, signer=privy_signer,
                rpc_url=solana_rpc_url, usdc_mint=cc_usdc_mint,
                operator_wallet_id=privy_operator_wallet_id,
                operator_address=privy_operator_address,
                seed_lamports=escrow_seed_lamports,
                price_base=b.price)
            if result == "voided":
                asyncio.create_task(_reconcile_voided_later(battle_id))
            asyncio.create_task(_broadcast_battle_drops(battle_id))
            asyncio.create_task(_maybe_announce_winner(battle_id))
        except Exception:
            logger.warning("background royale run failed for %s", battle_id)
        finally:
            release_reservations(s2, battle_id)
            s2.close()

    # Serialize on-chain buy-in collection (concurrent submits drop silently on devnet) and
    # CONFIRM the funds actually landed (submit does not wait for confirmation). Raises if not
    # confirmed — the caller surfaces it (toast) and does NOT join. No retry → no double charge.
    _buyin_lock = asyncio.Lock()

    async def collect_buyin_confirmed(player_wallet_id: str, player_wallet: str, escrow_address: str, amount: int):
        # Concurrent collects race and silently drop on devnet → serialize them (one at a time
        # with a short settle so each lands before the next). If the charge itself fails,
        # collect_buyin raises and the caller surfaces it (toast); no retry → no double charge.
        async with _buyin_lock:
            blockhash = await fetch_latest_blockhash(solana_rpc_url)
            await collect_buyin(solana_rpc_url, privy_signer, player_wallet_id, player_wallet,
                                privy_operator_wallet_id, privy_operator_address,
                                escrow_address, cc_usdc_mint, amount, blockhash)
            await asyncio.sleep(1)  # let the tx land before the next serialized collect

    def _anotar_buyin(s: Session, battle_id: str, wallet: str, amount: int) -> None:
        """Deja constancia de que ESTE jugador pagó su buy-in.

        Es el libro de caja que permite reconciliar un reembolso a medias: si la partida se anula y
        una transferencia falla, `buyin_paid > 0` con `refunded_at` nulo señala exactamente a quién
        le falta el dinero. Sin esto, lo único que quedaba era un saldo raro en el escrow y ninguna
        forma de saber de quién era.

        Se llama DESPUÉS del join porque el cobro va antes de que exista la fila del jugador.
        """
        fila = (s.query(BattlePlayer)
                .filter_by(battle_id=battle_id, player_wallet=wallet).first())
        if fila is not None:
            fila.buyin_paid = amount
            s.commit()

    def _anotar_reembolso(s: Session, battle_id: str, wallet: str, amount: int) -> None:
        """Cierra la anotación: a este jugador ya se le devolvió. Solo tras confirmar el envío."""
        fila = (s.query(BattlePlayer)
                .filter_by(battle_id=battle_id, player_wallet=wallet).first())
        if fila is not None:
            fila.refund_amount = amount
            fila.refunded_at = datetime.now(timezone.utc)
            s.commit()

    # Per-wallet withdraw throttle. The operator pays gas + the destination-ATA rent on every
    # withdraw, so without a rate-limit + minimum a user could spam tiny withdrawals to fresh
    # addresses and drain the operator's SOL (each new ATA ~0.002 SOL of rent the operator funds).
    _withdraw_hits: dict[str, list[float]] = {}

    def _withdraw_throttle(wallet: str) -> None:
        now = _time.time()
        hits = [t for t in _withdraw_hits.get(wallet, []) if now - t < withdraw_rate_window_s]
        if len(hits) >= withdraw_rate_limit:
            raise HTTPException(429, "too many withdrawals, try again later")
        hits.append(now)
        _withdraw_hits[wallet] = hits

    # Throttle de tips, con contadores PROPIOS. Compartirlos con el withdraw haría que dar
    # propinas dejara al jugador sin poder retirar, y son dos límites con motivos distintos: el
    # del withdraw protege la renta de ATA que paga el operador; este, del spam social.
    _tip_hits: dict[str, list[float]] = {}

    def _tip_throttle(wallet: str) -> None:
        now = _time.time()
        hits = [t for t in _tip_hits.get(wallet, []) if now - t < tip_rate_window_s]
        if len(hits) >= tip_rate_limit:
            raise HTTPException(429, "too many tips, try again later")
        hits.append(now)
        _tip_hits[wallet] = hits

    # Contadores propios, por el mismo motivo que arriba: si compartiera los del tip, buscar a
    # quién dar propina te dejaría sin poder dársela.
    _search_hits: dict[str, list[float]] = {}

    def _search_throttle(wallet: str) -> None:
        """Freno de la búsqueda de usuarios.

        Contadores propios: compartirlos con el tip haría que buscar a quién dar propina te dejara
        sin poder dársela. El freno del cliente (espera + caché) es una convención del frontend;
        este es la red de debajo, y es la que protege de un bucle hecho a mano.
        """
        now = _time.time()
        hits = [t for t in _search_hits.get(wallet, []) if now - t < 60.0]
        if len(hits) >= 20:
            raise HTTPException(429, "too many searches")
        hits.append(now)
        _search_hits[wallet] = hits

    @app.post("/gacha/free-pack")
    async def gacha_free_pack(body: GeneratePackBody, wallet: str = Depends(current_user),
                              wallet_id: str = Depends(current_user_id), s: Session = Depends(db)):
        """Canjea una tirada gratis con los puntos de Collector Crypt.

        Se diferencia de una tirada de pago en dos cosas. No hay nada que cobrar, así que no pasa
        por `_require_available` ni por el flujo de firmar-y-enviar del navegador: aquí lo único
        que CC pide es una transacción firmada por la wallet como PRUEBA DE PROPIEDAD, que ni
        siquiera envía a la cadena. La firmamos nosotros con la wallet delegada, igual que las
        tiradas de una batalla, así que para el jugador es un solo clic.

        Se comprueba el saldo ANTES de firmar: si no le quedan tiradas, un 409 dice cuántos puntos
        le faltan en vez de dejar que CC devuelva un error opaco.

        La fila de GachaPack se marca `submitted_at` ya: no hay pago pendiente que esperar, así
        que el sobre queda listo para abrir desde el primer momento.
        """
        svc = _gacha_or_503()
        _gacha_throttle(wallet)
        if privy_signer is None:
            raise HTTPException(503, "signer_unavailable")
        # La máquina tiene que estar ofertada por nosotros: una apagada a mano no puede estrenar
        # tiradas, ni gratis ni de pago. El precio, además, es lo que fija lo que cuesta la tirada.
        precio_base_units = await _machine_price(body.pack_type)
        try:
            estado = await svc.free_spins(wallet)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            logger.warning("free-pack: no se pudieron leer los puntos de CC: %s", e)
            raise HTTPException(502, "upstream_error")
        # Contra el precio de ESTA máquina: los mismos puntos dan tirada en la de 50 $ y no llegan
        # ni de lejos en la de 5.000 $. `_machine_price` da unidades base, la fórmula usa dólares.
        cuenta = tiradas_gratis(precio_base_units / 1_000_000, estado["points_available"])
        if cuenta["count"] <= 0:
            # CÓDIGO, no prosa: el texto que lee el jugador se escribe en el frontend, como en el
            # modal de propinas. El número viaja detrás de los dos puntos porque el mensaje bueno
            # dice cuántos puntos faltan, y esa cifra solo la sabe el servidor.
            raise HTTPException(409, f"not_enough_points:{cuenta['until_next']}")

        # El nonce se pide ANTES de firmar porque tiene que ir dentro de la transacción: CC lo
        # comprueba en el cuerpo y en la firma, y caduca en minutos, así que se pide aquí y no
        # antes de las comprobaciones de puntos. `None` = esta red todavía no lo pide (devnet),
        # y entonces la prueba es el memo de siempre.
        try:
            nonce = await svc.generate_free_pack(player_address=wallet, pack_type=body.pack_type)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            logger.warning("free-pack: CC no dio nonce para %s: %s", body.pack_type, e)
            raise HTTPException(502, "upstream_error")

        blockhash = await fetch_latest_blockhash(solana_rpc_url)
        prueba = (build_free_pack_proof_tx(wallet, blockhash, nonce) if nonce
                  else build_memo_tx(wallet, blockhash))
        firmada = await privy_signer.sign_solana(wallet_id, prueba)
        try:
            out = await svc.free_pack(player_address=wallet, pack_type=body.pack_type,
                                      signed_transaction=firmada, nonce=nonce)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            # CC distingue "esta máquina no da sobres gratis" de "no queda stock", y las dos son
            # cosas que el jugador puede entender; se traducen a un código en vez de a un 502 mudo.
            msg = str(e) or "gacha upstream unavailable"
            if "pack type" in msg.lower():
                raise HTTPException(409, "machine_no_free_spins")
            if "low" in msg.lower():
                raise HTTPException(409, "machine_out_of_cards")
            # El texto de CC se QUEDA EN EL LOG. Reenviarlo al navegador es cómo un jugador acabó
            # leyendo "Missing or invalid nonce" cuando CC cambió su contrato: su vocabulario no es
            # el nuestro, está en inglés y describe su implementación, no lo que le pasa a él.
            logger.warning("free-pack: CC rechazó el canje de %s: %s", body.pack_type, msg)
            raise HTTPException(502, "upstream_error")
        if not out.get("memo"):
            logger.warning("free-pack: CC respondió sin memo para %s", body.pack_type)
            raise HTTPException(502, "upstream_error")
        if s.get(GachaPack, out["memo"]) is None:
            s.add(GachaPack(memo=out["memo"], wallet=wallet, pack_type=body.pack_type,
                            price=0, submitted_at=datetime.now(timezone.utc)))
            s.commit()
        return {"memo": out["memo"], "remaining_points": out.get("remaining_points")}

    async def _withdraw_cards(body: WithdrawBody, wallet: str, wallet_id: str) -> dict:
        """Retiro de CARDS (airdrop de Collector Crypt), rama deliberadamente distinta a la de
        USDC — ver el detalle de cada diferencia en los comentarios de abajo."""
        # Sin mint configurado no hay nada que mover. Mismo criterio que en los endpoints del
        # claim: no configurado es "todavía no", no "no" — de ahí 503 y no un 404/422.
        if not cards_airdrop_mint:
            raise HTTPException(503, "withdrawals_unavailable")
        amount = int(round(body.amount * 1_000_000))   # CARDS también son 6 decimales, como USDC
        if amount <= 0:
            raise HTTPException(422, "amount must be > 0")
        # Mínimo PROPIO (min_withdraw_cards): el operador paga la renta de la ATA destino igual
        # que en USDC, así que el mismo ataque de dust a direcciones nuevas aplica igual.
        min_base = int(round(min_withdraw_cards * 1_000_000))
        if amount < min_base:
            raise HTTPException(422, f"the minimum withdrawal is {min_withdraw_cards} CARDS")
        _withdraw_throttle(wallet)  # el operador sigue pagando la renta de ATA aquí también
        # Sin bloqueo por partida en curso: CARDS no se apuesta en ninguna batalla (a diferencia
        # de USDC, que sí es lo que se juega), así que una partida abierta no le da destino a
        # este saldo y bloquearlo castigaría al jugador sin motivo que aplique a este token.
        # Sin resta de reservado: `reserved_total` cuenta holds de pack battle, que son en USDC.
        # El saldo disponible de CARDS es directamente el saldo on-chain, sin resta ninguna.
        bal = await usdc_balance_base_units(solana_rpc_url, wallet, cards_airdrop_mint)
        if bal < amount:
            raise HTTPException(402, "not enough available CARDS")
        blockhash = await fetch_latest_blockhash(solana_rpc_url)
        # Sin comisión de plataforma: el fee del withdraw grava dinero que SALE de la economía de
        # la plataforma, y este CARDS nunca entró en ella (llegó directo del airdrop de CC).
        try:
            sig = await withdraw_usdc(solana_rpc_url, privy_signer, wallet_id, wallet,
                                      privy_operator_wallet_id, privy_operator_address,
                                      body.address, cards_airdrop_mint, amount, blockhash)
        except Exception as exc:
            # El detalle va al log y NO al cuerpo de la respuesta: el str() de un error de httpx
            # incluye la URL completa del RPC, que en mainnet lleva la api-key en la query. Un 429
            # del proveedor le entregaría esa clave al navegador del jugador.
            logger.exception("airdrop: retiro de CARDS falló para %s", wallet)
            raise HTTPException(502, "withdraw failed")
        return {"signature": sig, "amount": body.amount, "net": amount / 1_000_000,
                "fee": 0.0, "address": body.address}

    @app.post("/users/me/withdraw")
    async def me_withdraw(body: WithdrawBody, wallet: str = Depends(current_user),
                          wallet_id: str = Depends(current_user_id), s: Session = Depends(db)):
        # Move USDC (or CARDS, ver _withdraw_cards) from the player's (delegated) wallet to an
        # external address; operator pays gas.
        if privy_signer is None or not (privy_operator_wallet_id and privy_operator_address):
            raise HTTPException(503, "withdrawals_unavailable")
        if body.token == "cards":
            return await _withdraw_cards(body, wallet, wallet_id)
        amount = int(round(body.amount * 1_000_000))   # USDC base units
        if amount <= 0:
            raise HTTPException(422, "amount must be > 0")
        # Enforce a minimum so the operator-paid ATA rent (~0.002 SOL/new dest) can't be drained
        # by a flood of 1-base-unit withdrawals to fresh addresses.
        min_base = int(round(min_withdraw_usdc * 1_000_000))
        if amount < min_base:
            raise HTTPException(422, f"the minimum withdrawal is {min_withdraw_usdc} USDC")
        _withdraw_throttle(wallet)                      # rate-limit per authed wallet
        # Se cierra el retiro solo cuando el saldo todavía tiene destino, que NO es siempre que
        # haya una partida abierta: una royale esperando en el lobby ya cobró su buy-in al escrow
        # y lo que quede en la wallet es del jugador. El detalle de qué cuenta como expuesto —y
        # por qué la royale en juego sí— está en battle_in_progress().
        en_juego = battle_in_progress(s, wallet)
        if en_juego:
            raise HTTPException(409, "you have a battle in play; you can withdraw once it ends")
        await _require_available(wallet, amount, s)     # caps at on-chain balance − reserved
        blockhash = await fetch_latest_blockhash(solana_rpc_url)
        # Platform fee: withdraw_fee_pct of the withdrawn amount, DEDUCTED from it — the destination
        # receives the net, the fee goes to the fee wallet (fallback: operator). Atomic in one tx.
        # 0 pct or no fee wallet → plain single-transfer withdrawal.
        fee_dest = fee_wallet_address or privy_operator_address
        fee = int(round(amount * withdraw_fee_pct)) if (withdraw_fee_pct > 0 and fee_dest) else 0
        net = amount - fee
        try:
            if fee > 0:
                sig = await withdraw_usdc_with_fee(solana_rpc_url, privy_signer, wallet_id, wallet,
                                                   privy_operator_wallet_id, privy_operator_address,
                                                   body.address, fee_dest, cc_usdc_mint, net, fee, blockhash)
            else:
                sig = await withdraw_usdc(solana_rpc_url, privy_signer, wallet_id, wallet,
                                          privy_operator_wallet_id, privy_operator_address,
                                          body.address, cc_usdc_mint, amount, blockhash)
        except Exception as exc:
            raise HTTPException(502, f"withdraw failed: {exc}")
        return {"signature": sig, "amount": body.amount, "net": net / 1_000_000,
                "fee": fee / 1_000_000, "address": body.address}

    @app.post("/users/me/tip")
    async def me_tip(body: TipBody, wallet: str = Depends(current_user),
                     wallet_id: str = Depends(current_user_id), s: Session = Depends(db)):
        """Propina en USDC de un jugador a OTRO JUGADOR.

        El destino tiene que ser un usuario registrado, y eso no es una comodidad: como
        `current_user` devuelve siempre la wallet embebida del token de Privy, exigir que el
        destinatario exista en `users` garantiza que el dinero aterriza en otra wallet delegada
        nuestra. Con destino libre, esto sería un `/users/me/withdraw` sin mínimo, sin comisión y
        sin throttle, o sea la puerta de atrás del withdraw.

        OJO, y esto es lo que hay que vigilar al tocar esto: esa garantía vale mientras TODA fila
        de `users` sea una wallet embebida nuestra, y eso hoy es cierto por accidente, no por
        construcción. `get_or_create_user` no comprueba nada, y `services/matches.py:89` da de
        alta usuarios con una wallet leída del estado ON-CHAIN de una batalla, que podría ser
        cualquier dirección externa. No es explotable ahora mismo (ese contrato no está desplegado
        y la ruta muere antes en un 404), pero el día que lo esté, o que aparezca otra alta de
        usuario con wallet de origen no verificado, este endpoint pasa a poder mandar USDC fuera
        de la plataforma sin mínimo, sin comisión y sin throttle. Si se añade una vía así, hay que
        validar aquí que el destino es una wallet embebida delegada, no basta con que exista.

        A diferencia del withdraw NO cobra comisión (el dinero sigue dentro de la plataforma y ya
        la pagará al salir) y NO se bloquea durante cualquier batalla, solo durante una royale en
        juego (`royale_in_progress`): así se puede dar propina en una pack battle o justo al
        terminar una partida, que es cuando apetece, sin tocar el dinero que una royale en marcha
        necesita para tirar.
        """
        # Interruptor, y va LO PRIMERO: apagado no se mira nada más, ni se consulta la base, ni se
        # toca la cadena. No borra nada, solo cierra la puerta (ver `tips_enabled` en config.py).
        if not tips_enabled:
            raise HTTPException(503, "tips_disabled")
        if privy_signer is None or not (privy_operator_wallet_id and privy_operator_address):
            raise HTTPException(503, "tips_unavailable")
        dest = (body.to or "").strip()
        if s.get(User, dest) is None:
            raise HTTPException(404, "that player does not have an account")
        if dest == wallet:
            raise HTTPException(422, "you cannot tip yourself")
        amount = int(round(body.amount * 1_000_000))    # USDC base units
        if amount <= 0:
            raise HTTPException(422, "amount must be > 0")
        min_base = int(round(min_tip_usdc * 1_000_000))
        if amount < min_base:
            raise HTTPException(422, f"the minimum tip is {min_tip_usdc} USDC")
        _tip_throttle(wallet)
        # Solo se cierra la royale EN JUEGO, no la guarda entera del retiro: durante una pack
        # battle el buy-in lleva reserva y `_require_available` ya lo protege, así que dar
        # propina ahí (o justo al acabar cualquier partida) se conserva a propósito. La royale en
        # marcha es otra cosa: el escrow le manda a la wallet el precio de cada caja justo antes
        # de tirar y ese importe NO lleva reserva, así que se ve como saldo libre y una propina
        # en esa ventana rompe la tirada, anula la partida y deja el escrow corto a costa de los
        # reembolsos de los DEMÁS. El detalle está en royale_in_progress().
        if royale_in_progress(s, wallet):
            raise HTTPException(409, "you are playing a royale; you can tip once it ends")
        await _require_available(wallet, amount, s)     # saldo on-chain menos lo reservado
        blockhash = await fetch_latest_blockhash(solana_rpc_url)
        try:
            sig = await withdraw_usdc(solana_rpc_url, privy_signer, wallet_id, wallet,
                                      privy_operator_wallet_id, privy_operator_address,
                                      dest, cc_usdc_mint, amount, blockhash)
        except Exception as exc:
            raise HTTPException(502, f"tip failed: {exc}")
        # La fila se escribe DESPUÉS de la firma: si la transferencia falla no hay historial que
        # corregir, y si falla esta escritura el dinero ya se movió y la firma está en los logs,
        # que es el menos malo de los dos fallos posibles.
        logger.info("tip: %s -> %s, %s unidades base, sig=%s", wallet, dest, amount, sig)
        source = body.source if body.source in ("profile", "chat") else "profile"
        s.add(Tip(from_wallet=wallet, to_wallet=dest, amount=amount, signature=sig, source=source))
        s.commit()
        # El aviso va al final y NO puede tumbar la propina: el dinero ya se movió y no se
        # deshace, así que responder un error por no haber podido avisar sería mentir sobre lo
        # que ha pasado. Va dirigido, nunca por broadcast: quién le da dinero a quién es asunto
        # de los dos, y difundirlo publicaría justo lo que se decidió no publicar al dejar los
        # `/tip` fuera del chat.
        try:
            alias = read_user_view(s, wallet, elo_start).get("alias")
            await _chat_mgr.send_to_wallet(dest, {
                "type": "tip", "from": wallet, "fromName": alias or abbreviate(wallet),
                "amount": amount / 1_000_000,
            })
        except Exception:
            logger.exception("propina %s -> %s hecha, pero no se pudo avisar", wallet, dest)
        return {"signature": sig, "amount": amount / 1_000_000, "to": dest}

    @app.post("/users/me/nft/withdraw")
    async def me_nft_withdraw(body: NftWithdrawBody, wallet: str = Depends(current_user),
                             wallet_id: str = Depends(current_user_id), s: Session = Depends(db)):
        # Send an NFT owned by the player's (delegated) embedded wallet to an EXTERNAL address.
        # Reuses the same nft_transfer service that ships won cards escrow→winner. The OPERATOR
        # sponsors the transfer (fee-payer + dest-ATA rent) when configured, so the user never needs
        # SOL — 2-signer: the owner authorizes the move, the operator pays. Requires the wallet
        # delegated so the server can sign on the owner's behalf.
        if privy_signer is None:
            raise HTTPException(503, "withdrawals_unavailable")
        # Ownership check FIRST: only transfer a mint the authed wallet actually holds on-chain, so
        # a user can never move someone else's NFT (the wallet is derived from their identity token).
        try:
            owns = await nft_in_owner(solana_rpc_url, wallet, body.nft_address)
        except Exception as exc:
            raise HTTPException(502, f"ownership check failed: {exc}")
        if not owns:
            raise HTTPException(403, "you do not own this NFT")
        _withdraw_throttle(wallet)  # same per-wallet throttle as USDC withdraw
        blockhash = await fetch_latest_blockhash(solana_rpc_url)
        sponsored = bool(privy_operator_wallet_id and privy_operator_address)
        try:
            tx = await build_transfer(solana_rpc_url, wallet, body.address, body.nft_address, blockhash,
                                      fee_payer=privy_operator_address if sponsored else None)
            signed = await privy_signer.sign_solana(wallet_id, tx)                          # owner authorizes
            if sponsored:
                signed = await privy_signer.sign_solana(privy_operator_wallet_id, signed)   # operator pays gas
            sig = await submit_signed_tx(solana_rpc_url, signed)
        except UnsupportedNftStandard as exc:
            raise HTTPException(422, f"unsupported nft standard: {exc}")
        except Exception as exc:
            raise HTTPException(502, f"nft withdraw failed: {exc}")
        # La carta ya no está en su wallet: el atajo del libro (que dice "se la entregamos") deja
        # de valer para ella, o vendería a CC algo que acaba de mandar fuera.
        _marcar_fuera(wallet, body.nft_address)
        return {"signature": sig, "nft_address": body.nft_address, "address": body.address}

    # ── Delegated signing — once the wallet is delegated, the server signs on the user's behalf
    # (session signer) so gacha/buyback/arena don't pop a wallet prompt. Each endpoint only ever
    # signs with the AUTHED user's own wallet_id, so it can never sign for anyone else. ──────────
    @app.post("/wallet/sign")
    async def wallet_sign(body: SignTxBody, wallet_id: str = Depends(current_user_id)):
        if privy_signer is None:
            raise HTTPException(503, "signing_unavailable")
        try:
            signed = await privy_signer.sign_solana(wallet_id, body.transaction)
        except Exception as exc:
            raise HTTPException(502, f"sign failed: {exc}")
        return {"signed_transaction": signed}

    @app.post("/wallet/sign-submit")
    async def wallet_sign_submit(body: SignTxBody, wallet_id: str = Depends(current_user_id)):
        if privy_signer is None:
            raise HTTPException(503, "signing_unavailable")
        try:
            signed = await privy_signer.sign_solana(wallet_id, body.transaction)
            sig = await submit_signed_tx(solana_rpc_url, signed)
        except Exception as exc:
            raise HTTPException(502, f"sign/submit failed: {exc}")
        return {"signature": sig}

    async def _exigir_delegacion(wallet_id: str) -> None:
        """Puerta de entrada a una partida: sin poder firmar por el jugador, no entra.

        POR QUÉ AQUÍ. Para tirar una caja, el servidor firma en nombre del jugador vía Privy. Si el
        jugador no nos ha añadido como firmante, esa firma falla — pero falla al ARRANCAR la
        partida, no al unirse, y para entonces el motor la anula para TODA la sala, con el escrow
        ya creado. En mainnet tumbó una Pack Battle de 250 $ por un solo jugador sin delegar. Aquí
        el daño se queda en quien lo causa: un 409 y nadie más se entera.

        La puerta del frontend (ModeHub / BattleFlow) ya lo pide, pero se salta llamando al
        endpoint a mano, así que no es una comprobación: es una comodidad.

        SI PRIVY NO CONTESTA, SE RECHAZA (503, reintentable). Es la decisión incómoda: una caída de
        Privy pasa a ser "no se puede entrar". Se elige eso porque dejar pasar reintroduce
        exactamente el fallo que esto viene a cerrar, y su coste lo pagan terceros —los demás de la
        sala— mientras que el coste de rechazar lo paga quien reintenta un minuto después. Además,
        con Privy caído tampoco se podrían firmar las tiradas, así que "dejarle entrar" solo
        retrasa el fallo hasta un sitio donde ya hace daño.

        Sin `privy_signer` (dev sin Privy) no se comprueba nada: no hay firma que verificar.
        """
        if privy_signer is None:
            return
        try:
            if not await privy_signer.podemos_firmar(wallet_id):
                raise HTTPException(409, "enable signing on your wallet before playing — the game "
                                         "signs your pack pulls for you")
        except PrivyNoVerificable:
            raise HTTPException(503, "could not verify your wallet right now; try again in a moment")

    @app.post("/users/me/airdrop/cards/claim")
    async def me_airdrop_cards_claim(wallet: str = Depends(current_user),
                                     wallet_id: str = Depends(current_user_id),
                                     s: Session = Depends(db)):
        """Reclama el airdrop del jugador. La wallet sale del identity token, así que
        nadie puede reclamar lo de otro ni aunque se invente el cuerpo de la petición.

        Dos firmas: el jugador autoriza como `temporal` y el operador paga. Es el mismo
        reparto que en /users/me/nft/withdraw.
        """
        _airdrop_o_503()
        # Mismo freno que en /withdraw y /nft/withdraw, y por la misma razón: el operador es
        # quien paga la renta de la ATA nueva (y el gas) de cada claim, así que sin límite un
        # jugador podría vaciarle el SOL a base de reclamar en bucle.
        _withdraw_throttle(wallet)
        # Antes que nada: sin delegación no podemos firmar por él, y más vale decírselo con
        # el mensaje que ya conoce del juego que dejarle chocar contra un 502 de Privy.
        # Atrapamos un 409 de delegación y lo etiquetamos para que el cliente lo distinga:
        # los dos 409s que salen de aquí significan cosas diferentes, y decirle al jugador
        # "ya reclamaste" cuando en realidad "no autorizaste la firma" es mentirle sobre su dinero.
        try:
            await _exigir_delegacion(wallet_id)
        except HTTPException as e:
            if e.status_code == 409:
                raise HTTPException(409, "needs_delegation")
            raise
        e = _airdrop.get(wallet)
        if e is None:
            raise HTTPException(403, "not eligible for this airdrop")
        index, amount = int(e["i"]), int(e["a"])
        if await _ya_reclamado(index):
            raise HTTPException(409, "already claimed")

        # Las pubkeys de CONFIGURACIÓN (vault/mint/distributor) se validan aparte de la
        # firma y el submit: un typo en el .env no es un fallo del jugador ni algo que se
        # arregle reintentando la firma, así que no puede salir como 500 ni como 502 — es
        # 503, igual que el resto de "no sabemos si esto funciona ahora mismo".
        try:
            destino = ata(Pubkey.from_string(wallet), Pubkey.from_string(cards_airdrop_mint))
        except ValueError as exc:
            # El try cubre dos pubkeys, no solo el mint: si algún día `wallet` llega
            # deformada, este mensaje tiene que decirlo, o mandaría a quien depura a
            # revisar CARDS_AIRDROP_MINT en el .env cuando el problema es otro.
            logger.error("airdrop: pubkey inválida al calcular la ATA de destino (wallet o mint): %s", exc)
            raise HTTPException(503, "airdrop_unavailable")
        try:
            crear_ata = await _airdrop_cuenta(solana_rpc_url, str(destino)) is None
        except Exception:
            # El cuerpo del 502 nunca lleva la excepción cruda: en mainnet `solana_rpc_url`
            # lleva un ?api-key= del proveedor, y volcarla en la respuesta se la mandaría al
            # navegador del jugador. El detalle completo se queda en el log del servidor.
            logger.exception("airdrop: check de la ATA falló para %s", wallet)
            raise HTTPException(502, "airdrop check failed")

        try:
            blockhash = await fetch_latest_blockhash(solana_rpc_url)
        except Exception:
            # Mismo trato que el resto de fallos de RPC de este endpoint: 502 y sin el
            # texto crudo, que puede traer la url con api-key incluida.
            logger.exception("airdrop: no se pudo obtener el blockhash para %s", wallet)
            raise HTTPException(502, "airdrop claim failed")
        try:
            tx = build_claim_tx(
                claimant=wallet, index=index, amount=amount, proof=list(e["p"]),
                distributor=cards_airdrop_distributor, vault=cards_airdrop_vault,
                mint=cards_airdrop_mint, operador=privy_operator_address,
                blockhash=blockhash, crear_ata=crear_ata,
            )
        except ValueError as exc:
            # El try cubre las CUATRO pubkeys de configuración (distributor/vault/mint/
            # operador), no solo vault y distributor: nombrarlas todas evita mandar a quien
            # depura a mirar el .env equivocado.
            logger.error("airdrop: pubkey de configuración inválida (distributor/vault/mint/operador): %s", exc)
            raise HTTPException(503, "airdrop_unavailable")
        try:
            firmada = await privy_signer.sign_solana(wallet_id, tx)                 # el dueño autoriza
            firmada = await privy_signer.sign_solana(privy_operator_wallet_id, firmada)  # el operador paga
            sig = await submit_signed_tx(solana_rpc_url, firmada)
        except Exception:
            # No nos fiamos del TEXTO del error para saber si "otra pestaña se adelantó":
            # eso ata el comportamiento a cómo redacte su mensaje el proveedor de RPC de
            # turno. Se le pregunta a la cadena, la única fuente de verdad de si la PDA ya
            # existe. Si _ya_reclamado tampoco puede contestar, propaga su propio 502 — la
            # respuesta correcta también en ese caso, porque tampoco sabemos qué pasó.
            # El cuerpo del 502 no lleva la excepción cruda por la misma razón que arriba:
            # puede traer la URL del RPC con su api-key. El detalle se queda en el log.
            logger.exception("airdrop: submit falló para %s", wallet)
            if await _ya_reclamado(index):
                raise HTTPException(409, "already claimed")
            raise HTTPException(502, "airdrop claim failed")

        # El dinero ya se movió on-chain llegados aquí: fallar la respuesta por no poder
        # guardar esta fila sería mentir sobre lo que pasó, la misma decisión que en /tip.
        # Se deja constancia a voces —con la firma, para poder reconstruirlo a mano— y se
        # responde igual que si hubiera ido bien, porque para el jugador ha ido bien.
        try:
            s.add(AirdropClaim(wallet=wallet, ronda=cards_airdrop_round, amount=amount, signature=sig))
            s.commit()
            logger.info("airdrop: %s reclamó %s unidades, sig=%s", wallet, amount, sig)
        except Exception:
            logger.exception("airdrop: %s reclamó %s unidades (sig=%s) pero no se pudo guardar la fila",
                             wallet, amount, sig)
        return {"signature": sig, "amount": amount}

    @app.post("/pack-battles")
    async def create_pack_battle(body: CreateBattleBody, wallet: str = Depends(current_user),
                                 wallet_id: str = Depends(current_user_id), s: Session = Depends(db)):
        # Quien crea también juega: la misma puerta que al unirse.
        await _exigir_delegacion(wallet_id)
        price = await _machine_price(body.machine_code) if body.machine_code else 0
        mode = body.mode

        if mode == "royale":
            if _royale_allow and wallet not in _royale_allow:
                raise HTTPException(403, "Battle Royale creation is limited during launch")
            # For royale, the funds check is against the buy-in, not just the pack price.
            buyin = royale_buyin(body.max_players, price)
            await _require_available(wallet, buyin, s)
            try:
                b = create_battle(s, wallet, wallet_id, machine_code=body.machine_code, price=price,
                                  max_players=body.max_players, mode="royale")
            except LobbyError as e:
                raise HTTPException(409, str(e))
            # Pre-create the escrow wallet at lobby-creation time so buy-ins can be
            # collected immediately when players join (before the battle starts).
            # Pack battles create the escrow lazily inside run_battle; royale diverges here.
            # Del pool si hay: este era el peor derrochador de wallets, porque crea el escrow al
            # abrir el lobby y 26 de las 79 wallets existentes son de lobbies que nadie jugó.
            esc = await escrow_pool.adquirir(s, privy_signer, b.id)
            b.escrow_wallet_id = esc["id"]
            b.escrow_address = esc["address"]
            s.commit()
            # Collect the creator's buy-in immediately (creator is the first player)
            try:
                await collect_buyin_confirmed(wallet_id, wallet, b.escrow_address, buyin)
            except Exception as exc:
                raise HTTPException(502, f"could not collect your buy-in: {exc}")
            _anotar_buyin(s, b.id, wallet, buyin)
            resp = get_battle(s, b.id)
            resp["buyin"] = buyin
            resp["escrow_address"] = b.escrow_address
            await _announce_created(b, buyin, "royale",
                                    read_user_view(s, wallet, elo_start).get("alias") or abbreviate(wallet))
            return resp

        # Default: pack mode — build the bundle (1..10 boxes), reserve the total
        if body.packs:
            for sel in body.packs:
                if sel.count < 1:
                    raise HTTPException(422, "each count must be >= 1")
            bundle: list[tuple[str, int]] = []
            for sel in body.packs:
                ppx = await _machine_price(sel.machine_code)   # 409 if unavailable
                bundle += [(sel.machine_code, ppx)] * sel.count
        else:
            if not body.machine_code:
                raise HTTPException(422, "machine_code or packs required")
            bundle = [(body.machine_code, await _machine_price(body.machine_code))]
        if not (1 <= len(bundle) <= 10):
            raise HTTPException(422, "a bundle must have between 1 and 10 boxes")
        total = sum(pr for _, pr in bundle)
        await _require_available(wallet, total, s)
        try:
            b = create_battle(s, wallet, wallet_id, machine_code=bundle[0][0], price=total,
                              max_players=body.max_players, mode=mode, packs=bundle)
        except LobbyError as e:
            raise HTTPException(409, str(e))
        reserve(s, wallet, b.id, total)
        await _announce_created(b, total, mode,
                                read_user_view(s, wallet, elo_start).get("alias") or abbreviate(wallet))
        return get_battle(s, b.id)

    @app.post("/pack-battles/{battle_id}/join")
    async def join_pack_battle(battle_id: str, wallet: str = Depends(current_user),
                               wallet_id: str = Depends(current_user_id), s: Session = Depends(db)):
        b = s.get(PackBattle, battle_id)
        if b is None:
            raise HTTPException(404, "battle not found")

        # Antes de mirar saldo y antes de cobrar nada: si no podemos firmar por él, no entra. Vale
        # igual para los dos modos, por eso va antes de la bifurcación.
        await _exigir_delegacion(wallet_id)

        if b.mode == "royale":
            # For royale, check that the player can cover the buy-in.
            buyin = royale_buyin(b.max_players, b.price)
            await _require_available(wallet, buyin, s)
            # Collect the buy-in into the pre-created escrow BEFORE joining — single attempt.
            # If the charge fails, the player is NOT joined and gets the error (toast).
            try:
                await collect_buyin_confirmed(wallet_id, wallet, b.escrow_address, buyin)
            except Exception as exc:
                raise HTTPException(502, f"could not collect the buy-in: {exc}")
            try:
                b, filled = join_battle(s, battle_id, wallet, wallet_id)
                _anotar_buyin(s, battle_id, wallet, buyin)
            except LobbyError as e:
                # Joined too late — refund the buy-in we just collected so it isn't stuck.
                try:
                    bh2 = await fetch_latest_blockhash(solana_rpc_url)
                    await distribute_usdc(solana_rpc_url, privy_signer, b.escrow_wallet_id,
                                          b.escrow_address, wallet, cc_usdc_mint, buyin, bh2,
                                          operator_wallet_id=privy_operator_wallet_id,
                                          operator_address=privy_operator_address)
                except Exception:
                    logger.warning("join refund failed for %s in %s", wallet, battle_id)
                raise HTTPException(409, str(e))
            if filled:
                _spawn(_run_royale_bg(battle_id))
            await _broadcast_join_event(s, b, wallet, filled)
            return get_battle(s, battle_id)

        # Default: pack mode
        await _require_available(wallet, b.price, s)
        try:
            b, filled = join_battle(s, battle_id, wallet, wallet_id)
        except LobbyError as e:
            raise HTTPException(409, str(e))
        reserve(s, wallet, battle_id, b.price)
        if filled:
            _spawn(_run_bg(battle_id))
        await _broadcast_join_event(s, b, wallet, filled)
        return get_battle(s, battle_id)

    def _rematch_body(fin: PackBattle, s: Session) -> CreateBattleBody:
        """Rebuild the create-battle body from a finished battle so the rematch has the same config."""
        if fin.mode == "royale":
            return CreateBattleBody(machine_code=fin.machine_code, max_players=fin.max_players, mode="royale")
        packs = s.query(BattlePack).filter_by(battle_id=fin.id).order_by(BattlePack.sequence).all()
        if packs:
            counts: dict[str, int] = {}
            order: list[str] = []
            for p in packs:
                if p.machine_code not in counts:
                    order.append(p.machine_code); counts[p.machine_code] = 0
                counts[p.machine_code] += 1
            return CreateBattleBody(max_players=fin.max_players, mode="pack",
                                    packs=[PackSel(machine_code=m, count=counts[m]) for m in order])
        return CreateBattleBody(machine_code=fin.machine_code, max_players=fin.max_players, mode="pack")

    @app.post("/pack-battles/{battle_id}/rematch")
    async def rematch_pack_battle(battle_id: str, wallet: str = Depends(current_user),
                                  wallet_id: str = Depends(current_user_id), s: Session = Depends(db)):
        """Create-or-join the rematch for a finished battle. The first participant to ask creates a new
        lobby with the same config (real stake — same funds path as a normal create) and the others
        auto-join it. Other participants are invited over the WS in case they left the result screen."""
        finished = s.get(PackBattle, battle_id)
        if finished is None:
            raise HTTPException(404, "battle not found")
        participants = {p.player_wallet for p in s.query(BattlePlayer).filter_by(battle_id=battle_id).all()}
        if wallet not in participants:
            raise HTTPException(403, "only players in the battle can ask for a rematch")
        if finished.status not in ("settled", "voided"):
            raise HTTPException(409, "the battle has not finished yet")

        # An open rematch lobby already exists → auto-join it (funds handled by the join path).
        rm = s.get(PackBattle, finished.rematch_battle_id) if finished.rematch_battle_id else None
        if rm is not None and rm.status == "lobby":
            rm_players = {p.player_wallet for p in s.query(BattlePlayer).filter_by(battle_id=rm.id).all()}
            if wallet in rm_players:
                return {"battle_id": rm.id, "created": False, "joined": False}
            await join_pack_battle(rm.id, wallet=wallet, wallet_id=wallet_id, s=s)
            return {"battle_id": rm.id, "created": False, "joined": True}

        # Otherwise create a fresh rematch lobby with the same config (same funds path as create).
        created = await create_pack_battle(body=_rematch_body(finished, s), wallet=wallet, wallet_id=wallet_id, s=s)
        new_id = created["id"]
        finished.rematch_battle_id = new_id
        s.commit()
        buyin_base = royale_buyin(finished.max_players, finished.price) if finished.mode == "royale" else finished.price
        from_name = read_user_view(s, wallet, elo_start).get("alias") or abbreviate(wallet)
        await _chat_mgr.broadcast({
            "type": "rematch", "finished_battle_id": battle_id, "rematch_battle_id": new_id,
            "from": wallet, "from_name": from_name, "players": sorted(participants),
            "mode": finished.mode, "buyin": (buyin_base or 0) / 1_000_000,
        })
        return {"battle_id": new_id, "created": True, "joined": True}

    _emote_last: dict[str, float] = {}   # wallet → last emote monotonic ts (rate-limit)

    @app.post("/pack-battles/{battle_id}/emote")
    async def throw_battle_emote(battle_id: str, body: EmoteThrowBody, wallet: str = Depends(current_user),
                                 s: Session = Depends(db)):
        """Broadcast an emote to everyone in a battle. Requires the caller to own the emote and be a
        participant; rate-limited to ~1/s per wallet. Delivery is via the WS hub (clients filter by
        battle_id)."""
        b = s.get(PackBattle, battle_id)
        if b is None:
            raise HTTPException(404, "battle not found")
        participants = {p.player_wallet for p in s.query(BattlePlayer).filter_by(battle_id=battle_id).all()}
        if wallet not in participants:
            raise HTTPException(403, "only players in the battle can send emotes")
        if not emote_service.owns(s, wallet, body.code):
            raise HTTPException(403, "you do not own that emote")
        now = _time.monotonic()
        last = _emote_last.get(wallet)
        if last is not None and now - last < 1.0:
            raise HTTPException(429, "too fast")
        _emote_last[wallet] = now
        await _chat_mgr.broadcast({"type": "emote", "battle_id": battle_id, "from": wallet, "code": body.code})
        return {"ok": True}

    async def _add_one_bot(s: Session, b: PackBattle) -> Optional[bool]:
        """Add one funded reserve bot to lobby `b`.

        Returns the `filled` flag (True if this bot completed the lobby and the
        battle was started) when a bot was added, or None if no eligible funded
        bot is available. Raises HTTPException on on-chain failure (buy-in
        collection) or a late-join race.
        """
        bots = load_bots()
        if not bots:
            return None
        in_battle = {p.player_wallet for p in s.query(BattlePlayer).filter_by(battle_id=b.id).all()}
        buyin = royale_buyin(b.max_players, b.price) if b.mode == "royale" else b.price
        candidates = [bot for bot in bots if bot["address"] not in in_battle]
        balances = {bot["address"]: await usdc_balance_base_units(solana_rpc_url, bot["address"], cc_usdc_mint)
                    for bot in candidates}
        bot = pick_bot(bots, in_battle, balances, buyin)
        if bot is None:
            return None
        bw, bid = bot["address"], bot["id"]
        if b.mode == "royale":
            # Collect the bot's buy-in into the escrow BEFORE joining — single attempt. If the
            # charge fails, the bot is NOT joined and the caller surfaces the error (toast):
            # no silent unfunded joins, no double charge.
            try:
                await collect_buyin_confirmed(bid, bw, b.escrow_address, buyin)
            except Exception as exc:
                raise HTTPException(502, f"could not collect the bot buy-in: {exc}")
            try:
                _b2, filled = join_battle(s, b.id, bw, bid)
                _anotar_buyin(s, b.id, bw, buyin)
            except LobbyError as e:
                # Joined too late — refund the buy-in we just collected so it isn't stuck.
                try:
                    bh2 = await fetch_latest_blockhash(solana_rpc_url)
                    await distribute_usdc(solana_rpc_url, privy_signer, b.escrow_wallet_id,
                                          b.escrow_address, bw, cc_usdc_mint, buyin, bh2,
                                          operator_wallet_id=privy_operator_wallet_id,
                                          operator_address=privy_operator_address)
                except Exception:
                    logger.warning("join-bot refund failed for %s in %s", bw, b.id)
                raise HTTPException(409, str(e))
            if filled:
                _spawn(_run_royale_bg(b.id))
        else:
            try:
                _b2, filled = join_battle(s, b.id, bw, bid)
            except LobbyError as e:
                raise HTTPException(409, str(e))
            reserve(s, bw, b.id, b.price)
            if filled:
                _spawn(_run_bg(b.id))
        # El aviso se difunde AQUÍ y no en cada endpoint: /join-bot y /join-all-bots se lo
        # dejaban los dos, así que llenar un lobby con bots arrancaba la partida sin que a los
        # humanos les llegara el 'battle_start' — ni toast ni forma de enterarse.
        await _broadcast_join_event(s, b, bw, filled)
        return filled

    @app.post("/pack-battles/{battle_id}/join-bot")
    async def join_bot_pack_battle(battle_id: str, s: Session = Depends(db)):
        """DEV/TEST: drop a random funded reserve bot into a lobby slot (no auth).

        SECURITY: this endpoint is unauthenticated and moves real USDC on-chain (it
        collects a bot's buy-in into the escrow and can start the battle). It MUST stay
        disabled in production — it is only mounted-effectively when DEV_ENDPOINTS_ENABLED
        is set. Otherwise it 404s as if it did not exist.
        """
        if not dev_endpoints_enabled:
            raise HTTPException(404, "Not Found")
        b = s.get(PackBattle, battle_id)
        if b is None:
            raise HTTPException(404, "battle not found")
        if b.status != "lobby":
            raise HTTPException(409, "the battle is not in the lobby")
        filled = await _add_one_bot(s, b)
        if filled is None:
            raise HTTPException(409, "no free bots with enough balance")
        return get_battle(s, battle_id)

    @app.post("/pack-battles/{battle_id}/join-all-bots")
    async def join_all_bots_pack_battle(battle_id: str, s: Session = Depends(db)):
        """DEV/TEST: fill every empty lobby seat with funded reserve bots.

        Same posture as /join-bot (unauthenticated, moves real USDC, dev-gated).
        Best-effort: adds bots until the lobby fills or no eligible funded bot
        remains; 409 only if it could not add a single bot.
        """
        if not dev_endpoints_enabled:
            raise HTTPException(404, "Not Found")
        b = s.get(PackBattle, battle_id)
        if b is None:
            raise HTTPException(404, "battle not found")
        if b.status != "lobby":
            raise HTTPException(409, "the battle is not in the lobby")
        added = 0
        while True:
            filled = await _add_one_bot(s, b)
            if filled is None:   # no eligible funded bot left
                break
            added += 1
            if filled:           # lobby completed → battle started
                break
        if added == 0:
            raise HTTPException(409, "no free bots with enough balance")
        return get_battle(s, battle_id)

    @app.post("/pack-battles/{battle_id}/cancel")
    async def cancel_pack_battle(battle_id: str, wallet: str = Depends(current_user),
                                 s: Session = Depends(db)):
        b = s.get(PackBattle, battle_id)
        if b is None:
            raise HTTPException(404, "battle not found")
        is_royale = b.mode == "royale"
        escrow_wallet_id = b.escrow_wallet_id
        escrow_address = b.escrow_address
        try:
            cancel_battle(s, battle_id, wallet)   # validates creator + lobby, sets cancelled
        except LobbyError as e:
            raise HTTPException(409, str(e))
        # Snapshot POST-flip: un join que se coló antes del flip queda incluido en los refunds;
        # uno posterior falla en join_battle (status != lobby) y se auto-refundea por su path.
        players = [p.player_wallet for p in s.query(BattlePlayer).filter_by(battle_id=battle_id).all()]
        if is_royale:
            # Refund each joined player their buy-in from the escrow (best-effort, bounded retries).
            buyin = royale_buyin(b.max_players, b.price)
            for pw in players:
                for _ in range(3):
                    try:
                        bh = await fetch_latest_blockhash(solana_rpc_url)
                        # operator pays the fee — the escrow has no SOL when cancelled pre-run
                        await refund_buyin(solana_rpc_url, privy_signer, escrow_wallet_id, escrow_address,
                                           privy_operator_wallet_id, privy_operator_address,
                                           pw, cc_usdc_mint, buyin, bh)
                        _anotar_reembolso(s, battle_id, pw, buyin)
                        break
                    except Exception as exc:
                        logger.warning("royale cancel refund retry for %s in %s: %s", pw, battle_id, exc)
                else:
                    # Sin anotar `refunded_at`: la fila queda con buyin_paid > 0 y sin reembolso, que
                    # es justo lo que hace falta para saber a quién se le debe. Antes esto solo
                    # dejaba una línea de log y el dinero quieto en el escrow sin dueño conocido.
                    logger.error("royale cancel refund FAILED after retries for %s in %s", pw, battle_id)
        else:
            release_reservations(s, battle_id)
        # Devolver el escrow al pool. Sin esto, cancelar un lobby dejaba su wallet marcada `in_use`
        # para siempre: las royale crean el escrow al abrir el lobby, y los lobbies que nadie juega
        # eran 26 de las 79 wallets históricas — o sea, el mayor derroche, intacto.
        # Si los reembolsos de arriba acaban de enviarse y aún no han aterrizado, la comprobación
        # verá saldo y la marcará `retained`; el barrido de escrow_pool_sync la reevalúa después.
        await escrow_pool.liberar_al_terminar(s, solana_rpc_url, b, cc_usdc_mint)
        return get_battle(s, battle_id)

    @app.get("/pack-battles/open")
    async def open_pack_battles(s: Session = Depends(db)):
        return lobby_list_open(s)

    @app.get("/pack-battles/list")
    async def list_pack_battles(s: Session = Depends(db)):
        # Open lobbies + live + recent finished — powers the Live-games filters.
        return lobby_list_battles(s)

    @app.get("/pack-battles/{battle_id}")
    async def get_pack_battle(battle_id: str, s: Session = Depends(db)):
        try:
            return get_battle(s, battle_id)
        except LobbyError:
            raise HTTPException(404, "battle not found")

    @app.get("/pack-battles/{battle_id}/verify")
    async def verify_pack_battle(battle_id: str, s: Session = Depends(db)):
        b = s.get(PackBattle, battle_id)
        if b is None:
            raise HTTPException(404, "battle not found")
        return verification(s, b)

    # ── Chat de lobby por WebSocket ───────────────────────────────────────────
    _chat_mgr = ConnectionManager()
    # Chat history is persisted in the DB (last 50) — survives restarts and is the shared source
    # of truth across workers. See save_chat_message / recent_chat_messages.
    # Generic ring buffer reused for the global Recent Drops feed. Replayed to
    # every client on connect so the feed is consistent across origins/devices
    # (localStorage is per-origin, so it can't be the shared source of truth).
    _drops_buf = ChatBuffer(maxlen=20)
    _chat_hits: dict[str, list[float]] = {}
    _CHAT_RATE_LIMIT = 5
    _CHAT_RATE_WINDOW = 10.0

    _ANNOUNCER = "📢 Arena"

    async def _announce(text: str, *, user: Optional[str] = None, action: Optional[dict] = None,
                        extra: Optional[dict] = None, persist: bool = False,
                        wallet: Optional[str] = None) -> None:
        """Post a system announcement into the lobby chat. `persist=True` stores it in history
        (highlights: big hits, winners); battle-created pings are live-only. `user` overrides the
        announcer name (e.g. the battle creator); `extra` adds structured fields for the client to
        style. Never raises."""
        try:
            author = user or _ANNOUNCER
            msg = {"type": "message", "kind": "system", "user": author,
                   "text": text, "ts": int(_time.time())}
            # Los avisos nombran a una persona ("X ganó una Pack Battle"), así que ese nombre
            # también lleva a su perfil. Los de la casa no tienen wallet y se quedan sin enlace.
            if wallet:
                msg["wallet"] = wallet
            if action:
                msg["action"] = action
            if extra:
                msg.update(extra)
            if persist:
                with session_factory() as s:
                    save_chat_message(s, author, text, msg["ts"], kind="system", action=action,
                                      wallet=wallet, event=(extra or {}).get("event"),
                                      amount_usd=(extra or {}).get("amountUsd"),
                                      machine=(extra or {}).get("machine"),
                                      mult=(extra or {}).get("mult"))
            await _chat_mgr.broadcast(msg)
        except Exception:
            logger.exception("chat announce failed")

    async def _broadcast_join_event(s: Session, battle, joiner_wallet: str, filled: bool) -> None:
        """Push a WS event so a lobby's participants get toasted when someone joins (pack) or the
        lobby fills and starts (any mode). Client-side filtered by `players`, like the rematch toast.
        Never raises — a failed broadcast must not fail the join."""
        try:
            players = [p.player_wallet for p in
                       s.query(BattlePlayer).filter_by(battle_id=battle.id).all()]
            name = read_user_view(s, joiner_wallet, elo_start).get("alias") or abbreviate(joiner_wallet)
            ev = join_event(battle_id=battle.id, mode=battle.mode, players=players,
                            joiner_wallet=joiner_wallet, joiner_name=name, filled=filled)
            if ev is not None:
                await _chat_mgr.broadcast(ev)
        except Exception:
            logger.exception("battle join/start broadcast failed")

    async def _announce_created(battle, stake_base: int, mode: str, creator_name: str) -> None:
        """Live-only ping when a joinable battle is created — rendered as a chat event
        '{creator} created a Pack Battle $50', with a quick-join button."""
        if (battle.max_players or 0) <= 1:
            return   # no open seat → nobody to invite
        label = "Battle Royale" if mode == "royale" else "Pack Battle"
        stake = (stake_base or 0) / 1_000_000
        await _announce(f"created a {label}", user=creator_name, wallet=battle.creator_wallet,
                        extra={"event": "created", "amountUsd": stake, "mode": mode},
                        action={"label": "Join", "battleId": battle.id, "mode": mode})

    @app.post("/dev/announce")
    async def dev_announce(body: DevAnnounceBody):
        """DEV/TEST: broadcast one sample chat announcement (dev-gated → 404 in prod).
        Lets the chat-event design be iterated by firing hit/winner/created examples."""
        if not dev_endpoints_enabled:
            raise HTTPException(404, "Not Found")
        extra: dict = {}
        if body.event:
            extra["event"] = body.event
        if body.amountUsd is not None:
            extra["amountUsd"] = body.amountUsd
        if body.mode:
            extra["mode"] = body.mode
        if body.machine:
            extra["machine"] = body.machine
        if body.mult is not None:
            extra["mult"] = body.mult
        action = None
        if body.action_label:
            action = {"label": body.action_label, "battleId": body.battle_id, "mode": body.mode or "pack"}
        await _announce(body.text, user=body.user, extra=extra or None, action=action, persist=body.persist)
        return {"ok": True}

    def _chat_allow(wallet: str) -> bool:
        now = _time.time()
        hits = [t for t in _chat_hits.get(wallet, []) if now - t < _CHAT_RATE_WINDOW]
        if len(hits) >= _CHAT_RATE_LIMIT:
            return False
        hits.append(now)
        _chat_hits[wallet] = hits
        return True

    def _presence() -> dict:
        """El aviso de quién hay. Se emite al entrar y al salir de cualquiera.

        `users` es la lista de mencionables (sin anónimos ni duplicados); `online` cuenta también
        a los anónimos, porque están mirando aunque no puedan hablar. Iban en tres sitios idénticos
        y ahora salen de aquí: al añadir `users` habría habido que acertar tres veces.
        """
        return {"type": "presence", "online": _chat_mgr.online_count(),
                "users": _chat_mgr.online_users()}

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket, token: Optional[str] = Query(None)):
        wallet = None
        if token and privy is not None:
            try:
                wallet = privy.embedded_solana_wallet(token)
            except PrivyAuthError:
                wallet = None
        await _chat_mgr.connect(ws)
        try:
            # Nombre a mostrar: alias del usuario si lo tiene, si no el wallet abreviado.
            # NOTA: se resuelve una vez al conectar; cambiar el username requiere reconectar.
            display_name = None
            if wallet:
                with session_factory() as s:
                    alias = read_user_view(s, wallet, elo_start).get("alias")
                display_name = alias or abbreviate(wallet)
                # Ata el socket a su jugador: es lo que permite ofrecer a quién mencionar y
                # contar jugadores en vez de sockets. Los anónimos se quedan sin identificar.
                _chat_mgr.identify(ws, wallet, display_name)
            with session_factory() as s:
                chat_history = recent_chat_messages(s)
            await ws.send_json({"type": "history", "messages": chat_history})
            await ws.send_json({"type": "drops_history", "drops": _drops_buf.history()})
            await _chat_mgr.broadcast(_presence())
            while True:
                data = await ws.receive_json()
                text = (data.get("text") or "").strip()
                if wallet is None:
                    await ws.send_json({"type": "error", "error": "login_required"})
                    continue
                if not text:
                    continue
                text = text[:280]
                if not _chat_allow(wallet):
                    await ws.send_json({"type": "error", "error": "rate_limited"})
                    continue
                # La wallet viaja junto al nombre para poder ir a su perfil desde el chat. Es
                # la embebida con la que ya está autenticado, así que no se pide nada nuevo.
                # El cliente manda a quién menciona; el servidor comprueba que sigan
                # conectados y recorta. Ver `_menciones_validas`.
                menciones = _menciones_validas(data.get("mentions"), _chat_mgr.online_users())
                msg = {"user": display_name, "wallet": wallet, "text": text, "ts": int(_time.time())}
                if menciones:
                    msg["mentions"] = menciones
                with session_factory() as s:
                    save_chat_message(s, display_name, text, msg["ts"], wallet=wallet,
                                      mentions=menciones or None)
                await _chat_mgr.broadcast({"type": "message", **msg})
        except WebSocketDisconnect:
            _chat_mgr.disconnect(ws)
            await _chat_mgr.broadcast(_presence())
        except Exception:
            _chat_mgr.disconnect(ws)
            await _chat_mgr.broadcast(_presence())

    # Cada cuánto se mira si hace falta abrir un lobby de la casa. No corre nada si el flag está
    # apagado, así que el coste en reposo es una consulta a SQLite cada medio minuto.
    _AUTO_ROYALE_PERIOD_S = 30.0

    async def _auto_royale_loop():
        """Mantiene una Battle Royale abierta mientras el flag `auto_royale` esté encendido.

        El flag se relee en CADA vuelta: encenderlo o apagarlo desde consola surte efecto en menos
        de un minuto sin reiniciar. El lobby se abre SIN creador y sin cobrar a nadie — ver
        services/house_lobby.py.
        """
        from .services import house_lobby
        while True:
            await asyncio.sleep(_AUTO_ROYALE_PERIOD_S)
            try:
                with session_factory() as s:
                    cfg = house_lobby.configuracion(s)
                    if cfg is None:
                        continue
                    machine, plazas = cfg
                    if not house_lobby.hace_falta_una(s, machine):
                        continue
                    b = create_battle(s, None, None, machine_code=machine,
                                      price=await _machine_price(machine),
                                      max_players=plazas, mode="royale")
                    esc = await escrow_pool.adquirir(s, privy_signer, b.id)
                    b.escrow_wallet_id = esc["id"]
                    b.escrow_address = esc["address"]
                    s.commit()
                    logger.info("auto-royale: abierto lobby de la casa %s (%s)", b.id, machine)
            except Exception:
                # Nunca puede tumbar el proceso: es un extra, no parte de ninguna partida en curso.
                logger.exception("auto-royale: no se pudo abrir el lobby; se reintenta luego")

    # ── EV tracker: ingesta del feed de ganadores de Collector Crypt ─────────
    #
    # Se suscribe al feed en vivo de Ably (keyless) y guarda cada tirada. Antes de escuchar, y en
    # cada reconexión, rellena por REST lo que se haya perdido y anota si quedó hueco: Ably solo
    # entrega lo que ocurre mientras estás conectado, así que sin este paso una caída deja un
    # agujero invisible dentro de la ventana.
    _EV_REINTENTO_S = 20.0
    # Dos sitios rellenan por REST —la reconexión y la red de seguridad— y no deben pisarse: dos
    # rellenos a la vez sobre la misma máquina se confundirían al decidir si un tramo enlaza.
    _ev_relleno = asyncio.Lock()

    async def _ev_rellenar(machine: str) -> None:
        with session_factory() as s:
            desde = winners_store.ultima_vista(s, machine)
        filas = await winners_ingest.traer_rest(gacha, machine, desde=desde)
        if not filas:
            return
        with session_factory() as s:
            winners_store.guardar(s, filas)
            winners_store.anotar_tramo(
                s, machine, filas[0]["created_at"], filas[-1]["created_at"],
                enlaza=not winners_ingest.hay_hueco(filas, desde))

    async def _ev_rellenar_todas(maquinas) -> int:
        """Rellena por REST todas las máquinas. Devuelve cuántas dieron algún fallo."""
        fallos = 0
        async with _ev_relleno:
            for m in maquinas:
                try:
                    await _ev_rellenar(m)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Una máquina que falla no puede dejar sin rellenar a las otras 38.
                    fallos += 1
        return fallos

    async def _ev_red_seguridad():
        """Rellena por REST cada cuarto de hora AUNQUE el feed en vivo parezca sano.

        Es la defensa contra lo que no tiene remedio: un hueco mayor de 200 tiradas se pierde para
        siempre, porque `getAllWinners` solo sirve las 200 más recientes y su `timestamp` acota
        hacia delante pero no alcanza hacia atrás (comprobado a mano: pedir desde hace 5 h y desde
        hace 24 h devuelve las mismas 200). Ably tampoco ayuda: su rewind llega a ~2 min.

        Así que no se intenta DETECTAR que el feed está mudo, que es justo lo que falló: se rellena
        y punto. La máquina más rápida tarda ~35 min en generar 200 tiradas, así que a este ritmo
        el relleno siempre llega a tiempo, se haya enterado alguien o no de que algo iba mal.
        """
        while True:
            await asyncio.sleep(winners_ingest.RELLENO_CADA_S)
            try:
                maquinas = [m["code"] for m in await gacha.machines() if m.get("code")]
                fallos = await _ev_rellenar_todas(maquinas)
                if fallos:
                    logger.warning("EV tracker: red de seguridad, %d de %d máquinas fallaron",
                                   fallos, len(maquinas))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("EV tracker: la red de seguridad falló, se reintenta", exc_info=True)

    async def _ev_loop():
        while True:
            try:
                maquinas = [m["code"] for m in await gacha.machines() if m.get("code")]
                if not maquinas:
                    await asyncio.sleep(_EV_REINTENTO_S)
                    continue
                await _ev_rellenar_todas(maquinas)
                token = await winners_ingest.token_ably(gacha)
                if not token:
                    await asyncio.sleep(_EV_REINTENTO_S)
                    continue
                # El feed llega más rápido de lo que conviene abrir sesiones, así que se acumula en
                # memoria y se vuelca por lotes. Perder un lote en una caída no es grave: el relleno
                # de la próxima reconexión lo recupera.
                buzon: list = []
                async def volcar():
                    while True:
                        await asyncio.sleep(5.0)
                        if not buzon:
                            continue
                        lote, buzon[:] = list(buzon), []
                        with session_factory() as s:
                            winners_store.guardar(s, lote)
                            for maq in {f["machine"] for f in lote}:
                                suyas = [f for f in lote if f["machine"] == maq]
                                winners_store.anotar_tramo(
                                    s, maq, min(f["created_at"] for f in suyas),
                                    max(f["created_at"] for f in suyas), enlaza=True)
                tarea = asyncio.create_task(volcar())
                try:
                    await winners_ingest.escuchar(token, maquinas, buzon.append)
                finally:
                    tarea.cancel()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Con la traza: un aviso que dice "se cortó" y no dice por qué obliga a reproducir
                # el fallo a mano, y eso ya costó una tarde con la clave duplicada de gacha_winners.
                logger.warning("EV tracker: la ingesta se cortó, se reintenta", exc_info=True)
            await asyncio.sleep(_EV_REINTENTO_S)

    # ── EV tracker: barrido del pool de cartas ───────────────────────────────
    #
    # La otra mitad del tracker. El feed dice lo que la máquina PAGÓ; el pool dice lo que debería
    # pagar, y sin las dos la aguja del dial no tiene contra qué compararse.
    #
    # Va muy espaciado a propósito: son 106.251 cartas en las 48 máquinas, unas 1.100 peticiones
    # por barrido. Y puede irlo: `pokemon_25` hace ~500 tiradas al día sobre 15.277 cartas, un 3%,
    # y el buyback devuelve al bote buena parte de lo que sale, así que la media de un tier apenas
    # se mueve en horas.
    _POOL_CADA_S = 6 * 3600
    _POOL_ESPERA_INICIAL_S = 90.0     # deja que el relleno del feed termine antes de empezar

    async def _pool_barrer(machine: str, odds: dict, ev_publicado) -> None:
        resumen = await pool_ingest.traer_pool(gacha, machine, odds)
        with session_factory() as s:
            pool_ingest.guardar_pool(s, machine, resumen)
        # Collector Crypt publica su propio `ev`, y comprobamos que es exactamente la suma de
        # probabilidad × valor medio (verificado en comic_25: 26.998 los dos). Que dejen de cuadrar
        # significa que han cambiado las odds, ha cambiado el pool, o uno de los dos se equivoca.
        # Es la única comprobación externa que tiene este cálculo, así que se avisa.
        bruto = sum(r["probability"] * r["avg_value"] for r in resumen.values()
                    if r.get("probability") and r.get("avg_value"))
        d = pool_ingest.desfase(bruto, ev_publicado)
        if d is not None and abs(d) > 0.02:
            logger.warning("EV tracker: el pool de %s no cuadra con el ev de CC "
                           "(nuestro %.2f, suyo %.2f, desfase %.1f%%)",
                           machine, bruto, ev_publicado, d * 100)

    async def _pool_loop():
        await asyncio.sleep(_POOL_ESPERA_INICIAL_S)
        while True:
            try:
                maquinas = await gacha.machines()
                barridas = 0
                for m in maquinas:
                    code, odds = m.get("code"), m.get("odds")
                    if not code or not odds:
                        continue
                    await _pool_barrer(code, odds, m.get("ev"))
                    barridas += 1
                logger.info("EV tracker: pool barrido en %d máquinas", barridas)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("EV tracker: el barrido del pool falló, se reintenta", exc_info=True)
            await asyncio.sleep(_POOL_CADA_S)

    @app.on_event("startup")
    async def _ev_start():
        if gacha is None or not gacha.enabled or not ev_tracker_enabled:
            return
        _spawn(_ev_loop())
        _spawn(_ev_red_seguridad())
        _spawn(_pool_loop())

    @app.on_event("startup")
    async def _auto_royale_start():
        if privy_signer is None:
            return          # sin firmante no hay escrow que asignar
        _spawn(_auto_royale_loop())

    @app.on_event("startup")
    async def _resume_orphaned_battles():
        # A backend restart kills the in-memory battle runners. Without this, a battle left in
        # 'running' is stranded forever (the reveal polls it and never sees it settle). On startup we
        # finish orphaned PACK and ROYALE battles off the persisted state: for pack, every pull
        # resolved → settle to the winner, a mid-pull crash (some pulls missing) → void + refund
        # each puller their own pull; for royale, resume_royale_live continues from the last
        # completed round (or voids + refunds if a mid-round pull is unrecoverable).
        # Also sweeps every already-'voided' battle through reconcile_voided_battle_live, in case a
        # hot void's deferred reconcile never got to run before a restart (idempotent, cheap no-op
        # for battles with nothing pending). Runs in background tasks so startup isn't blocked by
        # on-chain I/O.
        if privy_signer is None or gacha is None:
            return
        try:
            with session_factory() as s0:
                running = [(b.id, b.mode) for b in s0.query(PackBattle).filter_by(status="running").all()]
        except Exception:
            logger.warning("resume: could not query orphaned battles")
            return
        for bid, mode in running:
            async def _resume_one(battle_id=bid, battle_mode=mode):
                s2 = session_factory()
                try:
                    b = s2.get(PackBattle, battle_id)
                    if b is None or b.status != "running":
                        return
                    logger.warning("resume: finishing orphaned %s battle %s", battle_mode, battle_id)
                    if battle_mode == "royale":
                        await resume_royale_live(
                            s2, b, gacha=gacha, signer=privy_signer, rpc_url=solana_rpc_url,
                            usdc_mint=cc_usdc_mint, operator_wallet_id=privy_operator_wallet_id,
                            operator_address=privy_operator_address,
                            seed_lamports=escrow_seed_lamports, price_base=b.price)
                    else:
                        await resume_pack_battle_live(
                            s2, b, gacha=gacha, signer=privy_signer, rpc_url=solana_rpc_url,
                            usdc_mint=cc_usdc_mint, operator_wallet_id=privy_operator_wallet_id,
                            operator_address=privy_operator_address)
                    asyncio.create_task(_broadcast_battle_drops(battle_id))
                except Exception:
                    logger.warning("resume: failed to finish orphaned battle %s", battle_id)
                finally:
                    release_reservations(s2, battle_id)
                    s2.close()

            _spawn(_resume_one())

        # Barrido de reconciliación: batallas voided con refunds/pulls pendientes (p.ej. un void
        # en caliente cuya reconciliación diferida no llegó a correr antes de un reinicio).
        try:
            with session_factory() as s1:
                voided_ids = [b.id for b in s1.query(PackBattle).filter_by(status="voided").all()]
        except Exception:
            logger.warning("resume: could not query voided battles for reconcile sweep")
            voided_ids = []

        async def _sweep_one(battle_id):
            s2 = session_factory()
            try:
                b = s2.get(PackBattle, battle_id)
                if b is not None:
                    await reconcile_voided_battle_live(
                        s2, b, gacha=gacha, signer=privy_signer, rpc_url=solana_rpc_url,
                        usdc_mint=cc_usdc_mint, operator_wallet_id=privy_operator_wallet_id,
                        operator_address=privy_operator_address)
            except Exception:
                logger.warning("reconcile sweep failed for %s", battle_id)
            finally:
                s2.close()

        for bid in voided_ids:
            _spawn(_sweep_one(battle_id=bid))

    return app


def build_default_app() -> FastAPI:
    s = get_settings()
    _configure_app_logging()
    engine = make_engine(s.database_url)
    init_db(engine)
    session_factory = make_session_factory(engine)
    chain: ChainSource = MockChainSource()  # 'solana' se cablea cuando el lector real esté validado
    gacha = GachaService(base_url=s.gacha_base_url, api_key=s.gacha_api_key, nft_base_url=s.cc_nft_base_url)
    privy = PrivyVerifier(app_id=s.privy_app_id, jwks_url=s.privy_jwks_url.format(app_id=s.privy_app_id)) if s.privy_app_id else None
    privy_signer = PrivySigner(app_id=s.privy_app_id, app_secret=s.privy_app_secret,
                               auth_key_pem=s.privy_auth_key, cluster_caip2=s.privy_solana_caip2,
                               quorum_id=s.privy_quorum_id) if s.privy_app_id else None
    if privy_signer and not (s.privy_operator_wallet_id and s.privy_operator_address):
        logger.warning(
            "PRIVY_OPERATOR_WALLET_ID/PRIVY_OPERATOR_ADDRESS unset — Pack Battle/Royale will "
            "void at settle (escrow gas can't be funded). Set them in backend/.env."
        )
    _aviso = avisar_precios_raros(s.tracker_pass_7d_usdc, s.tracker_pass_30d_usdc)
    if _aviso:
        logger.warning("precios del pase del tracker: %s", _aviso)
    return create_app(session_factory, chain, elo_start=s.elo_start, elo_k=s.elo_k,
                      ev_tracker_enabled=s.ev_tracker_enabled,
                      cors_origins=s.cors_origins, gacha=gacha, privy=privy,
                      privy_signer=privy_signer,
                      solana_rpc_url=s.solana_rpc_url, cc_usdc_mint=s.cc_usdc_mint,
                      privy_operator_wallet_id=s.privy_operator_wallet_id,
                      privy_operator_address=s.privy_operator_address,
                      escrow_seed_lamports=s.escrow_seed_lamports,
                      dev_endpoints_enabled=s.dev_endpoints_enabled,
                      gacha_rate_limit=s.gacha_rate_limit,
                      min_withdraw_usdc=s.min_withdraw_usdc,
                      min_withdraw_cards=s.min_withdraw_cards,
                      tips_enabled=s.tips_enabled,
                      min_tip_usdc=s.min_tip_usdc,
                      tip_rate_limit=s.tip_rate_limit,
                      tip_rate_window_s=s.tip_rate_window_s,
                      withdraw_rate_limit=s.withdraw_rate_limit,
                      withdraw_rate_window_s=s.withdraw_rate_window_s,
                      referral_payout_wallet_id=s.referral_payout_wallet_id,
                      referral_payout_address=s.referral_payout_address,
                      referral_claim_min_base_units=s.referral_claim_min_base_units,
                      withdraw_fee_pct=s.withdraw_fee_pct,
                      fee_wallet_address=s.fee_wallet_address,
                      hit_announce_mult=s.hit_announce_mult,
                      winner_announce_mult=s.winner_announce_mult,
                      royale_creator_allowlist=s.royale_creator_allowlist_set,
                      tracker_access_allowlist=s.tracker_access_allowlist_set,
                      tracker_pass_7d_usdc=s.tracker_pass_7d_usdc,
                      tracker_pass_30d_usdc=s.tracker_pass_30d_usdc,
                      tracker_pass_rate_limit=s.tracker_pass_rate_limit,
                      tracker_pass_rate_window_s=s.tracker_pass_rate_window_s,
                      cards_airdrop=cargar_asignaciones(s.cards_airdrop_file),
                      cards_airdrop_distributor=s.cards_airdrop_distributor,
                      cards_airdrop_vault=s.cards_airdrop_vault,
                      cards_airdrop_mint=s.cards_airdrop_mint,
                      cards_airdrop_round=s.cards_airdrop_round)


app = build_default_app()
