from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    # El pool se dimensiona contra el del SERVIDOR, no a ojo. FastAPI ejecuta en un pool de 40
    # hilos todo lo que no es async —las dependencias síncronas, `run_in_threadpool`— y cada uno
    # de esos hilos puede pedir su propia conexión. Con el valor por defecto de SQLAlchemy (5 + 10
    # de overflow = 15) sobran 25 hilos capaces de quedarse esperando: piden conexión, aguantan 30
    # segundos y mueren con `QueuePool limit of size 5 overflow 10 reached`.
    #
    # No es teórico: pasó 630 veces en tres días en mainnet (17/09). Y el daño no se queda en quien
    # provocó la carga —el EV tracker— porque los hilos bloqueados son los mismos que necesita todo
    # lo demás: el chat dejó de cargar y hasta /health tardaba más de 30 s.
    #
    # 40 + 10 deja margen por encima del pool de hilos, así que la espera por conexión deja de ser
    # un modo de fallo. Para SQLite abrir una conexión es barato —es un fichero, no una red— y las
    # lecturas van en paralelo; lo que sigue serializándose son las escrituras, que es cosa suya y
    # no del pool.
    # Solo para una base EN FICHERO. Con `:memory:` —la de los tests— SQLAlchemy usa
    # SingletonThreadPool, que no acepta estos parámetros y revienta con TypeError al construir el
    # engine: 348 tests en rojo por dimensionar un pool que ahí ni existe.
    if database_url.startswith("sqlite") and ":memory:" not in database_url:
        motor = create_engine(database_url, connect_args=connect_args,
                              pool_size=40, max_overflow=10)
        _ajustar_sqlite(motor)
        return motor
    return create_engine(database_url, connect_args=connect_args)


def _ajustar_sqlite(motor) -> None:
    """WAL y espera ante bloqueo, en cada conexión que se abra.

    La base venía en `journal_mode=delete` y `busy_timeout=0`, que es la peor combinación posible
    para lo que hace este servidor: un escritor bloquea a TODOS los lectores, y quien se encuentra
    la base bloqueada falla EN EL ACTO en vez de esperar un momento.

    Con el ingestor del EV tracker escribiendo cada cinco segundos sobre 283 MB, eso se tradujo en
    `database is locked` por todas partes: 29 backups fallidos en un día —incluido el que hace el
    despliegue antes de tocar nada, que por eso abortaba— y lecturas largas reventando a mitad.

      · WAL: los lectores dejan de bloquear al escritor y viceversa. Es exactamente el caso de uso
        para el que existe: un escritor constante y muchos lectores.
      · busy_timeout=5000: ante un bloqueo se espera hasta 5 s en vez de rendirse al instante.
        La mayoría de los choques duran milisegundos.

    NO se toca `synchronous`. Bajarlo a NORMAL es lo que se suele hacer junto con WAL y acelera
    las escrituras, pero abre una ventana de pérdida de las últimas transacciones ante un corte de
    corriente — y esto es una base con dinero, en un mini PC que todavía no tiene SAI.
    """
    @event.listens_for(motor, "connect")
    def _pragmas(conexion, _registro):  # noqa: ANN001
        cur = conexion.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Idempotent column additions for the migration-less SQLite dev DB. create_all() creates
# new TABLES but never adds COLUMNS to pre-existing ones, so we guard each new column with
# an ADD COLUMN that runs only when the column is missing. Extend this list when adding columns.
_ENSURE_COLUMNS = [
    # DECIMAL, not integer: with the gacha at 0.01 per dollar, a 50 $ pack is worth half a point,
    # and rounding used to leave it at zero right on the two most played machines. Databases that
    # already exist do NOT need a migration: SQLite types by affinity, and a column declared
    # INTEGER stores 0.5 as is (checked: `typeof` returns `real`), because it only converts to an
    # integer when nothing is lost. The FLOAT is for the ones created from now on.
    ("users", "gimmighouls", "FLOAT NOT NULL DEFAULT 0"),
    ("users", "referred_by", "VARCHAR"),
    ("users", "withdraw_address", "VARCHAR"),
    ("users", "emote_slots", "VARCHAR"),
    ("pack_battles", "gimmighouls_awarded", "BOOLEAN NOT NULL DEFAULT 0"),
    ("pack_battles", "rematch_battle_id", "VARCHAR"),
    ("pack_battles", "fee_base_units", "INTEGER"),
    ("pack_battles", "fee_pct", "FLOAT"),
    ("pack_battles", "fee_charged", "BOOLEAN NOT NULL DEFAULT 0"),
    ("battle_pulls", "tx_signature", "VARCHAR"),
    ("chat_messages", "wallet", "VARCHAR"),
    ("chat_messages", "mentions", "VARCHAR"),
    ("battle_players", "buyin_paid", "INTEGER NOT NULL DEFAULT 0"),
    ("battle_players", "refund_amount", "INTEGER"),
    ("battle_players", "refunded_at", "DATETIME"),
    ("gacha_packs", "price", "INTEGER"),
    ("gacha_packs", "insured_value", "FLOAT"),
    ("gacha_packs", "name", "VARCHAR"),
    ("gacha_packs", "submitted_at", "DATETIME"),
    ("gacha_packs", "revealed_at", "DATETIME"),
    ("gacha_packs", "rarity", "VARCHAR"),
    ("gacha_packs", "auto_sold", "BOOLEAN NOT NULL DEFAULT 0"),
    ("gacha_packs", "buyback_amount", "INTEGER"),
    ("referral_codes", "rake_share_pct", "FLOAT NOT NULL DEFAULT 0.25"),
    ("chat_messages", "kind", "VARCHAR NOT NULL DEFAULT 'user'"),
    ("chat_messages", "action", "VARCHAR"),
    ("chat_messages", "event", "VARCHAR"),
    ("chat_messages", "amount_usd", "FLOAT"),
    ("chat_messages", "machine", "VARCHAR"),
    ("chat_messages", "mult", "FLOAT"),
    ("battle_pulls", "refunded", "BOOLEAN NOT NULL DEFAULT 0"),
    ("battle_players", "seen_at", "DATETIME"),
]


# Índices que hay que poder crear sobre tablas que YA existen. create_all solo crea tablas
# ausentes: si la tabla estaba, sus índices nuevos no aparecen nunca. `CREATE ... IF NOT EXISTS`
# lo hace idempotente, igual que _ENSURE_COLUMNS con las columnas.
_ENSURE_INDEXES = [
    # Un referidor no puede cobrar dos veces por el mismo jugador en la misma batalla.
    ("referral_earnings", "uq_earning_battle_referred",
     "CREATE UNIQUE INDEX IF NOT EXISTS uq_earning_battle_referred "
     "ON referral_earnings (battle_id, referred_wallet)"),
    # Buscar por wallet sin distinguir mayúsculas (base58: nadie recuerda dónde iban) necesita este
    # índice de expresión — igual que ux_users_alias_lower, sin él la rama de wallet de
    # buscar_usuarios haría SCAN de la tabla entera.
    ("users", "ux_users_wallet_lower",
     "CREATE INDEX IF NOT EXISTS ux_users_wallet_lower ON users (lower(wallet))"),
    # ux_users_alias_lower vive en __table_args__ del modelo, pero create_all() NUNCA toca una
    # tabla que ya existe: en una base creada antes de que ese índice existiera, no aparece solo.
    # Todo buscar_usuarios depende de él (los dos caminos vuelven a SCAN sin él) — mismo motivo que
    # el de wallet, así que va aquí también. Único, igual que en el modelo.
    ("users", "ux_users_alias_lower",
     "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_alias_lower ON users (lower(alias))"),
    # The tracker access gate asks "does this wallet have an active pass that has not expired?"
    # on every load. Without this composite index it would be a SCAN of the whole tracker_passes.
    ("tracker_passes", "ix_tracker_passes_wallet_status_ends",
     "CREATE INDEX IF NOT EXISTS ix_tracker_passes_wallet_status_ends "
     "ON tracker_passes (wallet, status, ends_at)"),
    # Two purchase requests at once from the same wallet cannot charge twice. The endpoint
    # already checks "is there a pending one?" before inserting, but that is a check-then-act:
    # between the SELECT and the INSERT there is room for a second request to pass the same
    # check. This index is the atomic part of that guarantee; without it, the app's check is
    # only a suggestion. Partial (`WHERE status = 'pending'`) on purpose: a wallet accumulates
    # many `active`/`failed` rows in its history, and can only have ONE purchase midway at a
    # time.
    ("tracker_passes", "uq_tracker_passes_pending_wallet",
     "CREATE UNIQUE INDEX IF NOT EXISTS uq_tracker_passes_pending_wallet "
     "ON tracker_passes (wallet) WHERE status = 'pending'"),
]


def _ensure_indexes(engine):
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, _name, ddl in _ENSURE_INDEXES:
            if table in existing:
                conn.execute(text(ddl))


def _ensure_columns(engine):
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _ENSURE_COLUMNS:
            if table not in existing_tables:
                continue  # create_all just made it with the column already present
            cols = {c["name"] for c in insp.get_columns(table)}
            if column not in cols:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}'))


def _backfill_gacha_price(engine):
    """Best-effort: set price for already-opened gacha packs (opened before price tracking) from the
    number in pack_type (e.g. 'pokemon_50' → $50), so past gacha spend still counts toward the wager.
    Only fills NULLs → idempotent. Packs with no number in the code (e.g. 'pokemon_cnft') stay null."""
    import re
    insp = inspect(engine)
    if "gacha_packs" not in set(insp.get_table_names()):
        return
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT memo, pack_type FROM gacha_packs WHERE opened_at IS NOT NULL AND price IS NULL"
        )).fetchall()
        for memo, pack_type in rows:
            m = re.search(r"(\d+)", pack_type or "")
            if m:
                conn.execute(text("UPDATE gacha_packs SET price = :p WHERE memo = :m"),
                             {"p": int(m.group(1)) * 1_000_000, "m": memo})


def _backfill_refunded(engine):
    """One-shot al añadir battle_pulls.refunded: las batallas ya terminadas se dan por
    saldadas (sus refunds ocurrieron antes de existir el flag). Solo se llama cuando la
    columna acaba de crearse — ver init_db."""
    insp = inspect(engine)
    if "battle_pulls" not in set(insp.get_table_names()):
        return
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE battle_pulls SET refunded = 1 WHERE battle_id IN "
            "(SELECT id FROM pack_battles WHERE status IN ('settled', 'voided', 'cancelled'))"
        ))


def _backfill_battles_seen(engine):
    """One-shot al añadir battle_players.seen_at: todas las batallas que ya existían se dan por
    VISTAS. Son historia — el jugador ya las jugó, y sin esto el modal de 'sin ver' listaría
    toda su trayectoria. Solo se llama cuando la columna acaba de crearse — ver init_db."""
    insp = inspect(engine)
    if "battle_players" not in set(insp.get_table_names()):
        return
    with engine.begin() as conn:
        conn.execute(text("UPDATE battle_players SET seen_at = CURRENT_TIMESTAMP WHERE seen_at IS NULL"))


def init_db(engine):
    # importa los modelos para registrarlos en Base.metadata antes de create_all
    from . import models  # noqa: F401
    insp = inspect(engine)
    had_refunded = ("battle_pulls" in set(insp.get_table_names())
                    and "refunded" in {c["name"] for c in insp.get_columns("battle_pulls")})
    had_seen = ("battle_players" in set(insp.get_table_names())
                and "seen_at" in {c["name"] for c in insp.get_columns("battle_players")})
    Base.metadata.create_all(engine)
    _ensure_columns(engine)
    _ensure_indexes(engine)
    if not had_refunded:
        _backfill_refunded(engine)
    if not had_seen:
        _backfill_battles_seen(engine)
    _backfill_gacha_price(engine)
