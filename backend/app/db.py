from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Idempotent column additions for the migration-less SQLite dev DB. create_all() creates
# new TABLES but never adds COLUMNS to pre-existing ones, so we guard each new column with
# an ADD COLUMN that runs only when the column is missing. Extend this list when adding columns.
_ENSURE_COLUMNS = [
    # DECIMAL, no entero: con el gacha a 0.01 por dólar un sobre de 50 $ vale medio punto, y
    # redondeando se quedaba en cero justo en las dos máquinas más jugadas. Las bases de datos que ya
    # existen NO necesitan migración: SQLite tiene tipado por afinidad y una columna declarada
    # INTEGER guarda 0.5 tal cual (comprobado: `typeof` devuelve `real`), porque solo convierte a
    # entero cuando no se pierde nada. El FLOAT es para las que se creen a partir de ahora.
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
    # La puerta de acceso del tracker busca "¿tiene esta wallet un pase activo que no haya
    # caducado?" en cada carga. Sin este índice compuesto sería SCAN de tracker_passes entera.
    ("tracker_passes", "ix_tracker_passes_wallet_status_ends",
     "CREATE INDEX IF NOT EXISTS ix_tracker_passes_wallet_status_ends "
     "ON tracker_passes (wallet, status, ends_at)"),
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
