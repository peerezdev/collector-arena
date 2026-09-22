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
