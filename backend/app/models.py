from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Integer, Boolean, DateTime, Index, func, Float
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    wallet: Mapped[str] = mapped_column(String, primary_key=True)
    alias: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    elo: Mapped[int] = mapped_column(Integer, default=1200)
    games_played: Mapped[int] = mapped_column(Integer, default=0)
    #: Decimal a propósito: el gacha paga 0.01 por dólar, así que los premios son fracciones de punto
    #: y un contador entero los redondearía a cero. Ver `award_gimmighouls`.
    gimmighouls: Mapped[float] = mapped_column(Float, default=0.0)
    referred_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # ReferralCode.code
    withdraw_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # USDC payout destination
    emote_slots: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # JSON list of up to 3 quick-access emote codes
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        Index("ux_users_alias_lower", func.lower(alias), unique=True),
    )


class UserEmote(Base):
    """An emote a user owns. Quick-access slots (which 3 show in the bar) live on User.emote_slots."""
    __tablename__ = "user_emotes"
    wallet: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    emote_code: Mapped[str] = mapped_column(String, primary_key=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReferralCode(Base):
    __tablename__ = "referral_codes"
    code: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)  # creator name
    boost_pct: Mapped[float] = mapped_column(Float, default=0.0)      # boost on the referred user's earnings
    referrer_pct: Mapped[float] = mapped_column(Float, default=0.0)   # cut credited to the code owner
    # Rev-share del rake de batallas que generan los referidos de este código. Es dinero real
    # (USDC), a diferencia de referrer_pct, que es puntos. Sale del rake existente: el jugador
    # paga lo mismo.
    rake_share_pct: Mapped[float] = mapped_column(Float, default=0.25)
    owner_wallet: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # User to credit the cut to
    earned: Mapped[float] = mapped_column(Float, default=0.0)  # fallback tally when no owner_wallet
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReferralEarning(Base):
    """Un devengo: lo que un referidor ganó por UN participante referido en UNA batalla.

    Una fila por (batalla, referido) hace la auditoría trivial: se puede reconstruir de dónde
    salió cada céntimo. `payout_id` nulo = pendiente de cobrar.
    """
    __tablename__ = "referral_earnings"
    # Un referidor no puede cobrar dos veces por el mismo jugador en la misma batalla. Va como
    # índice y no como UniqueConstraint porque aquí no hay framework de migraciones: un índice se
    # puede crear sobre una tabla que ya existe (ver _ENSURE_INDEXES en db.py), y una constraint de
    # tabla exigiría reconstruirla.
    __table_args__ = (Index("uq_earning_battle_referred", "battle_id", "referred_wallet",
                            unique=True),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String)
    referrer_wallet: Mapped[str] = mapped_column(String, index=True)
    referred_wallet: Mapped[str] = mapped_column(String)
    battle_id: Mapped[str] = mapped_column(String, index=True)
    amount_base_units: Mapped[int] = mapped_column(Integer)
    payout_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReferralPayout(Base):
    """Un claim: el pago agregado de todas las earnings pendientes de un referidor."""
    __tablename__ = "referral_payouts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(String, index=True)
    amount_base_units: Mapped[int] = mapped_column(Integer)
    signature: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending | sent | failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AppFlag(Base):
    """Interruptor de producto que se enciende y apaga sin reiniciar.

    Se lee en cada uso, así que un cambio surte efecto al instante. Es una tabla genérica a
    propósito: cada interruptor nuevo es una fila, no una columna ni un despliegue.

    Un flag AUSENTE significa apagado. Es lo que hace que "no configurado" y "desactivado" sean lo
    mismo, y que encender algo sea siempre un acto explícito.
    """
    __tablename__ = "app_flags"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HiddenMachine(Base):
    """Máquina de gacha que NO se ofrece, aunque Collector Crypt la sirva.

    Se apagan a mano desde `scripts/machines.py`. Vive en la base y se lee en cada petición, así que
    encender o apagar una no exige reiniciar nada — y el frontend, que repregunta el catálogo cada
    poco, la hace desaparecer sola.

    Ocultar afecta al CATÁLOGO, no al histórico: una partida ya jugada con esta máquina conserva su
    nombre y su imagen. Lo que se impide es empezar partidas nuevas con ella.
    """
    __tablename__ = "hidden_machines"
    code: Mapped[str] = mapped_column(String, primary_key=True)
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Match(Base):
    __tablename__ = "matches"
    battle_pubkey: Mapped[str] = mapped_column(String, primary_key=True)
    creator: Mapped[str] = mapped_column(String, index=True)
    opponent: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    stake: Mapped[int] = mapped_column(Integer)
    min_elo: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_elo: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="open", index=True)  # open|joined|settled
    winner: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_draw: Mapped[bool] = mapped_column(Boolean, default=False)
    elo_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class RatingHistory(Base):
    __tablename__ = "rating_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(String, index=True)
    battle_pubkey: Mapped[str] = mapped_column(String)
    elo_before: Mapped[int] = mapped_column(Integer)
    elo_after: Mapped[int] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(String)  # win|loss|draw
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GachaPack(Base):
    __tablename__ = "gacha_packs"
    memo: Mapped[str] = mapped_column(String, primary_key=True)
    wallet: Mapped[str] = mapped_column(String, index=True)
    pack_type: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Cuándo lo VIO el jugador. Distinto de opened_at: el servidor abre el sobre en cuanto CC lo
    # resuelve, pero el reveal espera a que el usuario pulse. Entre esos dos momentos la carta ya
    # es suya y todavía no la ha visto; sin esta marca, cerrar ahí perdía el reveal para siempre.
    revealed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    nft_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)           # USDC base units spent to open
    insured_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # value of the card pulled (dollars)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)             # pulled card name
    # La rareza SOLO la da CC al abrir el sobre; /gacha/nft/{mint} devuelve rarity null. Si no se
    # guarda aquí, un reveal reproducido más tarde no puede mostrarla nunca.
    rarity: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # El turbo hace que CC recompre la carta al abrir. Sin guardarlo, un reveal reproducido más
    # tarde ofrece "Keep" y "Sell" de un NFT que ya no es del jugador.
    auto_sold: Mapped[bool] = mapped_column(Boolean, default=False)
    buyback_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class PackBattle(Base):
    __tablename__ = "pack_battles"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    mode: Mapped[str] = mapped_column(String)  # pack|royale
    machine_code: Mapped[str] = mapped_column(String)
    price: Mapped[int] = mapped_column(Integer)  # USDC base units
    max_players: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="lobby", index=True)  # lobby|running|settled|voided
    winner: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    creator_wallet: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    rematch_battle_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # link → the rematch lobby
    escrow_wallet_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    escrow_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    server_seed: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    server_seed_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    client_seed: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tie_break_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gimmighouls_awarded: Mapped[bool] = mapped_column(Boolean, default=False)  # idempotency guard for loyalty points
    fee_base_units: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # fee actually charged (USDC base units)
    fee_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)         # total pct applied (post-cap)
    fee_charged: Mapped[bool] = mapped_column(Boolean, default=False)              # idempotency guard
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BattlePlayer(Base):
    __tablename__ = "battle_players"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    battle_id: Mapped[str] = mapped_column(String, index=True)
    player_wallet: Mapped[str] = mapped_column(String, index=True)
    wallet_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    eliminated_round: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    accumulated_value: Mapped[float] = mapped_column(Float, default=0.0)
    # Cuándo VIO este jugador el resultado de la batalla. Null = terminó y aún no lo ha visto.
    # Distinto de PackBattle.settled_at (cuándo terminó): si nos guiáramos por eso, el modal daría
    # la lata con batallas que el jugador acaba de ver en directo. Es la misma distinción que en
    # el gacha entre opened_at (CC lo resolvió) y revealed_at (el jugador lo vio).
    seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Libro de caja del buy-in, por jugador. Sin esto, si una partida se anula y un reembolso falla,
    # no queda constancia de a quién le falta: el dinero se queda en el escrow y reconstruirlo exige
    # forense on-chain. Pasó de verdad — una royale anulada de 4 jugadores retenía exactamente una
    # parte, y no había forma de saber cuál de los cuatro no cobró.
    # buyin_paid > 0 y refunded_at NULL en una partida anulada = a este jugador se le debe dinero.
    buyin_paid: Mapped[int] = mapped_column(Integer, default=0)              # USDC base units
    refund_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BattlePull(Base):
    __tablename__ = "battle_pulls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    battle_id: Mapped[str] = mapped_column(String, index=True)
    player_wallet: Mapped[str] = mapped_column(String, index=True)
    memo: Mapped[str] = mapped_column(String)
    nft_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    insured_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    grade: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rarity: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    year: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    auto_sold: Mapped[bool] = mapped_column(Boolean, default=False)
    transferred: Mapped[bool] = mapped_column(Boolean, default=False)
    refunded: Mapped[bool] = mapped_column(Boolean, default=False)   # devolución post-void enviada
    buyback_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    round_number: Mapped[int] = mapped_column(Integer, default=1)
    # Firma de la transacción de COMPRA del sobre, que es la prueba de autoría: en esa misma
    # transacción va el `memo` de arriba y firma la wallet del jugador. Sin esto la prueba existe
    # igual —está en la cadena— pero hay que buscarla recorriendo el historial de la wallet, así
    # que no se le puede enseñar a nadie de un clic. Nula en tiradas anteriores a esta columna;
    # scripts/backfill_pull_signatures.py las reconstruye buscando el memo.
    tx_signature: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class BattlePack(Base):
    __tablename__ = "battle_packs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    battle_id: Mapped[str] = mapped_column(String, index=True)
    machine_code: Mapped[str] = mapped_column(String)
    price: Mapped[int] = mapped_column(Integer)   # USDC base units, per box
    sequence: Mapped[int] = mapped_column(Integer)  # 1..N order within the bundle


class BattleRound(Base):
    __tablename__ = "battle_rounds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    battle_id: Mapped[str] = mapped_column(String, index=True)
    round_number: Mapped[int] = mapped_column(Integer)
    client_seed: Mapped[str] = mapped_column(String)
    eliminated_wallet: Mapped[str] = mapped_column(String)
    tie_break_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(String, index=True)
    battle_id: Mapped[str] = mapped_column(String, index=True)
    amount: Mapped[int] = mapped_column(Integer)   # USDC base units
    status: Mapped[str] = mapped_column(String, default="active", index=True)  # active|released
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatMessage(Base):
    """Persisted lobby chat. Only the newest ~50 are kept (pruned on insert) so a user who
    wasn't connected still sees recent history, and it survives a backend restart."""
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    author: Mapped[str] = mapped_column(String)   # display name (alias or abbreviated wallet)
    # Wallet de quien habla, para poder ir a su perfil desde el chat. Nula en los mensajes
    # anteriores a esta columna y en los avisos de la casa, que no son de nadie: quien pinte el
    # nombre tiene que aguantar que falte, no darlo por hecho.
    wallet: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    text: Mapped[str] = mapped_column(String)
    ts: Mapped[int] = mapped_column(Integer, index=True)   # unix seconds
    kind: Mapped[str] = mapped_column(String, default="user")            # "user" | "system" (announcements)
    action: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # JSON {label, battleId, mode} for a button
    event: Mapped[Optional[str]] = mapped_column(String, nullable=True)   # "created" | "hit" | "winner" — structured render tag
    amount_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # value styled in gold (hit pull / winner take)
    machine: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # gacha machine a hit came from (display name)
    mult: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # hit multiple (value ÷ pull cost), e.g. 10.0 → "(x10)"
    # JSON con [{"wallet", "label"}]. Va APARTE del texto a propósito: guardar solo "@juan" dentro
    # del mensaje haría que mintiera el día que Juan se cambie el alias, y volver a enlazarlo
    # exigiría resolver nombre → wallet, que es la búsqueda que este diseño evita.
    mentions: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class EscrowWallet(Base):
    """Pool de wallets de escrow reutilizables.

    Antes se creaba una wallet de Privy por partida y no se reciclaba nunca: 79 partidas → 79
    wallets, y un tercio de ellas para lobbies que nadie llegó a jugar. Cada wallet cuenta como
    usuario activo en Privy, así que el coste crecía con las partidas para siempre.

    Una wallet solo vuelve al pool cuando se ha comprobado ON-CHAIN que está vacía: ni cartas (de
    cualquier estándar) ni USDC. `unavailable_reason` guarda por qué no se pudo liberar, para que un
    barrido que deja dinero detrás se vea como lo que es en vez de disfrazarse de pool poco eficiente.
    """
    __tablename__ = "escrow_wallets"
    address: Mapped[str] = mapped_column(String, primary_key=True)
    wallet_id: Mapped[str] = mapped_column(String)          # id de la wallet en Privy (para firmar)
    # free  → se puede reclamar. in_use → la tiene una partida. retained → tiene algo dentro.
    status: Mapped[str] = mapped_column(String, default="free", index=True)
    battle_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    unavailable_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    times_used: Mapped[int] = mapped_column(Integer, default=0)


class Tip(Base):
    """Propina en USDC de un jugador a otro.

    La transferencia vive en la cadena; esta fila es el historial: sin ella un tip solo existiría
    como una firma suelta, y no habría forma de enseñar las propinas recibidas en un perfil ni de
    investigar un abuso después. `source` se guarda porque lo primero que se querrá saber si hay
    que capar el spam es por dónde entra.
    """
    __tablename__ = "tips"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_wallet: Mapped[str] = mapped_column(String, index=True)
    to_wallet: Mapped[str] = mapped_column(String, index=True)
    amount: Mapped[int] = mapped_column(Integer)          # unidades base de USDC (6 decimales)
    signature: Mapped[str] = mapped_column(String)        # la prueba: la firma de la transacción
    source: Mapped[str] = mapped_column(String)           # "profile" | "chat"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GachaWinner(Base):
    """Una tirada del gacha de Collector Crypt, de quien sea. Es la materia prima del EV tracker.

    POR QUÉ SE GUARDA. CC sirve como mucho las 200 últimas por máquina, y la más movida renueva
    esas 200 en poco más de media hora. Sin acumular no hay ventana de 48 h que valga, así que la
    historia la construimos nosotros escuchando el feed en vivo.

    LA CLAVE ES (`nft_address`, `created_at`), y la primera parte sola NO vale. Una misma carta se
    entrega VARIAS veces: el jugador la revende a CC con el buyback y CC la devuelve al pool, donde
    otro la saca. Medido el 2026-08-14 en mainnet: `onepiece_50` traía 183 direcciones distintas en
    200 tiradas, con la misma carta reapareciendo en media hora. Usar solo la dirección reventaba
    la inserción, y "arreglarlo" descartando repetidas habría descontado hasta un 8% de las
    tiradas, sesgando el EV a la baja sin que nada lo delatara.

    Ojo también con `memo_slug` del REST: parece un identificador y es el prefijo del integrador
    (`cc`, `jupiter`), compartido por miles de tiradas.
    """
    __tablename__ = "gacha_winners"
    nft_address: Mapped[str] = mapped_column(String, primary_key=True)
    machine: Mapped[str] = mapped_column(String, index=True)      # `gachaCode` en vivo, `pack_type` en REST
    prize_tier: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # 1=Epic … 4=Common
    insured_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Un número propio de CC que SOLO viaja en el feed en vivo y que no está documentado. Se guarda
    # para poder estudiarlo; hoy no se usa para medir nada.
    weighted_insured_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    memo: Mapped[Optional[str]] = mapped_column(String, nullable=True)   # completo solo en vivo
    winner: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String)                   # "live" | "rest"
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GachaPoolTier(Base):
    """El pool de cartas de una máquina, resumido por rareza.

    Es la otra mitad del EV tracker. Lo que medimos del feed dice lo que la máquina PAGÓ; esto dice
    lo que debería pagar según las cartas que tiene dentro y las odds que publica Collector Crypt.
    Sin esta mitad, la aguja del dial no tiene contra qué compararse.

    Se guarda el resumen y no las cartas: son 106.251 en las 48 máquinas, y de cada rareza solo hace
    falta cuántas hay y cuánto valen de media. Guardarlas todas sería copiar su catálogo entero para
    calcular cuatro medias.
    """
    __tablename__ = "gacha_pool_tiers"
    machine: Mapped[str] = mapped_column(String, primary_key=True)
    #: `common` | `uncommon` | `rare` | `epic`, tal y como los nombra CC en `odds`.
    tier: Mapped[str] = mapped_column(String, primary_key=True)
    #: La probabilidad publicada por CC. Se guarda con el resto porque es la que se usó para el
    #: cálculo: si CC la cambia, un `gross` viejo deja de cuadrar y hay que poder verlo.
    probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    n_cards: Mapped[int] = mapped_column(Integer, default=0)
    avg_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    min_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GachaCoverage(Base):
    """Desde cuándo tenemos datos SIN HUECOS de una máquina, y qué tramos se perdieron.

    Sin esto, una ventana con agujeros se calcularía igual y saldría un número limpio sobre una
    muestra incompleta, que es peor que no dar número: nadie podría saber que le falta un trozo.
    Con esto la tarjeta puede decir "llevamos 6 h de 48" o "falta de 02:10 a 03:35".
    """
    __tablename__ = "gacha_coverage"
    machine: Mapped[str] = mapped_column(String, primary_key=True)
    # Instante desde el que la serie es continua. Un hueco lo EMPUJA hacia delante: lo anterior al
    # agujero existe, pero ya no sirve para una ventana que lo cruce.
    continuous_since: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    gaps: Mapped[Optional[str]] = mapped_column(String, nullable=True)   # JSON [[desde, hasta], …]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TrackerPass(Base):
    """Un pase de pago del Machine Tracker.

    La ÚNICA parte con estado del acceso al tracker. La ventana del wager se recalcula en cada
    consulta y no guarda nada; ver `tracker_access`. Esa frontera importa: si un día el pase se
    complica, el wager no se entera.

    `status` va de `pending` a `active` o a `failed`, y SOLO `active` da acceso. Se inserta como
    `pending` ANTES de enviar el cobro a la cadena, y YA CON su firma (ver `tx_signature`), así
    que un fallo del envío no regala acceso y además deja algo concreto que reconciliar.
    """
    __tablename__ = "tracker_passes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    wallet: Mapped[str] = mapped_column(String, index=True)
    days: Mapped[int] = mapped_column(Integer)
    #: Lo cobrado, congelado. Si el precio cambia mañana, lo que se pagó no se reescribe.
    price_base_units: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="pending")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: La fila NACE con esta columna ya rellena, no al revés. La firma de una transacción de
    #: Solana viaja DENTRO de la propia transacción ya firmada (no la inventa el RPC), así que se
    #: lee en local y se inserta la fila `pending` con ella ANTES de enviar nada: nunca existe un
    #: `pending` sin firma. Un `pending` con firma significa "se cobró y no se sabe si se activó"
    #: —no "no se activó" a secas: `confirmar_firma` puede devolver `None` en vez de un
    #: veredicto— y es lo que hace ese hueco reconciliable: la siguiente compra de la misma
    #: wallet vuelve a preguntar sola antes de rendirse (ver `gacha_tracker_pass` en app/main.py).
    tx_signature: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AirdropClaim(Base):
    """Constancia de un claim de airdrop hecho desde la app.

    No manda sobre nada: la elegibilidad la decide el fichero de asignaciones y si está
    reclamado lo decide la cadena, porque el jugador puede haber reclamado en la web de
    CC sin pasar por aquí. Esta fila existe para poder reenseñarle la firma cuando
    vuelva y para saber cuánto SOL nos ha costado la operación.
    """
    __tablename__ = "airdrop_claims"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(String, index=True)
    ronda: Mapped[str] = mapped_column(String, index=True)
    amount: Mapped[int] = mapped_column(Integer)          # unidades base de CARDS (6 decimales)
    signature: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
