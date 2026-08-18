"""Gimmighouls loyalty points + referral codes.

Pure, testable service functions over a SQLAlchemy Session. Gimmighouls are loyalty
points; the USDC→Gimmighoul ratio is a single project-wide constant in config.

A referral code gives the referred user a % BOOST on the Gimmighouls they earn, and
the code's owner a % CUT of those base earnings. Example: boost 10%, referrer 10%,
base 100 → user gets 110, referrer gets 10.
"""
from __future__ import annotations

from typing import Optional
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import User, ReferralCode


class ReferralError(Exception):
    """Invalid referral operation (unknown code, already referred, ...)."""


def get_referral_code(session: Session, code: str) -> Optional[ReferralCode]:
    return session.get(ReferralCode, code)


def create_referral_code(session: Session, code: str, name: str,
                         boost_pct: float = 0.0, referrer_pct: float = 0.0,
                         owner_wallet: Optional[str] = None) -> ReferralCode:
    rc = ReferralCode(code=code, name=name, boost_pct=boost_pct,
                      referrer_pct=referrer_pct, owner_wallet=owner_wallet)
    session.add(rc)
    session.flush()
    return rc


def _get_or_create_user(session: Session, wallet: str) -> User:
    u = session.get(User, wallet)
    if u is None:
        u = User(wallet=wallet, elo=get_settings().elo_start, games_played=0)
        session.add(u)
        session.flush()
    return u


USDC = 1_000_000  # USDC base units per dollar (6 decimals)

#: Decimales que se guardan de un premio. La coma flotante binaria no sabe escribir 1.1, así que
#: 100 $ a 0.5 con un boost del 10% da 110.00000000000001 en vez de 110. Recortar aquí deja el
#: contador en el número que saldría a mano. Seis decimales están muy por debajo del premio más
#: pequeño que se puede ganar (0.01 por dólar sobre 1 $ es 0.01), así que no se pierde nada real.
DECIMALES = 6


def award_gimmighouls(session: Session, wallet: str, buyin_base_units: float, ratio: float | None = None) -> float:
    """Credit a participant their loyalty points for a completed spend (settled battle or gacha open).

    `buyin_base_units` is the USDC amount in base units (6 decimals). base = (buyin / 1e6) * ratio, so
    the ratio is per-dollar (ratio defaults to the battles rate; gacha passes its own lower rate). If
    the user has a valid referral code, they earn base * (1 + boost_pct) and the referrer earns
    base * referrer_pct (credited to owner_wallet's User, or to ReferralCode.earned as a fallback).
    Returns the amount credited to the user.

    Lo que se acredita son DECIMALES. El contador era entero, y con el gacha a 0.01 por dólar dejó
    de valer: un sobre de 50 $ da medio punto, `round(0.5)` es 0, y las dos máquinas más jugadas
    (las de 25 y 50 $) habrían dejado de pagar nada mientras la ayuda prometía 0.01 por dólar.
    Tampoco se paga de más: lo único que se recorta es la basura binaria, ver `DECIMALES`.
    """
    if ratio is None:
        ratio = get_settings().gimmighoul_per_usdc
    base = (buyin_base_units / USDC) * ratio

    user = _get_or_create_user(session, wallet)
    code = get_referral_code(session, user.referred_by) if user.referred_by else None

    if code is not None:
        bruto = base * (1 + code.boost_pct)
        referrer_cut = base * code.referrer_pct
        if referrer_cut > 0:
            if code.owner_wallet:
                owner = _get_or_create_user(session, code.owner_wallet)
                owner.gimmighouls = round((owner.gimmighouls or 0.0) + referrer_cut, DECIMALES)
            else:
                code.earned = round((code.earned or 0.0) + referrer_cut, DECIMALES)
    else:
        bruto = base

    user_amount = round(bruto, DECIMALES)
    user.gimmighouls = round((user.gimmighouls or 0.0) + user_amount, DECIMALES)
    session.flush()
    return user_amount


def award_battle_loyalty(session: Session, battle, player_wallets, buyin_usdc: float) -> bool:
    """Idempotent settle hook: award each participant once per battle.

    Uses battle.gimmighouls_awarded as the guard, so buy-in+cancel yields nothing (cancel
    never settles) and re-running settle never double-credits. Returns True if it awarded.
    """
    if getattr(battle, "gimmighouls_awarded", False):
        return False
    for w in player_wallets:
        award_gimmighouls(session, w, buyin_usdc)
    battle.gimmighouls_awarded = True
    session.flush()
    return True


def apply_referral_code(session: Session, wallet: str, code: str) -> dict:
    """Register `wallet` under `code`. Cannot be changed once set. Raises ReferralError."""
    user = _get_or_create_user(session, wallet)
    if user.referred_by:
        raise ReferralError("already_referred")
    rc = get_referral_code(session, code)
    if rc is None:
        raise ReferralError("invalid_code")
    user.referred_by = code
    session.flush()
    return {"code": rc.code, "boost_pct": rc.boost_pct}
