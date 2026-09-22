"""Rank emblems: what a player has wagered in battles, over their whole history.

The rank is derived on read, never stored. It MUST use the same per-battle amount and the same set
of battles as the profile's "Total wagered" (`read_user_stats`), or a player could see $9,990
wagered next to a Gold emblem. That is why `entry_base_units` lives here and `users.py` imports it.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BattlePlayer, PackBattle

USDC = 1_000_000

# (id, name, threshold in USD), ascending. The only place that knows the ranks.
RANKS: list[tuple[str, str, int]] = [
    ("bronze", "Bronze", 500),
    ("silver", "Silver", 2_500),
    ("gold", "Gold", 10_000),
    ("platinum", "Platinum", 25_000),
    ("diamond", "Diamond", 50_000),
    ("obsidian", "Obsidian", 100_000),
]


def entry_base_units(b: PackBattle) -> int:
    """USDC (base units) each player wagered in a battle: the pack price for a pack battle, but the
    full buy-in for a royale (b.price is only the per-box price there, not what the player paid)."""
    if b.mode == "royale":
        from .royale_funding import royale_buyin  # lazy: keep solana deps out of module import
        return royale_buyin(b.max_players, b.price)
    return b.price


def rank_for(wagered_usd: float) -> Optional[str]:
    """The highest rank reached, or None under the first threshold."""
    reached = None
    for rid, _name, threshold in RANKS:
        if wagered_usd >= threshold:
            reached = rid
    return reached


def progress_for(wagered_usd: float) -> dict:
    """Current rank and the next one to reach. At the top rank there is no next."""
    rank = rank_for(wagered_usd)
    nxt = next(((rid, t) for rid, _n, t in RANKS if wagered_usd < t), None)
    return {"wagered_usd": wagered_usd, "rank": rank,
            "next_rank": nxt[0] if nxt else None,
            "next_threshold_usd": nxt[1] if nxt else None}


def wagered_by_wallet(session: Session, wallets: list[str]) -> dict[str, float]:
    """Lifetime battle wager in USD per wallet, in ONE query. Wallets with nothing map to 0.0."""
    totals = {w: 0 for w in wallets}
    if not wallets:
        return {}
    rows = session.execute(
        select(BattlePlayer.player_wallet, PackBattle)
        .join(PackBattle, PackBattle.id == BattlePlayer.battle_id)
        .where(BattlePlayer.player_wallet.in_(wallets), PackBattle.status == "settled")
    ).all()
    for wallet, battle in rows:
        totals[wallet] += entry_base_units(battle)
    return {w: units / USDC for w, units in totals.items()}


def badges_for(session: Session, wallets: list[str]) -> dict[str, dict]:
    """Rank and tags for each wallet. Two queries whatever the number of wallets."""
    from .user_tags import tags_by_wallet
    wagers = wagered_by_wallet(session, wallets)
    tags = tags_by_wallet(session, wallets)
    return {w: {"rank": rank_for(wagers[w]), "tags": tags[w]} for w in wallets}


def profile_badges(session: Session, wallet: str) -> dict:
    """What the profile page shows: rank, tags and the progress towards the next rank."""
    from .user_tags import tags_by_wallet
    progress = progress_for(wagered_by_wallet(session, [wallet])[wallet])
    return {"rank": progress["rank"],
            "tags": tags_by_wallet(session, [wallet])[wallet],
            "rank_progress": {"wagered_usd": progress["wagered_usd"],
                              "next_rank": progress["next_rank"],
                              "next_threshold_usd": progress["next_threshold_usd"]}}
