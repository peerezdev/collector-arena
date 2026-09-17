import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.main import create_app
from app.db import make_session_factory, init_db
from app.privy import PrivyVerifier
from app.chain.mock import MockChainSource
from tests.conftest import make_es256, privy_auth_headers

from app.models import User, ReferralCode, PackBattle
from app.services.referrals import (
    award_gimmighouls, apply_referral_code, create_referral_code,
    get_referral_code, ReferralError,
)
from app.services.users import leaderboard


@pytest.fixture
def unit_rate(monkeypatch):
    """Pin the battles loyalty rate to 1 G/USDC so the expected amounts read literally
    (the production default is 0.1; these tests document the mechanism, not the rate)."""
    import app.services.referrals as refmod
    from app.config import Settings
    monkeypatch.setattr(refmod, "get_settings", lambda: Settings(gimmighoul_per_usdc=1.0))


def test_award_no_code_base_only(Session, unit_rate):
    s = Session()
    award_gimmighouls(s, "alice", 100_000_000)   # $100 in base units
    s.commit()
    assert s.get(User, "alice").gimmighouls == 100


def test_award_custom_ratio_for_gacha(Session):
    """A lower ratio (gacha) credits proportionally fewer gimmighouls than the default battles rate."""
    s = Session()
    award_gimmighouls(s, "alice", 100_000_000, ratio=0.5)   # $100 at the gacha rate
    s.commit()
    assert s.get(User, "alice").gimmighouls == 50      # half of the 100 a battle would give


def test_award_with_code_boost_and_referrer_cut_to_owner(Session, unit_rate):
    s = Session()
    create_referral_code(s, "CREATOR", "Creator", boost_pct=0.10, referrer_pct=0.10,
                         owner_wallet="owner")
    apply_referral_code(s, "bob", "CREATOR")
    credited = award_gimmighouls(s, "bob", 100_000_000)   # $100
    s.commit()
    assert credited == 110
    assert s.get(User, "bob").gimmighouls == 110
    assert s.get(User, "owner").gimmighouls == 10


def test_award_with_code_fallback_to_earned_when_no_owner(Session, unit_rate):
    s = Session()
    create_referral_code(s, "NOOWNER", "NoOwner", boost_pct=0.0, referrer_pct=0.20)
    apply_referral_code(s, "carol", "NOOWNER")
    credited = award_gimmighouls(s, "carol", 50_000_000)   # $50
    s.commit()
    assert credited == 50
    assert get_referral_code(s, "NOOWNER").earned == 10  # round(50 * 0.20)


def test_award_does_not_round_the_boost(Session, unit_rate):
    s = Session()
    create_referral_code(s, "ODD", "Odd", boost_pct=0.105, referrer_pct=0.0)
    apply_referral_code(s, "dave", "ODD")
    credited = award_gimmighouls(s, "dave", 33_000_000)  # $33 · 33 * 1.105 = 36.465, as is
    s.commit()
    assert credited == pytest.approx(36.465)


def test_award_respects_config_ratio(Session, monkeypatch):
    import app.services.referrals as refmod
    from app.config import Settings
    monkeypatch.setattr(refmod, "get_settings", lambda: Settings(gimmighoul_per_usdc=2.0))
    s = Session()
    credited = award_gimmighouls(s, "erin", 10_000_000)   # $10 at 2x
    s.commit()
    assert credited == 20


def test_apply_referral_happy(Session):
    s = Session()
    create_referral_code(s, "OK", "Ok", boost_pct=0.15)
    out = apply_referral_code(s, "frank", "OK")
    s.commit()
    assert out == {"code": "OK", "boost_pct": 0.15}
    assert s.get(User, "frank").referred_by == "OK"


def test_apply_referral_already_referred(Session):
    s = Session()
    create_referral_code(s, "A", "A")
    create_referral_code(s, "B", "B")
    apply_referral_code(s, "grace", "A")
    with pytest.raises(ReferralError, match="already_referred"):
        apply_referral_code(s, "grace", "B")


def test_apply_referral_invalid_code(Session):
    s = Session()
    with pytest.raises(ReferralError, match="invalid_code"):
        apply_referral_code(s, "heidi", "NOPE")


def test_leaderboard_ordered_by_gimmighouls(Session):
    s = Session()
    s.add(User(wallet="low", elo=2000, games_played=0, gimmighouls=10))
    s.add(User(wallet="high", elo=1000, games_played=0, gimmighouls=500))
    s.add(User(wallet="mid", elo=1500, games_played=0, gimmighouls=100))
    s.commit()
    rows = leaderboard(s)
    assert [u.wallet for u in rows] == ["high", "mid", "low"]


APP_ID = "app123"


def _client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)
    priv = make_es256()
    privy = PrivyVerifier(app_id=APP_ID, key_resolver=lambda kid: priv.public_key())
    app = create_app(sf, MockChainSource(), elo_start=1200, elo_k=32, privy=privy)
    return TestClient(app), sf, priv


WALLET = "So1ana1111111111111111111111111111111111111"


def test_apply_referral_endpoint_happy_and_already(Session):
    c, sf, priv = _client()
    with sf() as s:
        create_referral_code(s, "PROMO", "Promo", boost_pct=0.10)
        s.commit()
    hdrs = privy_auth_headers(priv, APP_ID, WALLET)
    r = c.post(f"/users/{WALLET}/referral", json={"code": "PROMO"}, headers=hdrs)
    assert r.status_code == 200 and r.json() == {"code": "PROMO", "boost_pct": 0.10}
    # second apply → 409 already_referred
    r2 = c.post(f"/users/{WALLET}/referral", json={"code": "PROMO"}, headers=hdrs)
    assert r2.status_code == 409 and "already_referred" in r2.json()["detail"]


def test_apply_referral_endpoint_invalid_code(Session):
    c, sf, priv = _client()
    hdrs = privy_auth_headers(priv, APP_ID, WALLET)
    r = c.post(f"/users/{WALLET}/referral", json={"code": "NOPE"}, headers=hdrs)
    assert r.status_code == 409 and "invalid_code" in r.json()["detail"]


def test_leaderboard_endpoint_shape_and_order(Session):
    c, sf, _ = _client()
    with sf() as s:
        s.add(User(wallet="x", elo=1000, games_played=0, gimmighouls=5))
        s.add(User(wallet="y", elo=1000, games_played=0, gimmighouls=50))
        s.commit()
    rows = c.get("/leaderboard").json()
    assert [r["wallet"] for r in rows] == ["y", "x"]
    assert set(rows[0].keys()) == {"wallet", "alias", "gimmighouls", "elo"}


def test_settle_award_idempotent_guard(Session, unit_rate):
    """The guard: award only when not yet awarded, then flip the flag → second pass no-ops."""
    s = Session()
    b = PackBattle(id="b1", mode="pack", machine_code="m", price=25, max_players=2,
                   status="settled")
    s.add(b)
    s.commit()

    def settle_award_once(battle, wallets, buyin):
        if battle.gimmighouls_awarded:
            return
        for w in wallets:
            award_gimmighouls(s, w, buyin)
        battle.gimmighouls_awarded = True

    settle_award_once(b, ["p1", "p2"], 25_000_000)   # $25 in base units
    settle_award_once(b, ["p1", "p2"], 25_000_000)  # re-run must not double-credit
    s.commit()
    assert s.get(User, "p1").gimmighouls == 25
    assert s.get(User, "p2").gimmighouls == 25
    assert b.gimmighouls_awarded is True


def test_ratios_de_gimmighouls_no_se_mueven_sin_tocar_la_copia():
    """Both figures are written BY HAND in the interface.

    src/ui/screens/Help/helpContent.ts and src/ui/components/OnboardingTutorial.tsx promise the
    player "0.5 per dollar in battles" and "0.01 in gacha". No endpoint publishes them, so if these
    values change and the copy does not, the Help text lies about what you earn. This test is the
    warning: if you break it, update those two files too.
    """
    from app.config import Settings
    s = Settings()
    assert s.gimmighoul_per_usdc == 0.5
    assert s.gimmighoul_per_usdc_gacha == 0.01
    # Y el gacha renta menos que una batalla: se premia jugar contra alguien.
    assert s.gimmighoul_per_usdc_gacha < s.gimmighoul_per_usdc


# ── Fractions do not get lost ─────────────────────────────────────────────
#
# The counter IS DECIMAL. It used to be an integer, and with the gacha at 0.01 per dollar it
# stopped working: a 50 $ pack gives half a point, `round(0.5)` is 0, and the two MOST played
# machines (25 and 50 $) would have stopped paying anything at all while the help text kept
# promising 0.01 per dollar.
#
# With decimals there is nothing to round, so nothing gets overpaid either: you get paid exactly
# what is due.

USDC_ = 1_000_000


def test_half_a_unit_is_paid_in_full_not_rounded(Session):
    with Session() as s:
        assert award_gimmighouls(s, "W", 50 * USDC_, ratio=0.01) == 0.5
        assert s.get(User, "W").gimmighouls == 0.5


def test_two_50_dollar_packs_add_up_to_the_point(Session):
    with Session() as s:
        award_gimmighouls(s, "W", 50 * USDC_, ratio=0.01)
        award_gimmighouls(s, "W", 50 * USDC_, ratio=0.01)
        assert s.get(User, "W").gimmighouls == 1


def test_ten_50_dollar_packs_pay_the_FIVE_points_owed(Session):
    """What backs the promise: 0.01 per dollar on 500 $ is 5 points. Neither 0 nor 10."""
    with Session() as s:
        for _ in range(10):
            award_gimmighouls(s, "W", 50 * USDC_, ratio=0.01)
        assert s.get(User, "W").gimmighouls == 5


def test_four_25_dollar_packs_pay_one_point(Session):
    with Session() as s:
        for _ in range(4):
            award_gimmighouls(s, "W", 25 * USDC_, ratio=0.01)
        assert s.get(User, "W").gimmighouls == 1


def test_never_overpays(Session):
    """A 150 $ pack at 0.01 is 1.5 points. Rounding to the nearest one would give away half a
    point for free."""
    with Session() as s:
        assert award_gimmighouls(s, "W", 150 * USDC_, ratio=0.01) == 1.5
        assert s.get(User, "W").gimmighouls == 1.5


def test_battles_still_pay_the_same(Session):
    """Where the payout was already an integer nothing changes: 10 $ at 0.5 is 5 points."""
    with Session() as s:
        assert award_gimmighouls(s, "W", 10 * USDC_) == 5
        assert s.get(User, "W").gimmighouls == 5


def test_the_referrer_cut_also_carries_decimals(Session):
    """Its cut is a percentage of an already small payout, so it is where the most fractions
    come out."""
    with Session() as s:
        create_referral_code(s, code="REF", name="R", boost_pct=0.0, referrer_pct=0.1,
                             owner_wallet="OWNER")
        apply_referral_code(s, "W", "REF")
        award_gimmighouls(s, "W", 50 * USDC_, ratio=0.01)   # 0.1 of half a point = 0.05
        assert s.get(User, "OWNER").gimmighouls == 0.05


def test_the_decimal_survives_the_round_trip_to_disk(Session):
    """The ones above look at the in-memory object; this one forces a round trip through the
    database.

    What this does NOT prove, in case someone reads it backwards: that the column has to be
    declared FLOAT. SQLite types by affinity and stores 0.5 as is even if the column says
    INTEGER (checked by mutating the model: this test passes just the same). The model's FLOAT
    is what states the intent, and what would be needed if this were ever not SQLite. What this
    does hold is that nothing on the round trip turns the half point into an integer.
    """
    with Session() as s:
        award_gimmighouls(s, "W", 50 * USDC_, ratio=0.01)
        s.commit()
    with Session() as s:
        assert s.get(User, "W").gimmighouls == 0.5
