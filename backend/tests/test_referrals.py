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


def test_award_no_redondea_el_boost(Session, unit_rate):
    s = Session()
    create_referral_code(s, "ODD", "Odd", boost_pct=0.105, referrer_pct=0.0)
    apply_referral_code(s, "dave", "ODD")
    credited = award_gimmighouls(s, "dave", 33_000_000)  # $33 · 33 * 1.105 = 36.465, tal cual
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
    """Las dos cifras están escritas A MANO en la interfaz.

    src/ui/screens/Help/helpContent.ts y src/ui/components/OnboardingTutorial.tsx le prometen al
    jugador "0.5 per dollar in battles" y "0.01 in gacha". No hay endpoint que las publique, así
    que si estos valores cambian y la copia no, la ayuda miente sobre lo que se gana. Este test es
    el aviso: si lo rompes, actualiza también esos dos ficheros.
    """
    from app.config import Settings
    s = Settings()
    assert s.gimmighoul_per_usdc == 0.5
    assert s.gimmighoul_per_usdc_gacha == 0.01
    # Y el gacha renta menos que una batalla: se premia jugar contra alguien.
    assert s.gimmighoul_per_usdc_gacha < s.gimmighoul_per_usdc


# ── Las fracciones no se pierden ─────────────────────────────────────────────
#
# El contador ES DECIMAL. Era entero, y con el gacha a 0.01 por dólar dejó de valer: un sobre de
# 50 $ da medio punto, `round(0.5)` es 0, y las dos máquinas MÁS jugadas —25 y 50 $— habrían
# dejado de pagar absolutamente nada mientras la ayuda seguía prometiendo 0.01 por dólar.
#
# Con decimales no hay nada que redondear, así que tampoco se paga de más: se paga lo que toca.

USDC_ = 1_000_000


def test_media_unidad_se_paga_entera_no_se_redondea(Session):
    with Session() as s:
        assert award_gimmighouls(s, "W", 50 * USDC_, ratio=0.01) == 0.5
        assert s.get(User, "W").gimmighouls == 0.5


def test_dos_sobres_de_50_suman_el_punto(Session):
    with Session() as s:
        award_gimmighouls(s, "W", 50 * USDC_, ratio=0.01)
        award_gimmighouls(s, "W", 50 * USDC_, ratio=0.01)
        assert s.get(User, "W").gimmighouls == 1


def test_diez_sobres_de_50_pagan_los_CINCO_puntos_que_tocan(Session):
    """Lo que sostiene la promesa: 0.01 por dólar sobre 500 $ son 5 puntos. Ni 0 ni 10."""
    with Session() as s:
        for _ in range(10):
            award_gimmighouls(s, "W", 50 * USDC_, ratio=0.01)
        assert s.get(User, "W").gimmighouls == 5


def test_cuatro_sobres_de_25_pagan_un_punto(Session):
    with Session() as s:
        for _ in range(4):
            award_gimmighouls(s, "W", 25 * USDC_, ratio=0.01)
        assert s.get(User, "W").gimmighouls == 1


def test_nunca_se_paga_de_mas(Session):
    """Un sobre de 150 $ a 0.01 son 1.5 puntos. Redondeando al más cercano se regalaría medio."""
    with Session() as s:
        assert award_gimmighouls(s, "W", 150 * USDC_, ratio=0.01) == 1.5
        assert s.get(User, "W").gimmighouls == 1.5


def test_las_batallas_siguen_pagando_igual(Session):
    """Donde el premio ya era entero no cambia nada: 10 $ a 0.5 son 5 puntos."""
    with Session() as s:
        assert award_gimmighouls(s, "W", 10 * USDC_) == 5
        assert s.get(User, "W").gimmighouls == 5


def test_el_corte_del_referidor_tambien_lleva_decimales(Session):
    """Su corte es un porcentaje de un premio ya pequeño, así que es donde más fracciones salen."""
    with Session() as s:
        create_referral_code(s, code="REF", name="R", boost_pct=0.0, referrer_pct=0.1,
                             owner_wallet="OWNER")
        apply_referral_code(s, "W", "REF")
        award_gimmighouls(s, "W", 50 * USDC_, ratio=0.01)   # 0.1 de medio punto = 0.05
        assert s.get(User, "OWNER").gimmighouls == 0.05


def test_el_decimal_sobrevive_al_disco(Session):
    """Los de arriba miran el objeto en memoria; este obliga a pasar por la base de datos.

    Lo que NO prueba, por si alguien lo lee al revés: que la columna tenga que declararse FLOAT.
    SQLite tipa por afinidad y guarda 0.5 tal cual aunque la columna diga INTEGER (comprobado
    mutando el modelo: este test pasa igual). El FLOAT del modelo es lo que dice la intención, y
    lo que haría falta si algún día esto no fuera SQLite. Lo que sí sujeta es que nada en el
    camino de ida y vuelta convierta el medio punto a entero.
    """
    with Session() as s:
        award_gimmighouls(s, "W", 50 * USDC_, ratio=0.01)
        s.commit()
    with Session() as s:
        assert s.get(User, "W").gimmighouls == 0.5
