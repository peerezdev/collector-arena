from __future__ import annotations

from typing import Optional
from sqlalchemy import select, desc, func
from sqlalchemy.orm import Session
from ..models import User, RatingHistory
from .badges import entry_base_units as _entry_base_units


class AliasTakenError(Exception):
    """Otro usuario ya tiene ese username (case-insensitive)."""


# Tope duro de resultados. Un desplegable no puede enseñar miles, y el tope es también lo que
# mantiene barata la consulta pase lo que pase por el parámetro.
MAX_BUSQUEDA = 8


def _rango_prefijo(q: str) -> tuple[str, str]:
    """(desde, hasta) para buscar por prefijo con un RANGO, que es lo único que usa el índice.

    Medido con EXPLAIN QUERY PLAN: `lower(alias) LIKE 'an%'` hace SCAN de la tabla entera, porque
    SQLite no aplica la optimización de LIKE a un índice de expresión como ux_users_alias_lower.
    El rango sí: SEARCH users USING INDEX. Con 16 usuarios da igual; con 100.000, un escaneo por
    pulsación deja al backend (un proceso, consultas síncronas) sin atender nada más.
    """
    return q, q + "￿"


def buscar_usuarios(session: Session, q: str, limit: int = MAX_BUSQUEDA) -> list[dict]:
    """Jugadores cuyo alias o wallet EMPIEZA por `q` (sin distinguir mayúsculas en ninguno de los
    dos: la wallet es base58 y nadie recuerda dónde iban las mayúsculas).

    Sin `q`, los primeros CON alias, ordenados por él. A quien no tiene alias se le sigue
    encontrando escribiendo su wallet (la rama de arriba); en una lista sin filtrar aparecería
    como una dirección suelta que nadie reconoce, y el `IS NULL` que haría falta para mandarlos al
    final es justo lo que le impide a esta rama usar el índice (ver test_la_consulta_usa_el_indice).

    Devuelve [{wallet, alias}]; quién está conectado lo pone el endpoint, que es quien lo sabe.
    """
    limit = max(1, min(limit, MAX_BUSQUEDA))
    stmt = select(User)
    if q:
        desde, hasta = _rango_prefijo(q.lower())
        stmt = stmt.where(
            (func.lower(User.alias) >= desde) & (func.lower(User.alias) < hasta)
            | ((func.lower(User.wallet) >= desde) & (func.lower(User.wallet) < hasta))
        )
        stmt = stmt.order_by(func.lower(User.alias).is_(None), func.lower(User.alias), User.wallet)
    else:
        # `isnot(None)` era correcto pero caro: el plan arranca por el PRINCIPIO del índice, que es
        # justo donde SQLite ordena los NULL, así que para las primeras 8 filas CON alias tenía que
        # pasar antes por cada fila SIN alias (medido: pasos en el orden de cuántos usuarios no
        # tienen alias, no de cuántos hacen falta). `> ""` es una cota inferior real, no un rodeo:
        # el alias se valida en el endpoint con min_length=3 (AliasBody, app/main.py), así que
        # ningún alias es la cadena vacía y la cota no descarta ningún resultado válido. Con la cota
        # el plan pasa a SEARCH (arranca ya después de los NULL) y el coste queda plano.
        stmt = stmt.where(func.lower(User.alias) > "")
        stmt = stmt.order_by(func.lower(User.alias))
    return [{"wallet": u.wallet, "alias": u.alias} for u in session.scalars(stmt.limit(limit))]


def read_user_view(session: Session, wallet: str, elo_start: int) -> dict:
    """Lectura sin efectos: devuelve el usuario si existe, o una vista por defecto (sin persistir)."""
    u = session.get(User, wallet)
    if u is None:
        return {"wallet": wallet, "alias": None, "elo": elo_start, "games_played": 0,
                "gimmighouls": 0, "referred_by": None, "withdraw_address": None}
    return {"wallet": u.wallet, "alias": u.alias, "elo": u.elo, "games_played": u.games_played,
            "gimmighouls": u.gimmighouls, "referred_by": u.referred_by, "withdraw_address": u.withdraw_address}


def get_or_create_user(session: Session, wallet: str, elo_start: int) -> User:
    user = session.get(User, wallet)
    if user is None:
        user = User(wallet=wallet, elo=elo_start, games_played=0)
        session.add(user)
        session.flush()
    return user


def set_alias(session: Session, wallet: str, alias: str) -> None:
    user = session.get(User, wallet)
    if user is None:
        raise ValueError("user does not exist")
    clash = session.scalar(
        select(User).where(func.lower(User.alias) == alias.lower(), User.wallet != wallet)
    )
    if clash is not None:
        raise AliasTakenError(alias)
    user.alias = alias


def leaderboard(session: Session, limit: int = 50) -> list[User]:
    return list(session.scalars(
        select(User).order_by(desc(User.gimmighouls), desc(User.elo)).limit(limit)
    ))


def history(session: Session, wallet: str) -> list[RatingHistory]:
    return list(session.scalars(
        select(RatingHistory).where(RatingHistory.wallet == wallet).order_by(desc(RatingHistory.ts))
    ))


def read_user_stats(session: Session, wallet: str) -> dict:
    """Aggregate profile stats from settled battles + pulls. Computed on read (no schema):
    battles/wins/win_rate/total_wagered, the best single card pulled, and the biggest loot
    (combined insured value) of a battle the wallet won."""
    from ..models import PackBattle, BattlePlayer, BattlePull, GachaPack
    USDC = 1_000_000  # USDC base units → dollars

    battles = list(session.scalars(
        select(PackBattle)
        .join(BattlePlayer, BattlePlayer.battle_id == PackBattle.id)
        .where(BattlePlayer.player_wallet == wallet, PackBattle.status == "settled")
    ))
    n_battles = len(battles)
    wins = sum(1 for b in battles if b.winner == wallet)
    # Wager = SOLO lo apostado en batallas (pack + royale). El gacha NO cuenta.
    #
    # Sumaba también los sobres de gacha, y el número quedaba sin significado: se enseña al lado de
    # BATTLES, WINS y WIN RATE, así que se lee como "cuánto he apostado en estas partidas", pero
    # incluía compras que no son una apuesta contra nadie. En devnet el gacha era el 16% de la
    # cifra; en mainnet, el 99,7% — o sea, la métrica de batallas la dominaba el gacha.
    #
    # No se pierde nada: el gasto de gacha sigue en `gacha_packs`, y el historial lo sigue
    # mostrando pack a pack con su neto (valor − coste).
    wagered_usd = sum(_entry_base_units(b) for b in battles) / USDC

    # best hit — la mejor carta de TODAS: pack battle, royale y gacha.
    #
    # Miraba solo battle_pulls, así que una carta sacada en el gacha no podía ser la mejor por buena
    # que fuese. Para el jugador es la misma acción — abrir un sobre — y separarlas hacía que su
    # mejor tirón no apareciese en su propio perfil.
    #
    # Un sobre de gacha SIN abrir no compite: todavía no se sabe qué hay dentro, y contarlo sería
    # enseñar una carta que su dueño aún no ha visto. El gacha no guarda `grade` ni `year`, así que
    # esos van a None y la tarjeta los omite.
    best_pull = session.scalars(
        select(BattlePull)
        .where(BattlePull.player_wallet == wallet, BattlePull.insured_value.isnot(None))
        .order_by(desc(BattlePull.insured_value)).limit(1)
    ).first()
    best_pack = session.scalars(
        select(GachaPack)
        .where(GachaPack.wallet == wallet, GachaPack.opened_at.isnot(None),
               GachaPack.insured_value.isnot(None))
        .order_by(desc(GachaPack.insured_value)).limit(1)
    ).first()

    candidatos = []
    if best_pull is not None:
        candidatos.append({"name": best_pull.name, "grade": best_pull.grade,
                           "rarity": best_pull.rarity, "year": best_pull.year,
                           "valueUsd": best_pull.insured_value,
                           "nftAddress": best_pull.nft_address, "source": "battle"})
    if best_pack is not None:
        candidatos.append({"name": best_pack.name, "grade": None,
                           "rarity": best_pack.rarity, "year": None,
                           "valueUsd": best_pack.insured_value,
                           "nftAddress": best_pack.nft_address, "source": "gacha"})
    best_hit = max(candidatos, key=lambda c: c["valueUsd"]) if candidatos else None

    # best victory — biggest combined loot (all cards) of a battle this wallet won
    best_victory = None
    for b in battles:
        if b.winner != wallet:
            continue
        loot = session.scalar(
            select(func.coalesce(func.sum(BattlePull.insured_value), 0.0))
            .where(BattlePull.battle_id == b.id)
        ) or 0.0
        if best_victory is None or loot > best_victory["amountUsd"]:
            opponents = [w for (w,) in session.execute(
                select(BattlePlayer.player_wallet)
                .where(BattlePlayer.battle_id == b.id, BattlePlayer.player_wallet != wallet)
            )]
            # La mejor carta de ESA partida. El importe de arriba es el botín entero, que no
            # cuenta nada de lo que se ganó: la tarjeta enseñaba un trofeo genérico donde debía ir
            # la carta.
            mejor = session.scalars(
                select(BattlePull)
                .where(BattlePull.battle_id == b.id, BattlePull.insured_value.isnot(None))
                .order_by(desc(BattlePull.insured_value)).limit(1)
            ).first()
            best_victory = {"amountUsd": loot, "mode": b.mode, "machineCode": b.machine_code,
                            "opponents": opponents,
                            "bestCard": None if mejor is None else {
                                "name": mejor.name, "grade": mejor.grade, "rarity": mejor.rarity,
                                "year": mejor.year, "valueUsd": mejor.insured_value,
                                "nftAddress": mejor.nft_address}}

    return {
        "wallet": wallet,
        "battles": n_battles,
        "wins": wins,
        "winRate": (wins / n_battles) if n_battles else 0.0,
        "totalWageredUsd": wagered_usd,
        "bestHit": best_hit,
        "bestVictory": best_victory,
    }


from datetime import datetime, timezone


def read_unseen_battles(session: Session, wallet: str, limit: int = 30) -> list[dict]:
    """Batallas TERMINADAS (settled) o ANULADAS (voided) en las que el jugador participó y cuyo
    resultado todavía no ha visto (BattlePlayer.seen_at is null).

    Se guía por seen_at, no por el estado de la batalla: si mirara solo el estado, listaría las
    que el jugador acaba de ver en directo. Es la misma lección del gacha (opened_at vs
    revealed_at) — 'terminada' no es lo mismo que 'vista'.
    """
    from ..models import PackBattle, BattlePlayer, BattlePull
    USDC = 1_000_000

    rows = list(session.execute(
        select(PackBattle, BattlePlayer.id)
        .join(BattlePlayer, BattlePlayer.battle_id == PackBattle.id)
        .where(BattlePlayer.player_wallet == wallet,
               BattlePlayer.seen_at.is_(None),
               PackBattle.status.in_(("settled", "voided", "cancelled")))
        .order_by(desc(PackBattle.settled_at), desc(PackBattle.created_at))
        .limit(limit)
    ))
    out = []
    for b, _ in rows:
        # Anulada o cancelada por el creador: en ambas se devuelve la entrada y no hay resultado
        # que enseñar. Se listan igual para que el jugador se entere de que su partida ya no existe
        # — sin esto, una batalla a la que se unió desaparecía sin explicación.
        refunded = b.status in ("voided", "cancelled")
        won = (not refunded) and b.winner == wallet
        if refunded:
            amount = _entry_base_units(b) / USDC          # entrada devuelta (informativo)
        elif won:
            amount = (session.scalar(
                select(func.coalesce(func.sum(BattlePull.insured_value), 0.0))
                .where(BattlePull.battle_id == b.id)) or 0.0)
        else:
            amount = -(_entry_base_units(b) / USDC)
        out.append({
            "battle_id": b.id, "mode": b.mode, "machine_code": b.machine_code,
            "status": b.status, "won": won, "amount_usd": amount,
            "settled_at": (b.settled_at or b.created_at).isoformat() if (b.settled_at or b.created_at) else None,
        })
    return out


def mark_battles_seen(session: Session, wallet: str, battle_ids: list[str]) -> int:
    """Marca como vistas las filas del jugador en esas batallas. Idempotente y acotado a su propia
    wallet: solo toca sus BattlePlayer, nunca los de otros. Devuelve cuántas marcó."""
    from ..models import BattlePlayer
    if not battle_ids:
        return 0
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(BattlePlayer)
        .where(BattlePlayer.player_wallet == wallet,
               BattlePlayer.battle_id.in_(battle_ids),
               BattlePlayer.seen_at.is_(None))
    ).scalars().all()
    for r in rows:
        r.seen_at = now
    if rows:
        session.commit()
    return len(rows)


def read_user_battles(session: Session, wallet: str, limit: int = 20) -> list[dict]:
    """The wallet's most recent activity for the History tab: settled battles + gacha opens, newest
    first. amountUsd is signed — battle win = combined loot, battle loss = minus the entry buy-in,
    gacha = the pulled card's value minus what the pack cost."""
    from ..models import PackBattle, BattlePlayer, BattlePull, GachaPack
    USDC = 1_000_000

    battles = list(session.scalars(
        select(PackBattle)
        .join(BattlePlayer, BattlePlayer.battle_id == PackBattle.id)
        .where(BattlePlayer.player_wallet == wallet, PackBattle.status == "settled")
        .order_by(desc(PackBattle.settled_at), desc(PackBattle.created_at))
        .limit(limit)
    ))
    out = []
    for b in battles:
        won = b.winner == wallet
        if won:
            amount = session.scalar(
                select(func.coalesce(func.sum(BattlePull.insured_value), 0.0))
                .where(BattlePull.battle_id == b.id)
            ) or 0.0
        else:
            amount = -(_entry_base_units(b) / USDC)
        cards = session.scalar(
            select(func.count()).select_from(BattlePull)
            .where(BattlePull.battle_id == b.id, BattlePull.player_wallet == wallet)
        ) or 0
        opponents = [w for (w,) in session.execute(
            select(BattlePlayer.player_wallet)
            .where(BattlePlayer.battle_id == b.id, BattlePlayer.player_wallet != wallet)
        )]
        out.append({
            "kind": "battle",
            "battleId": b.id, "mode": b.mode, "machineCode": b.machine_code,
            "result": "win" if won else "loss", "amountUsd": amount,
            "cards": cards, "opponents": opponents,
            "ts": (b.settled_at or b.created_at).timestamp() if (b.settled_at or b.created_at) else None,
        })

    # Gacha opens — amount = pulled card value minus what the pack cost. Only opens where we captured
    # the card value show in history (older, pre-tracking opens still count toward the wager).
    gacha = list(session.scalars(
        select(GachaPack)
        .where(GachaPack.wallet == wallet, GachaPack.opened_at.isnot(None), GachaPack.insured_value.isnot(None))
        .order_by(desc(GachaPack.opened_at)).limit(limit)
    ))
    for g in gacha:
        value = g.insured_value or 0.0
        spent = (g.price or 0) / USDC
        out.append({
            "kind": "gacha",
            # `battleId` lleva el memo desde siempre —es lo que hace de clave de la fila— pero ese
            # nombre no dice nada en una tirada de gacha. Se manda TAMBIÉN como `memo`, que es lo
            # que entienden el replay y el VRF, para que quien lo use no tenga que saberse la
            # coincidencia. El campo viejo se queda: hay UI que ya lo usa como key.
            "battleId": g.memo, "memo": g.memo,
            "mode": "gacha", "machineCode": g.pack_type,
            "result": "gacha", "amountUsd": value - spent,
            "pullName": g.name, "pullValue": value, "spentUsd": spent,
            "cards": 1, "opponents": [],
            "ts": g.opened_at.timestamp() if g.opened_at else None,
        })

    # Merge battles + gacha, newest first, cap at `limit`.
    out.sort(key=lambda r: r["ts"] or 0, reverse=True)
    return out[:limit]


def set_withdraw_address(session: Session, wallet: str, address: Optional[str]) -> None:
    user = session.get(User, wallet)
    if user is None:
        raise ValueError("user does not exist")
    user.withdraw_address = address
