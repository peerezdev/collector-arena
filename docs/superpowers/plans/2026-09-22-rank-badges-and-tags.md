# Rank Emblems and User Tags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a rank emblem (by lifetime battle wager) and hand-assigned tags such as `TEAM` next to player names in the lobby chat and on the profile page.

**Architecture:** The backend derives the rank on read from settled battles (same per-battle function as the profile's "Total wagered") and stores tags in a new `user_tags` table managed by a console script. The frontend fetches rank and tags in batches from a new `GET /users/badges` endpoint through a module-level batcher with a 5 minute cache, and renders them with a shared `NombreUsuario` component. The profile gets rank, tags and progress from the existing `GET /users/{wallet}`, extended.

**Tech Stack:** FastAPI + SQLAlchemy 2 (SQLite), pytest; React 18 + TypeScript, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-22-rank-badges-and-tags-design.md`

## Global Constraints

- Ranks (USD, lifetime, settled Pack Battle + Battle Royale only, gacha never counts): Bronze 500 · Silver 2,500 · Gold 10,000 · Platinum 25,000 · Diamond 50,000 · Obsidian 100,000. Below 500: no rank.
- Rank ids: `bronze`, `silver`, `gold`, `platinum`, `diamond`, `obsidian`.
- The wager MUST be summed with the same per-battle function the profile uses (`entry_base_units`, the full buy-in for a Royale) over `PackBattle.status == "settled"`.
- Tags: upper case, `^[A-Z0-9]{2,12}$`, many per wallet, never the same twice.
- `GET /users/badges`: at most 100 wallets per call, 422 above that. Registered before `GET /users/{wallet}`.
- The chat websocket and chat storage are NOT changed.
- If badges fail to load, names render exactly as today. A name is never held back waiting for its emblem.
- New code, comments, test names and commit messages are in English. Commits end with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- Backend commands run from `backend/` with `.venv/bin/python -m pytest`. Frontend tests run from the repo root with `npx vitest run`.
- Backend endpoints that query the database are plain `def` (FastAPI runs them in a thread pool). An `async def` handler with synchronous queries blocks the event loop; that took production down once (see `src/ui/useAliases.ts`).

## File map

Backend:
- Create `backend/app/services/badges.py`: ranks, `entry_base_units`, `rank_for`, `progress_for`, `wagered_by_wallet`, `badges_for`, `profile_badges`.
- Create `backend/app/services/user_tags.py`: tag validation and storage.
- Modify `backend/app/models.py`: add `UserTag`.
- Modify `backend/app/services/users.py`: import `entry_base_units` from `badges.py` instead of defining it.
- Modify `backend/app/main.py`: add `GET /users/badges`; extend `GET /users/{wallet}`.
- Create `backend/scripts/tags.py`.
- Tests: `backend/tests/test_badges.py`, `backend/tests/test_user_tags.py`, `backend/tests/test_badges_api.py`.

Frontend:
- Create `src/ui/badges/ranks.ts` (+ `ranks.test.ts`).
- Create `src/ui/badges/EmblemaRango.tsx` (+ test).
- Create `src/ui/badges/TagUsuario.tsx`.
- Create `src/ui/badges/useBadges.ts` (+ test).
- Create `src/ui/badges/NombreUsuario.tsx` (+ test).
- Modify `src/ui/screens/Hub/ChatDock.tsx` (`Autor`) and `ChatDock.test.tsx`.
- Modify `src/hooks/useProfile.ts`.
- Create `src/ui/screens/Profile/RankProgress.tsx` (+ test).
- Modify `src/ui/screens/Profile/ProfilePage.tsx` and `ProfilePage.test.tsx`.

---

### Task 1: Rank computation service

**Files:**
- Create: `backend/app/services/badges.py`
- Modify: `backend/app/services/users.py` (remove `_entry_base_units` at lines 105-111, import it instead)
- Test: `backend/tests/test_badges.py`

**Interfaces:**
- Produces:
  - `RANKS: list[tuple[str, str, int]]` — `(id, name, threshold_usd)` ascending.
  - `entry_base_units(b: PackBattle) -> int`
  - `rank_for(wagered_usd: float) -> str | None`
  - `progress_for(wagered_usd: float) -> dict` with keys `wagered_usd`, `rank`, `next_rank`, `next_threshold_usd`.
  - `wagered_by_wallet(session, wallets: list[str]) -> dict[str, float]`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_badges.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_badges.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.badges'`

- [ ] **Step 3: Write the service**

Create `backend/app/services/badges.py`:

```python
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
```

In `backend/app/services/users.py`, delete the `_entry_base_units` function (lines 105-111) and add under the existing imports at the top:

```python
from .badges import entry_base_units as _entry_base_units
```

The three existing call sites (`read_user_stats` and `read_user_battles`) keep calling `_entry_base_units` unchanged.

- [ ] **Step 4: Run the new tests and the existing user tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_badges.py tests/test_users.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/badges.py backend/app/services/users.py backend/tests/test_badges.py
git commit -m "feat(badges): rank from lifetime battle wager, same sum as the profile

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Tags table and service

**Files:**
- Modify: `backend/app/models.py` (add `UserTag` after `AppFlag`)
- Create: `backend/app/services/user_tags.py`
- Test: `backend/tests/test_user_tags.py`

**Interfaces:**
- Produces:
  - model `UserTag` (table `user_tags`, PK `(wallet, tag)`, `created_at`)
  - `normalize_tag(tag: str) -> str` — raises `ValueError("invalid_tag")`
  - `validate_wallet(wallet: str) -> str` — raises `ValueError("invalid_wallet")`
  - `add_tag(session, wallet: str, tag: str) -> bool` — True if added, False if it was already there
  - `remove_tag(session, wallet: str, tag: str) -> bool` — True if removed, False if absent
  - `list_tags(session, wallet: str | None = None) -> list[UserTag]`
  - `tags_by_wallet(session, wallets: list[str]) -> dict[str, list[str]]` — sorted tags, every requested wallet present

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_user_tags.py`:

```python
import pytest

from app.services.user_tags import (add_tag, list_tags, normalize_tag, remove_tag,
                                    tags_by_wallet, validate_wallet)

W = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"
W2 = "3q6Ucr1s7Knkp5nRQKQe3dYPzoh72XQGnn2oCgSS9S34"


def test_normalize_upper_cases():
    assert normalize_tag(" team ") == "TEAM"


@pytest.mark.parametrize("bad", ["", "T", "TOOLONGTAGNAME1", "TE AM", "TEAM!", "ñu"])
def test_normalize_rejects_bad_text(bad):
    with pytest.raises(ValueError, match="invalid_tag"):
        normalize_tag(bad)


def test_validate_wallet_rejects_non_solana():
    with pytest.raises(ValueError, match="invalid_wallet"):
        validate_wallet("not-a-wallet")
    assert validate_wallet(W) == W


def test_add_is_idempotent_and_stored_upper_case(Session):
    with Session() as s:
        assert add_tag(s, W, "team") is True
        assert add_tag(s, W, "TEAM") is False
        assert [t.tag for t in list_tags(s, W)] == ["TEAM"]


def test_a_wallet_can_have_several_tags(Session):
    with Session() as s:
        add_tag(s, W, "TEAM")
        add_tag(s, W, "MOD")
        assert tags_by_wallet(s, [W]) == {W: ["MOD", "TEAM"]}


def test_remove_reports_whether_something_was_removed(Session):
    with Session() as s:
        add_tag(s, W, "TEAM")
        assert remove_tag(s, W, "team") is True
        assert remove_tag(s, W, "TEAM") is False


def test_tags_by_wallet_includes_wallets_without_tags(Session):
    with Session() as s:
        add_tag(s, W, "TEAM")
        assert tags_by_wallet(s, [W, W2]) == {W: ["TEAM"], W2: []}
        assert tags_by_wallet(s, []) == {}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_user_tags.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.user_tags'`

- [ ] **Step 3: Add the model**

In `backend/app/models.py`, after the `AppFlag` class:

```python
class UserTag(Base):
    """A hand-assigned label shown next to a player's name (e.g. TEAM for the house's accounts).

    No foreign key to `users`: a house wallet can be tagged before it ever logs in.
    Managed with `scripts/tags.py`.
    """
    __tablename__ = "user_tags"
    wallet: Mapped[str] = mapped_column(String, primary_key=True)
    tag: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

`init_db` creates it through `create_all`; no entry in `_ENSURE_COLUMNS` is needed because the table is new.

- [ ] **Step 4: Write the service**

Create `backend/app/services/user_tags.py`:

```python
"""Hand-assigned tags shown next to a player's name. Written only by `scripts/tags.py`."""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import UserTag

_TAG = re.compile(r"^[A-Z0-9]{2,12}$")


def normalize_tag(tag: str) -> str:
    t = (tag or "").strip().upper()
    if not _TAG.match(t):
        raise ValueError("invalid_tag")
    return t


def validate_wallet(wallet: str) -> str:
    from solders.pubkey import Pubkey
    try:
        Pubkey.from_string(wallet)
    except ValueError:
        raise ValueError("invalid_wallet")
    return wallet


def add_tag(session: Session, wallet: str, tag: str) -> bool:
    t = normalize_tag(tag)
    if session.get(UserTag, (wallet, t)) is not None:
        return False
    session.add(UserTag(wallet=wallet, tag=t))
    session.commit()
    return True


def remove_tag(session: Session, wallet: str, tag: str) -> bool:
    row = session.get(UserTag, (wallet, normalize_tag(tag)))
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def list_tags(session: Session, wallet: Optional[str] = None) -> list[UserTag]:
    q = select(UserTag).order_by(UserTag.wallet, UserTag.tag)
    if wallet:
        q = q.where(UserTag.wallet == wallet)
    return list(session.scalars(q))


def tags_by_wallet(session: Session, wallets: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {w: [] for w in wallets}
    if not wallets:
        return out
    for row in session.scalars(select(UserTag).where(UserTag.wallet.in_(wallets))
                               .order_by(UserTag.tag)):
        out[row.wallet].append(row.tag)
    return out
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_user_tags.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/services/user_tags.py backend/tests/test_user_tags.py
git commit -m "feat(tags): user_tags table and the service that manages it

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Console script for tags

**Files:**
- Create: `backend/scripts/tags.py`
- Test: `backend/tests/test_tags_script.py`

**Interfaces:**
- Consumes: `add_tag`, `remove_tag`, `list_tags`, `validate_wallet`, `normalize_tag` from Task 2.
- Produces: `main(argv) -> int` with subcommands `add <wallet> <tag>`, `remove <wallet> <tag>`, `list [wallet]`; a module-level `_session()` the tests replace.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_tags_script.py`:

```python
from scripts import tags as script
from app.services.user_tags import tags_by_wallet

W = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"


def _use(monkeypatch, Session):
    monkeypatch.setattr(script, "_session", lambda: Session())


def test_add_then_list(monkeypatch, Session, capsys):
    _use(monkeypatch, Session)
    assert script.main(["add", W, "team"]) == 0
    assert script.main(["add", W, "TEAM"]) == 0          # idempotent
    assert script.main(["list"]) == 0
    assert "TEAM" in capsys.readouterr().out
    with Session() as s:
        assert tags_by_wallet(s, [W]) == {W: ["TEAM"]}


def test_remove_of_a_missing_tag_fails(monkeypatch, Session):
    _use(monkeypatch, Session)
    assert script.main(["remove", W, "TEAM"]) == 1


def test_invalid_wallet_and_tag_are_rejected(monkeypatch, Session):
    _use(monkeypatch, Session)
    assert script.main(["add", "nope", "TEAM"]) == 2
    assert script.main(["add", W, "BAD TAG"]) == 2
    with Session() as s:
        assert tags_by_wallet(s, [W]) == {W: []}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tags_script.py -v`
Expected: FAIL with `ImportError: cannot import name 'tags' from 'scripts'`

- [ ] **Step 3: Write the script**

Create `backend/scripts/tags.py`:

```python
"""Tags shown next to a player's name in the chat and on the profile (e.g. TEAM).

Usage (from backend/):
  PYTHONPATH=. .venv/bin/python3 scripts/tags.py add <wallet> TEAM
  PYTHONPATH=. .venv/bin/python3 scripts/tags.py remove <wallet> TEAM
  PYTHONPATH=. .venv/bin/python3 scripts/tags.py list [<wallet>]

With APP_NETWORK=mainnet it works on mainnet. Tags are 2 to 12 letters or digits, stored upper case.
The frontend caches badges for 5 minutes, so a change can take that long to show.
"""
import argparse
import sys

from app.config import get_settings
from app.db import init_db, make_engine, make_session_factory
from app.services.user_tags import (add_tag, list_tags, normalize_tag, remove_tag,
                                    validate_wallet)
from scripts._destino import anunciar


def _session():
    st = get_settings()
    anunciar(st)
    engine = make_engine(st.database_url)
    init_db(engine)
    return make_session_factory(engine)()


def _checked(args) -> bool:
    try:
        validate_wallet(args.wallet)
        normalize_tag(args.tag)
        return True
    except ValueError as e:
        print(f"rejected: {e}", file=sys.stderr)
        return False


def cmd_add(args) -> int:
    if not _checked(args):
        return 2
    s = _session()
    try:
        added = add_tag(s, args.wallet, args.tag)
        print(f"{'added' if added else 'already had'} {normalize_tag(args.tag)} on {args.wallet}")
        return 0
    finally:
        s.close()


def cmd_remove(args) -> int:
    if not _checked(args):
        return 2
    s = _session()
    try:
        if remove_tag(s, args.wallet, args.tag):
            print(f"removed {normalize_tag(args.tag)} from {args.wallet}")
            return 0
        print(f"{args.wallet} has no {normalize_tag(args.tag)} tag", file=sys.stderr)
        return 1
    finally:
        s.close()


def cmd_list(args) -> int:
    s = _session()
    try:
        rows = list_tags(s, args.wallet)
        if not rows:
            print("(no tags)")
            return 0
        for r in rows:
            print(f"{r.wallet:<46}{r.tag:<14}{str(r.created_at)[:19]}")
        return 0
    finally:
        s.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="manage the tags shown next to player names")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add", help="give a wallet a tag")
    pa.add_argument("wallet")
    pa.add_argument("tag")
    pa.set_defaults(func=cmd_add)

    pr = sub.add_parser("remove", help="take a tag from a wallet")
    pr.add_argument("wallet")
    pr.add_argument("tag")
    pr.set_defaults(func=cmd_remove)

    pl = sub.add_parser("list", help="list tags, optionally for one wallet")
    pl.add_argument("wallet", nargs="?")
    pl.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

`backend/scripts/` has no `__init__.py`; it imports as a namespace package (the scripts already do `from scripts._destino import …`), so `from scripts import tags` works from the tests without adding one.

- [ ] **Step 4: Run the tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tags_script.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/tags.py backend/tests/test_tags_script.py
git commit -m "feat(tags): console script to add, remove and list tags

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```


---

### Task 4: API — batched badges and the extended user endpoint

**Files:**
- Modify: `backend/app/services/badges.py` (add `badges_for`, `profile_badges`)
- Modify: `backend/app/main.py` (new route right after `GET /users/search`, around line 412; change `GET /users/{wallet}` at line 458)
- Test: `backend/tests/test_badges_api.py`

**Interfaces:**
- Consumes: `wagered_by_wallet`, `rank_for`, `progress_for` (Task 1); `tags_by_wallet` (Task 2).
- Produces:
  - `badges_for(session, wallets) -> dict[str, {"rank": str|None, "tags": list[str]}]`
  - `profile_badges(session, wallet) -> {"rank", "tags", "rank_progress": {"wagered_usd", "next_rank", "next_threshold_usd"}}`
  - `GET /users/badges?wallets=a,b` → `{wallet: {"rank", "tags"}}`
  - `GET /users/{wallet}` gains `rank`, `tags`, `rank_progress`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_badges_api.py`:

```python
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.db import init_db, make_session_factory
from app.main import create_app
from app.models import BattlePlayer, PackBattle
from app.services.gacha import GachaService
from app.services.user_tags import add_tag
from tests.test_chain_mock import MockChainSource

A = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"
B = "3q6Ucr1s7Knkp5nRQKQe3dYPzoh72XQGnn2oCgSS9S34"


def _client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)
    app = create_app(sf, MockChainSource(),
                     gacha=GachaService(base_url="https://dev-gacha.example.com", api_key=""),
                     solana_rpc_url="https://api.devnet.solana.com")
    c = TestClient(app, raise_server_exceptions=True)
    c.session_factory = sf
    return c


def _wager(c, wallet, usd):
    with c.session_factory() as s:
        s.add(PackBattle(id=f"b-{wallet}", mode="pack", machine_code="pokemon_50",
                         price=int(usd * 1_000_000), max_players=2, status="settled"))
        s.add(BattlePlayer(battle_id=f"b-{wallet}", player_wallet=wallet))
        s.commit()


def test_badges_for_several_wallets_in_one_call():
    c = _client()
    _wager(c, A, 12_000)
    with c.session_factory() as s:
        add_tag(s, A, "TEAM")
    r = c.get(f"/users/badges?wallets={A},{B}")
    assert r.status_code == 200
    assert r.json() == {A: {"rank": "gold", "tags": ["TEAM"]}, B: {"rank": None, "tags": []}}


def test_badges_ignores_duplicates_and_empty_entries():
    c = _client()
    r = c.get(f"/users/badges?wallets={A},,{A}, ")
    assert r.json() == {A: {"rank": None, "tags": []}}


def test_badges_with_no_wallets_is_empty():
    assert _client().get("/users/badges").json() == {}


def test_badges_caps_the_batch_at_100():
    wallets = ",".join(f"W{i}" for i in range(101))
    assert _client().get(f"/users/badges?wallets={wallets}").status_code == 422


def test_badges_is_not_taken_for_a_wallet():
    """/users/badges must be registered before /users/{wallet}, or it answers as a wallet."""
    body = _client().get("/users/badges?wallets=X").json()
    assert "alias" not in body and body == {"X": {"rank": None, "tags": []}}


def test_user_endpoint_carries_rank_tags_and_progress():
    c = _client()
    _wager(c, A, 7_340)
    with c.session_factory() as s:
        add_tag(s, A, "TEAM")
    u = c.get(f"/users/{A}").json()
    assert u["rank"] == "silver"
    assert u["tags"] == ["TEAM"]
    assert u["rank_progress"] == {"wagered_usd": 7_340.0, "next_rank": "gold",
                                  "next_threshold_usd": 10_000}
    assert "alias" in u and "elo" in u          # nothing removed


def test_user_endpoint_for_an_unknown_wallet_still_has_badges():
    u = _client().get(f"/users/{B}").json()
    assert u["rank"] is None and u["tags"] == []
    assert u["rank_progress"]["next_rank"] == "bronze"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_badges_api.py -v`
Expected: FAIL (404 or `alias` present for `/users/badges`, `KeyError: 'rank'` for the user endpoint).

- [ ] **Step 3: Add the two service functions**

Append to `backend/app/services/badges.py`:

```python
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
```

- [ ] **Step 4: Add the routes**

In `backend/app/main.py`, add to the imports from services:

```python
from .services.badges import badges_for, profile_badges
```

Right after the `GET /users/search` handler (it starts around line 412) and BEFORE `@app.get("/users/{wallet}")`:

```python
    # MUST stay above /users/{wallet}: FastAPI matches routes in order, and "badges" is a valid
    # path segment for {wallet}. Plain `def`, not `async def`: the queries are synchronous, and an
    # async handler would run them on the event loop and stall every other request meanwhile.
    @app.get("/users/badges")
    def get_badges(wallets: str = "", s: Session = Depends(db)):
        pedidas = list(dict.fromkeys(w.strip() for w in wallets.split(",") if w.strip()))
        if len(pedidas) > 100:
            raise HTTPException(422, "too_many_wallets")
        return badges_for(s, pedidas)
```

Replace the existing `GET /users/{wallet}` handler (line 458):

```python
    @app.get("/users/{wallet}")
    def get_user(wallet: str, s: Session = Depends(db)):
        # Rank and tags only here, NOT inside read_user_view: that one runs in hot loops (battle
        # settlement, chat announcements) that only need the alias.
        return {**read_user_view(s, wallet, elo_start), **profile_badges(s, wallet)}
```

- [ ] **Step 5: Run the new tests and the whole backend suite**

Run: `cd backend && .venv/bin/python -m pytest tests/test_badges_api.py -v && .venv/bin/python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/badges.py backend/app/main.py backend/tests/test_badges_api.py
git commit -m "feat(badges): GET /users/badges in batches, and rank and tags on the profile

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Rank definitions and the emblem component

**Files:**
- Create: `src/ui/badges/ranks.ts`
- Create: `src/ui/badges/EmblemaRango.tsx`
- Create: `src/ui/badges/TagUsuario.tsx`
- Test: `src/ui/badges/ranks.test.ts`, `src/ui/badges/EmblemaRango.test.tsx`

**Interfaces:**
- Produces:
  - `type RankId = 'bronze' | 'silver' | 'gold' | 'platinum' | 'diamond' | 'obsidian'`
  - `RANKS: readonly { id: RankId; name: string; base: string; light: string; dark: string; text: string }[]`
  - `isRankId(x: unknown): x is RankId`
  - `rankInfo(id: RankId)` → the entry above
  - `starPoints(cx, cy, r1, r2): string`
  - `<EmblemaRango rank={RankId | string | null} size={number} />`
  - `<TagUsuario tag={string} />`

- [ ] **Step 1: Write the failing tests**

Create `src/ui/badges/ranks.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { RANKS, isRankId, rankInfo, starPoints } from './ranks'

describe('ranks', () => {
  it('has the six ranks in order', () => {
    expect(RANKS.map((r) => r.id)).toEqual(['bronze', 'silver', 'gold', 'platinum', 'diamond', 'obsidian'])
    expect(RANKS.map((r) => r.name)).toEqual(['Bronze', 'Silver', 'Gold', 'Platinum', 'Diamond', 'Obsidian'])
  })

  it('recognises only known ids', () => {
    expect(isRankId('gold')).toBe(true)
    expect(isRankId('ruby')).toBe(false)
    expect(isRankId(null)).toBe(false)
  })

  it('uses the light colour for text, violet for obsidian', () => {
    expect(rankInfo('gold').text).toBe('#ffe38c')
    expect(rankInfo('obsidian').text).toBe('#b99bff')
  })

  it('starts the star at the top and alternates radii', () => {
    const pts = starPoints(16, 16.5, 14, 6.2).split(' ')
    expect(pts).toHaveLength(10)
    expect(pts[0]).toBe('16.00,2.50')
  })
})
```

Create `src/ui/badges/EmblemaRango.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EmblemaRango } from './EmblemaRango'
import { RANKS } from './ranks'

describe('EmblemaRango', () => {
  it.each(RANKS.map((r) => [r.id, r.name]))('draws %s with its name as label', (id, name) => {
    render(<EmblemaRango rank={id} size={15} />)
    const img = screen.getByRole('img', { name })
    expect(img.getAttribute('width')).toBe('15')
  })

  it('draws nothing without a rank', () => {
    const { container } = render(<EmblemaRango rank={null} size={15} />)
    expect(container.innerHTML).toBe('')
  })

  it('draws nothing for an id it does not know', () => {
    const { container } = render(<EmblemaRango rank="ruby" size={15} />)
    expect(container.innerHTML).toBe('')
  })
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/ui/badges`
Expected: FAIL, modules not found.

- [ ] **Step 3: Write `ranks.ts`**

```ts
/**
 * The six rank emblems: ids, names and colours. Thresholds live in the backend
 * (`backend/app/services/badges.py`), which is the only one that decides a rank.
 */
export type RankId = 'bronze' | 'silver' | 'gold' | 'platinum' | 'diamond' | 'obsidian'

export interface RankInfo {
  id: RankId
  name: string
  base: string
  light: string
  dark: string
  /** Colour for the rank's name as text on the app background. */
  text: string
}

export const RANKS: readonly RankInfo[] = [
  { id: 'bronze', name: 'Bronze', base: '#c47f45', light: '#eab184', dark: '#6e3f1c', text: '#eab184' },
  { id: 'silver', name: 'Silver', base: '#b9c2cc', light: '#f1f4f7', dark: '#5d6773', text: '#f1f4f7' },
  { id: 'gold', name: 'Gold', base: '#f0bd3f', light: '#ffe38c', dark: '#8a6112', text: '#ffe38c' },
  { id: 'platinum', name: 'Platinum', base: '#9fd9d3', light: '#e6fbf8', dark: '#3f7c77', text: '#e6fbf8' },
  { id: 'diamond', name: 'Diamond', base: '#62c6ff', light: '#d6f3ff', dark: '#1d6aa3', text: '#d6f3ff' },
  // Its base is too dark to read on the app background, so its text uses the violet edge.
  { id: 'obsidian', name: 'Obsidian', base: '#2b2140', light: '#b99bff', dark: '#b99bff', text: '#b99bff' },
]

const BY_ID = new Map(RANKS.map((r) => [r.id, r]))

export function isRankId(x: unknown): x is RankId {
  return typeof x === 'string' && BY_ID.has(x as RankId)
}

export function rankInfo(id: RankId): RankInfo {
  return BY_ID.get(id)!
}

/** Five-point star: ten points alternating r1 and r2, the first one straight up. */
export function starPoints(cx: number, cy: number, r1: number, r2: number): string {
  const pts: string[] = []
  for (let i = 0; i < 10; i++) {
    const r = i % 2 === 0 ? r1 : r2
    const a = -Math.PI / 2 + (i * Math.PI) / 5
    pts.push(`${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`)
  }
  return pts.join(' ')
}

// Computed once, not on every render.
export const SILVER_STAR_OUTER = starPoints(16, 16.5, 14, 6.2)
export const SILVER_STAR_INNER = starPoints(16, 16.5, 6.5, 2.9)
```

- [ ] **Step 4: Write `EmblemaRango.tsx`**

```tsx
import { isRankId, rankInfo, SILVER_STAR_INNER, SILVER_STAR_OUTER, type RankId } from './ranks'

/**
 * A player's rank emblem. Each rank is a different OBJECT (medal, star, ingot, crystal, diamond,
 * obsidian shards), so ranks read by shape and not only by colour: at 15 px in the chat, and for
 * colour-blind players, colour alone is not enough.
 *
 * Draws nothing for no rank or an id it does not know (a rank added in the backend first).
 */
export function EmblemaRango({ rank, size }: { rank: RankId | string | null | undefined; size: number }) {
  if (!isRankId(rank)) return null
  const r = rankInfo(rank)
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label={r.name}
      style={{ flexShrink: 0, overflow: 'visible' }}>
      <title>{r.name}</title>
      <Shape id={rank} base={r.base} light={r.light} dark={r.dark} />
    </svg>
  )
}

function Shape({ id, base, light, dark }: { id: RankId; base: string; light: string; dark: string }) {
  switch (id) {
    case 'bronze':
      return (<>
        <circle cx="16" cy="16" r="12.5" fill={base} stroke={dark} strokeWidth="1.6" />
        <circle cx="16" cy="16" r="8" fill="none" stroke={light} strokeWidth="1.4" />
      </>)
    case 'silver':
      return (<>
        <polygon points={SILVER_STAR_OUTER} fill={base} stroke={dark} strokeWidth="1.5" strokeLinejoin="round" />
        <polygon points={SILVER_STAR_INNER} fill={light} />
      </>)
    case 'gold':
      return (<>
        <polygon points="9,9 23,9 29,23 3,23" fill={base} stroke={dark} strokeWidth="1.6" strokeLinejoin="round" />
        <polygon points="10.5,11 21.5,11 23,15 9,15" fill={light} />
      </>)
    case 'platinum':
      return (<>
        <polygon points="16,2 28,9 28,23 16,30 4,23 4,9" fill={base} stroke={dark} strokeWidth="1.6" strokeLinejoin="round" />
        <polygon points="16,2 28,9 16,16 4,9" fill={light} />
        <polyline points="16,16 16,30" fill="none" stroke={dark} strokeWidth="1" />
      </>)
    case 'diamond':
      return (<>
        <polygon points="9,5 23,5 30,12 16,29 2,12" fill={base} stroke={dark} strokeWidth="1.6" strokeLinejoin="round" />
        <polygon points="9,5 23,5 30,12 2,12" fill={light} />
        <path d="M11 12 L16 29 L21 12" fill="none" stroke={dark} strokeWidth="1" />
        <polyline points="11,12 16,5 21,12" fill="none" stroke={dark} strokeWidth="1" />
      </>)
    case 'obsidian':
      return (<>
        <polygon points="16,1 21,12 18,30 13,30 10,12" fill={base} stroke={light} strokeWidth="1.5" strokeLinejoin="round" />
        <polygon points="7,9 11,17 10,30 5,30 3,17" fill={base} stroke={light} strokeWidth="1.3" strokeLinejoin="round" />
        <polygon points="25,9 29,17 27,30 22,30 21,17" fill={base} stroke={light} strokeWidth="1.3" strokeLinejoin="round" />
        <polyline points="16,1 16,30" fill="none" stroke={light} strokeWidth="0.9" opacity="0.7" />
      </>)
  }
}
```

- [ ] **Step 5: Write `TagUsuario.tsx`**

```tsx
import { COLORS, FONTS } from '../theme'

/** A hand-assigned tag next to a name (TEAM, MOD…). Every tag uses this one style for now. */
export function TagUsuario({ tag }: { tag: string }) {
  return (
    <span style={{
      fontFamily: FONTS.mono, fontSize: 9.5, fontWeight: 700, letterSpacing: '.1em',
      color: COLORS.green, background: `${COLORS.green}1a`, border: `1px solid ${COLORS.green}55`,
      borderRadius: 4, padding: '1px 5px', lineHeight: 1.3, flexShrink: 0,
    }}>
      {tag}
    </span>
  )
}
```

- [ ] **Step 6: Run the tests**

Run: `npx vitest run src/ui/badges`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ui/badges
git commit -m "feat(badges): the six rank emblems as SVG, and the tag pill

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `useBadges` — batched, cached lookups

**Files:**
- Create: `src/ui/badges/useBadges.ts`
- Test: `src/ui/badges/useBadges.test.ts`

**Interfaces:**
- Consumes: `isRankId`, `RankId` (Task 5); `config.backendUrl` from `src/onchain/config`.
- Produces:
  - `interface Badges { rank: RankId | null; tags: string[] }`
  - `useBadges(wallets: string[]): Record<string, Badges>` — only wallets already resolved are present
  - `__resetBadgesForTests(): void`

Why a batcher and not a fetch per call: `NombreUsuario` calls the hook once per chat line. Without a module-level queue, a chat of 50 messages would fire 50 requests at once, the burst that took production down before (see the comment on `A_LA_VEZ` in `src/ui/useAliases.ts`).

- [ ] **Step 1: Write the failing tests**

Create `src/ui/badges/useBadges.test.ts`:

```ts
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useBadges, __resetBadgesForTests } from './useBadges'

const ok = (body: unknown) => ({ ok: true, json: async () => body })

beforeEach(() => __resetBadgesForTests())
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

describe('useBadges', () => {
  it('resolves rank and tags', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok({ A: { rank: 'gold', tags: ['TEAM'] } })))
    const { result } = renderHook(() => useBadges(['A']))
    await waitFor(() => expect(result.current.A).toEqual({ rank: 'gold', tags: ['TEAM'] }))
  })

  it('batches the wallets of many callers into one request', async () => {
    const f = vi.fn().mockResolvedValue(ok({ A: { rank: null, tags: [] }, B: { rank: null, tags: [] } }))
    vi.stubGlobal('fetch', f)
    const a = renderHook(() => useBadges(['A']))
    const b = renderHook(() => useBadges(['B']))
    await waitFor(() => expect(a.result.current.A).toBeDefined())
    await waitFor(() => expect(b.result.current.B).toBeDefined())
    expect(f).toHaveBeenCalledTimes(1)
    expect(String(f.mock.calls[0][0])).toContain('wallets=A%2CB')
  })

  it('splits more than 100 wallets into several requests', async () => {
    const f = vi.fn(async (url: string) => {
      const ws = decodeURIComponent(url.split('wallets=')[1]).split(',')
      return ok(Object.fromEntries(ws.map((w) => [w, { rank: null, tags: [] }])))
    })
    vi.stubGlobal('fetch', f)
    const wallets = Array.from({ length: 150 }, (_, i) => `W${i}`)
    const { result } = renderHook(() => useBadges(wallets))
    await waitFor(() => expect(Object.keys(result.current)).toHaveLength(150))
    expect(f).toHaveBeenCalledTimes(2)
  })

  it('does not ask again for a cached wallet', async () => {
    const f = vi.fn().mockResolvedValue(ok({ A: { rank: 'bronze', tags: [] } }))
    vi.stubGlobal('fetch', f)
    const first = renderHook(() => useBadges(['A']))
    await waitFor(() => expect(first.result.current.A).toBeDefined())
    const second = renderHook(() => useBadges(['A']))
    expect(second.result.current.A).toEqual({ rank: 'bronze', tags: [] })
    expect(f).toHaveBeenCalledTimes(1)
  })

  it('asks again once the cache entry is 5 minutes old', async () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    const f = vi.fn().mockResolvedValue(ok({ A: { rank: 'bronze', tags: [] } }))
    vi.stubGlobal('fetch', f)
    const first = renderHook(() => useBadges(['A']))
    await waitFor(() => expect(first.result.current.A).toBeDefined())
    now.mockReturnValue(1_000_000 + 5 * 60_000 + 1)
    renderHook(() => useBadges(['A']))
    await waitFor(() => expect(f).toHaveBeenCalledTimes(2))
  })

  it('shows nothing and caches nothing when the request fails', async () => {
    const f = vi.fn().mockRejectedValue(new Error('network'))
    vi.stubGlobal('fetch', f)
    const { result } = renderHook(() => useBadges(['A']))
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1))
    expect(result.current.A).toBeUndefined()
    f.mockResolvedValue(ok({ A: { rank: 'gold', tags: [] } }))
    const again = renderHook(() => useBadges(['A']))
    await waitFor(() => expect(again.result.current.A).toEqual({ rank: 'gold', tags: [] }))
  })

  it('treats an unknown rank id as no rank', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok({ A: { rank: 'ruby', tags: ['TEAM'] } })))
    const { result } = renderHook(() => useBadges(['A']))
    await waitFor(() => expect(result.current.A).toEqual({ rank: null, tags: ['TEAM'] }))
  })
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/ui/badges/useBadges.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 3: Write the hook**

Create `src/ui/badges/useBadges.ts`:

```ts
import { useEffect, useState } from 'react'
import { config } from '../../onchain/config'
import { isRankId, type RankId } from './ranks'

export interface Badges {
  rank: RankId | null
  tags: string[]
}

/** A rank or tag change shows up within this long without a reload. */
const TTL_MS = 5 * 60_000
/** The backend's limit per request (GET /users/badges answers 422 above it). */
const LOTE = 100
/** How long the queue waits to gather the wallets of every component rendering at once. */
const ESPERA_MS = 20

const cache = new Map<string, { badges: Badges; at: number }>()
const cola = new Set<string>()
const enVuelo = new Set<string>()
const oyentes = new Set<() => void>()
let temporizador: ReturnType<typeof setTimeout> | null = null

function fresco(w: string): Badges | undefined {
  const e = cache.get(w)
  return e && Date.now() - e.at <= TTL_MS ? e.badges : undefined
}

function avisar() {
  for (const o of oyentes) o()
}

async function pedirLote(wallets: string[]) {
  try {
    const url = `${config.backendUrl}/users/badges?wallets=${encodeURIComponent(wallets.join(','))}`
    const r = await fetch(url, { headers: { 'ngrok-skip-browser-warning': 'true' } })
    if (!r.ok) return
    const body = (await r.json()) as Record<string, { rank?: unknown; tags?: unknown }>
    const at = Date.now()
    for (const w of wallets) {
      const d = body[w]
      if (!d) continue
      cache.set(w, {
        at,
        badges: {
          rank: isRankId(d.rank) ? d.rank : null,
          tags: Array.isArray(d.tags) ? d.tags.filter((t): t is string => typeof t === 'string') : [],
        },
      })
    }
  } catch {
    // Nothing cached: the names render without emblems, and the next mount asks again.
  } finally {
    for (const w of wallets) enVuelo.delete(w)
    avisar()
  }
}

function vaciarCola() {
  temporizador = null
  const wallets = [...cola]
  cola.clear()
  for (let i = 0; i < wallets.length; i += LOTE) void pedirLote(wallets.slice(i, i + LOTE))
}

function encolar(wallets: string[]) {
  let alguna = false
  for (const w of wallets) {
    if (fresco(w) || enVuelo.has(w)) continue
    enVuelo.add(w)
    cola.add(w)
    alguna = true
  }
  if (alguna && !temporizador) temporizador = setTimeout(vaciarCola, ESPERA_MS)
}

function leer(wallets: string[]): Record<string, Badges> {
  const out: Record<string, Badges> = {}
  for (const w of wallets) {
    const b = fresco(w)
    if (b) out[w] = b
  }
  return out
}

/**
 * Rank and tags per wallet. Only wallets already resolved are in the result; a missing wallet
 * means "nothing to show yet", and callers render the bare name.
 */
export function useBadges(wallets: string[]): Record<string, Badges> {
  const key = wallets.join(',')
  const [vista, setVista] = useState(() => leer(wallets))

  useEffect(() => {
    const refrescar = () => setVista(leer(wallets))
    oyentes.add(refrescar)
    encolar(wallets)
    refrescar()
    return () => { oyentes.delete(refrescar) }
    // `key` captures the wallet list; the array identity changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return vista
}

export function __resetBadgesForTests() {
  cache.clear()
  cola.clear()
  enVuelo.clear()
  if (temporizador) clearTimeout(temporizador)
  temporizador = null
}
```

- [ ] **Step 4: Run the tests**

Run: `npx vitest run src/ui/badges/useBadges.test.ts`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ui/badges/useBadges.ts src/ui/badges/useBadges.test.ts
git commit -m "feat(badges): useBadges, one batched request for every name on screen

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `NombreUsuario` and the chat

**Files:**
- Create: `src/ui/badges/NombreUsuario.tsx`
- Test: `src/ui/badges/NombreUsuario.test.tsx`
- Modify: `src/ui/screens/Hub/ChatDock.tsx` (`Autor`, lines 24-32; import at the top)
- Modify: `src/ui/screens/Hub/ChatDock.test.tsx`

**Interfaces:**
- Consumes: `useBadges` (Task 6), `EmblemaRango`, `TagUsuario` (Task 5).
- Produces: `<NombreUsuario wallet={string} size={number}>{nameElement}</NombreUsuario>` — renders emblem, then the children unchanged, then tags. The emblem and tags sit OUTSIDE the children, so a link's accessible name stays the bare name.

- [ ] **Step 1: Write the failing component test**

Create `src/ui/badges/NombreUsuario.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

const estado = vi.hoisted(() => ({ badges: {} as Record<string, { rank: string | null; tags: string[] }> }))
vi.mock('./useBadges', () => ({ useBadges: () => estado.badges }))

import { NombreUsuario } from './NombreUsuario'

beforeEach(() => { estado.badges = {} })

describe('NombreUsuario', () => {
  it('shows emblem, name and tags', () => {
    estado.badges = { A: { rank: 'gold', tags: ['TEAM'] } }
    render(<NombreUsuario wallet="A" size={15}><a href="/profile/A">kairo</a></NombreUsuario>)
    expect(screen.getByRole('img', { name: 'Gold' })).toBeTruthy()
    expect(screen.getByText('TEAM')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'kairo' })).toBeTruthy()   // emblem not in the link
  })

  it('shows only the name while loading or after a failure', () => {
    render(<NombreUsuario wallet="A" size={15}><span>kairo</span></NombreUsuario>)
    expect(screen.getByText('kairo')).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('shows tags without a rank', () => {
    estado.badges = { A: { rank: null, tags: ['TEAM'] } }
    render(<NombreUsuario wallet="A" size={15}><span>kairo</span></NombreUsuario>)
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText('TEAM')).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/ui/badges/NombreUsuario.test.tsx`
Expected: FAIL, module not found.

- [ ] **Step 3: Write the component**

Create `src/ui/badges/NombreUsuario.tsx`:

```tsx
import type { ReactNode } from 'react'
import { EmblemaRango } from './EmblemaRango'
import { TagUsuario } from './TagUsuario'
import { useBadges } from './useBadges'

/**
 * A player's name with their rank emblem before it and their tags after it.
 *
 * The name itself is the caller's element (a link to the profile in the chat), passed as
 * children and left untouched: the emblem and tags sit outside it, so a link's accessible name
 * stays the bare name. Until badges arrive, or if they fail, only the name shows.
 */
export function NombreUsuario({ wallet, size, children }: { wallet: string; size: number; children: ReactNode }) {
  const b = useBadges([wallet])[wallet]
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
      <EmblemaRango rank={b?.rank ?? null} size={size} />
      {children}
      {b?.tags.map((t) => <TagUsuario key={t} tag={t} />)}
    </span>
  )
}
```

- [ ] **Step 4: Run the component test**

Run: `npx vitest run src/ui/badges/NombreUsuario.test.tsx`
Expected: PASS.

- [ ] **Step 5: Write the failing chat tests**

In `src/ui/screens/Hub/ChatDock.test.tsx`, extend the `vi.hoisted` object with a badges map and mock the hook. Add `badges` to the destructured hoisted names:

```ts
const { chatState, tipModalCalls, toasts, flags, busqueda, badges } = vi.hoisted(() => ({
  // …existing entries unchanged…
  badges: {} as Record<string, { rank: string | null; tags: string[] }>,
}))
vi.mock('../../badges/useBadges', () => ({ useBadges: () => badges }))
```

Add a test block. The file already has a `renderDock()` helper and sets messages through `chatState.messages` (see the `ChatDock · perfiles clicables` block around line 218):

```tsx
describe('ChatDock rank emblems and tags', () => {
  beforeEach(() => { for (const k of Object.keys(badges)) delete badges[k] })

  it('shows the emblem and tags of a ranked, tagged author, and keeps the link name', () => {
    badges['So1anaAAA111'] = { rank: 'gold', tags: ['TEAM'] }
    chatState.messages = [{ user: 'kairo', wallet: 'So1anaAAA111', text: 'hi', ts: 1 }]
    renderDock()
    expect(screen.getByRole('img', { name: 'Gold' })).toBeTruthy()
    expect(screen.getByText('TEAM')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'kairo' }).getAttribute('href')).toBe('/profile/So1anaAAA111')
  })

  it('shows neither on a message without a wallet', () => {
    chatState.messages = [{ user: 'House', text: 'hello', ts: 1 }]
    renderDock()
    expect(screen.queryByRole('img', { name: /bronze|silver|gold|platinum|diamond|obsidian/i })).toBeNull()
    expect(screen.queryByText('TEAM')).toBeNull()
  })
})
```


- [ ] **Step 6: Run the chat tests to verify the new ones fail**

Run: `npx vitest run src/ui/screens/Hub/ChatDock.test.tsx`
Expected: the two new tests FAIL (no emblem rendered); the existing ones PASS.

- [ ] **Step 7: Use `NombreUsuario` in `Autor`**

In `src/ui/screens/Hub/ChatDock.tsx`, add the import:

```ts
import { NombreUsuario } from '../../badges/NombreUsuario'
```

Replace `Autor` (lines 24-32) with:

```tsx
function Autor({ msg, style }: { msg: ChatLine; style: React.CSSProperties }) {
  if (!msg.wallet) return <span style={style}>{msg.user}</span>
  return (
    <NombreUsuario wallet={msg.wallet} size={15}>
      <Link to={`/profile/${encodeURIComponent(msg.wallet)}`} title={`View ${msg.user}'s profile`}
        style={{ ...style, textDecoration: 'none' }}>
        {msg.user}
      </Link>
    </NombreUsuario>
  )
}
```

Add one line to the comment above `Autor`: "The rank emblem and tags come from `NombreUsuario`; messages without a wallet have neither, since they belong to nobody."

- [ ] **Step 8: Run the chat tests and the badges folder**

Run: `npx vitest run src/ui/screens/Hub src/ui/badges`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add src/ui/badges/NombreUsuario.tsx src/ui/badges/NombreUsuario.test.tsx src/ui/screens/Hub/ChatDock.tsx src/ui/screens/Hub/ChatDock.test.tsx
git commit -m "feat(chat): rank emblem and tags next to each author

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Profile — emblem, tags and progress

**Files:**
- Modify: `src/hooks/useProfile.ts`
- Create: `src/ui/screens/Profile/RankProgress.tsx`
- Test: `src/ui/screens/Profile/RankProgress.test.tsx`
- Modify: `src/ui/screens/Profile/ProfilePage.tsx` (identity block, lines 73-95)
- Modify: `src/ui/screens/Profile/ProfilePage.test.tsx`

**Interfaces:**
- Consumes: `EmblemaRango`, `TagUsuario`, `isRankId`, `rankInfo`, `RankId` (Task 5); `formatUsd` from `src/ui/theme`; the `rank`, `tags`, `rank_progress` fields of `GET /users/{wallet}` (Task 4).
- Produces:
  - `ProfileData` gains `rank: RankId | null`, `tags: string[]`, `rankProgress: { wageredUsd: number; nextRank: RankId | null; nextThresholdUsd: number | null } | null`
  - `<RankProgress rank={RankId | null} progress={ProfileData['rankProgress']} />`

- [ ] **Step 1: Write the failing `RankProgress` tests**

Create `src/ui/screens/Profile/RankProgress.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RankProgress } from './RankProgress'

describe('RankProgress', () => {
  it('shows the rank name and the way to the next rank', () => {
    render(<RankProgress rank="silver" progress={{ wageredUsd: 7340, nextRank: 'gold', nextThresholdUsd: 10000 }} />)
    expect(screen.getByText('Silver')).toBeTruthy()
    expect(screen.getByText(/\$7,340 wagered/)).toBeTruthy()
    expect(screen.getByText(/\$10,000 for Gold/)).toBeTruthy()
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('73')
  })

  it('at Obsidian shows the name and no bar', () => {
    render(<RankProgress rank="obsidian" progress={{ wageredUsd: 150000, nextRank: null, nextThresholdUsd: null }} />)
    expect(screen.getByText('Obsidian')).toBeTruthy()
    expect(screen.queryByRole('progressbar')).toBeNull()
  })

  it('under 500 aims at Bronze with no rank name', () => {
    render(<RankProgress rank={null} progress={{ wageredUsd: 120, nextRank: 'bronze', nextThresholdUsd: 500 }} />)
    expect(screen.getByText(/\$500 for Bronze/)).toBeTruthy()
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('24')
  })

  it('renders nothing without progress data', () => {
    const { container } = render(<RankProgress rank={null} progress={null} />)
    expect(container.innerHTML).toBe('')
  })
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/ui/screens/Profile/RankProgress.test.tsx`
Expected: FAIL, module not found.

- [ ] **Step 3: Write `RankProgress.tsx`**

```tsx
import { COLORS, FONTS, formatUsd } from '../../theme'
import { EmblemaRango } from '../../badges/EmblemaRango'
import { rankInfo, type RankId } from '../../badges/ranks'

export interface RankProgressData {
  wageredUsd: number
  nextRank: RankId | null
  nextThresholdUsd: number | null
}

/**
 * The rank's name and how far the next one is. Shown on every profile, not only your own: the
 * total wagered is already public in the profile stats.
 */
export function RankProgress({ rank, progress }: { rank: RankId | null; progress: RankProgressData | null }) {
  if (!progress) return null
  const { wageredUsd, nextRank, nextThresholdUsd } = progress
  const pct = nextThresholdUsd ? Math.min(100, Math.floor((wageredUsd / nextThresholdUsd) * 100)) : 100
  return (
    <div style={{ maxWidth: 420, marginBottom: 14 }}>
      {rank && (
        <div style={{ fontSize: 13, fontWeight: 600, color: rankInfo(rank).text }}>{rankInfo(rank).name}</div>
      )}
      {nextRank && nextThresholdUsd != null && (
        <>
          <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
            aria-label={`Progress to ${rankInfo(nextRank).name}`}
            style={{ height: 8, borderRadius: 99, background: '#ffffff12', marginTop: 8, overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, height: '100%', background: rankInfo(nextRank).base }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginTop: 6,
                        fontFamily: FONTS.mono, fontSize: 11, color: COLORS.muted }}>
            <span>{formatUsd(wageredUsd)} wagered</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {formatUsd(nextThresholdUsd)} for {rankInfo(nextRank).name}
              <EmblemaRango rank={nextRank} size={14} />
            </span>
          </div>
        </>
      )}
    </div>
  )
}
```

Note: the next rank's emblem sits in the right-hand label, so the test's `getByText(/\$10,000 for Gold/)` matches the span even though it also holds the SVG.

- [ ] **Step 4: Run the `RankProgress` tests**

Run: `npx vitest run src/ui/screens/Profile/RankProgress.test.tsx`
Expected: PASS.

- [ ] **Step 5: Extend `useProfile`**

In `src/hooks/useProfile.ts`:

- Import: `import { isRankId, type RankId } from '../ui/badges/ranks'`
- Add to `ProfileData`:

```ts
  rank: RankId | null
  tags: string[]
  rankProgress: { wageredUsd: number; nextRank: RankId | null; nextThresholdUsd: number | null } | null
```

- Replace the two empty literals (initial state and the `!address` reset) with a shared constant:

```ts
const EMPTY: ProfileData = { username: null, elo: null, gamesPlayed: null, gimmighouls: null,
  withdrawAddress: null, rank: null, tags: [], rankProgress: null }
```

- In the `.then((u) => …)` mapping, add:

```ts
          rank: isRankId(u.rank) ? u.rank : null,
          tags: Array.isArray(u.tags) ? u.tags : [],
          rankProgress: u.rank_progress
            ? { wageredUsd: u.rank_progress.wagered_usd,
                nextRank: isRankId(u.rank_progress.next_rank) ? u.rank_progress.next_rank : null,
                nextThresholdUsd: u.rank_progress.next_threshold_usd ?? null }
            : null,
```

- [ ] **Step 6: Write the failing profile page test**

In `src/ui/screens/Profile/ProfilePage.test.tsx`, make the `useProfile` mock read from a hoisted, mutable object so one test can give the profile a rank:

```ts
const perfil = vi.hoisted(() => ({
  data: { username: null, elo: null, gamesPlayed: null, gimmighouls: null, withdrawAddress: null,
          rank: null, tags: [] as string[], rankProgress: null } as Record<string, unknown>,
}))
vi.mock('../../../hooks/useProfile', () => ({
  useProfile: () => ({ ...perfil.data, loading: false, refresh: () => {} }),
}))
```

(This replaces the existing `vi.mock('../../../hooks/useProfile', …)` call. Keep its comment about `username`.) Save the empty profile as `const VACIO = { ...perfil.data }` right after the hoisted block, and add `beforeEach(() => { perfil.data = { ...VACIO } })` inside the `describe`, importing `beforeEach` from vitest, so the ranked profile of one test does not leak into the others.

Add a test:

```tsx
  it('shows the emblem, tags and progress next to the name', () => {
    params.wallet = 'WalletB'
    perfil.data = { ...perfil.data, username: 'kairo', rank: 'silver', tags: ['TEAM'],
                    rankProgress: { wageredUsd: 7340, nextRank: 'gold', nextThresholdUsd: 10000 } }
    render(<ProfilePage />)
    expect(screen.getByRole('img', { name: 'Silver' })).toBeTruthy()
    expect(screen.getByText('TEAM')).toBeTruthy()
    expect(screen.getByRole('progressbar')).toBeTruthy()
  })
```

- [ ] **Step 7: Run it to verify it fails**

Run: `npx vitest run src/ui/screens/Profile/ProfilePage.test.tsx`
Expected: the new test FAILS (no emblem); the two existing tests PASS.

- [ ] **Step 8: Wire it into `ProfilePage`**

In `src/ui/screens/Profile/ProfilePage.tsx`:

- Imports:

```ts
import { EmblemaRango } from '../../badges/EmblemaRango'
import { TagUsuario } from '../../badges/TagUsuario'
import { RankProgress } from './RankProgress'
```

- Read the new fields: `const { username, rank, tags, rankProgress } = useProfile(target)`
- In the identity row, right after the `<h1>…{handle}</h1>` (line 74), add:

```tsx
              <EmblemaRango rank={rank} size={28} />
              {tags.map((t) => <TagUsuario key={t} tag={t} />)}
```

- Right after that row's closing `</div>` (the one with `marginBottom: 14`, before the `HeroStat` row), add:

```tsx
            <RankProgress rank={rank} progress={rankProgress} />
```

The profile reads badges from `useProfile`, not `useBadges`: it needs the progress too, and it already makes this request.

- [ ] **Step 9: Run the profile tests and the full frontend suite**

Run: `npx vitest run src/ui/screens/Profile && npx tsc --noEmit -p . && npx vitest run`
Expected: all PASS, no type errors.

- [ ] **Step 10: Commit**

```bash
git add src/hooks/useProfile.ts src/ui/screens/Profile/RankProgress.tsx src/ui/screens/Profile/RankProgress.test.tsx src/ui/screens/Profile/ProfilePage.tsx src/ui/screens/Profile/ProfilePage.test.tsx
git commit -m "feat(profile): rank emblem, tags and progress to the next rank

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Final check

- [ ] `cd backend && .venv/bin/python -m pytest -q` — all pass.
- [ ] From the repo root: `npx tsc --noEmit -p . && npx vitest run` — all pass.
- [ ] Manual, on devnet with the services up: tag a wallet with `PYTHONPATH=. .venv/bin/python3 scripts/tags.py add <wallet> TEAM`, reload the lobby, and check the emblem and TEAM next to that name in the chat and on its profile.
