from datetime import datetime, timezone

import pytest

from app.models import BattlePlayer, GachaPack, PackBattle
from app.services.badges import RANKS, progress_for, rank_for, wagered_by_wallet
from app.services.royale_funding import royale_buyin
from app.services.users import read_user_stats


def _battle(s, bid, wallets, *, price=50_000_000, status="settled", mode="pack", max_players=2):
    s.add(PackBattle(id=bid, mode=mode, machine_code="pokemon_50", price=price,
                     max_players=max_players, status=status))
    s.add_all([BattlePlayer(battle_id=bid, player_wallet=w) for w in wallets])


def test_ranks_are_the_agreed_six_in_order():
    assert RANKS == [("bronze", "Bronze", 500), ("silver", "Silver", 2_500),
                     ("gold", "Gold", 10_000), ("platinum", "Platinum", 25_000),
                     ("diamond", "Diamond", 50_000), ("obsidian", "Obsidian", 100_000)]


@pytest.mark.parametrize("usd,expected", [
    (0, None), (499.99, None), (500, "bronze"), (2_499.99, "bronze"), (2_500, "silver"),
    (10_000, "gold"), (25_000, "platinum"), (50_000, "diamond"), (100_000, "obsidian"),
    (10_000_000, "obsidian"),
])
def test_rank_for_edges(usd, expected):
    assert rank_for(usd) == expected


def test_progress_below_bronze_points_at_bronze():
    assert progress_for(120.0) == {"wagered_usd": 120.0, "rank": None,
                                   "next_rank": "bronze", "next_threshold_usd": 500}


def test_progress_mid_rank_points_at_the_next():
    assert progress_for(7_340.0) == {"wagered_usd": 7_340.0, "rank": "silver",
                                     "next_rank": "gold", "next_threshold_usd": 10_000}


def test_progress_at_obsidian_has_no_next():
    p = progress_for(150_000.0)
    assert p["rank"] == "obsidian" and p["next_rank"] is None and p["next_threshold_usd"] is None


def test_wager_counts_only_settled_battles(Session):
    with Session() as s:
        _battle(s, "b1", ["W1", "W2"])                          # settled, 50
        _battle(s, "b2", ["W1"], status="lobby")                # not settled
        _battle(s, "b3", ["W1"], status="refunded")             # not settled
        s.commit()
        assert wagered_by_wallet(s, ["W1", "W2"]) == {"W1": 50.0, "W2": 50.0}


def test_wager_ignores_gacha(Session):
    with Session() as s:
        s.add(GachaPack(memo="g1", wallet="W1", pack_type="pokemon_50",
                        opened_at=datetime.now(timezone.utc), nft_address="N1",
                        price=50_000_000, insured_value=10.0, name="X"))
        s.commit()
        assert wagered_by_wallet(s, ["W1"]) == {"W1": 0.0}


def test_royale_counts_the_full_buyin(Session):
    with Session() as s:
        _battle(s, "r1", ["W1"], mode="royale", max_players=4)
        s.commit()
        assert wagered_by_wallet(s, ["W1"])["W1"] == royale_buyin(4, 50_000_000) / 1_000_000


def test_wallets_without_battles_map_to_zero(Session):
    with Session() as s:
        assert wagered_by_wallet(s, ["NOBODY"]) == {"NOBODY": 0.0}
        assert wagered_by_wallet(s, []) == {}


def test_wager_matches_the_profile_total(Session):
    """The emblem and the profile's "Total wagered" must never disagree."""
    with Session() as s:
        _battle(s, "b1", ["W1", "W2"], price=25_000_000)
        _battle(s, "b2", ["W1"], price=250_000_000)
        _battle(s, "r1", ["W1"], mode="royale", max_players=10)
        s.commit()
        assert wagered_by_wallet(s, ["W1"])["W1"] == read_user_stats(s, "W1")["totalWageredUsd"]
