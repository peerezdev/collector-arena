"""The pass prices live in configuration, not in the code.

Zero turns off the entire payment path, just like an empty `gacha_base_url` turns off the gacha.
It's what lets this ship without having decided the price yet: the purchase is simply not
offered.
"""
from app.config import Settings, avisar_precios_raros


def test_the_paid_route_is_OFF_by_default(monkeypatch):
    # `_env_file=None` and clearing the variables, because this asserts what the CODE ships with,
    # not what this machine happens to have configured. Reading the real `.env` made the test pass
    # or fail depending on whether whoever ran it had already priced the pass, which is exactly
    # the kind of test that goes red for the wrong reason the day someone sets a price.
    monkeypatch.delenv("TRACKER_PASS_7D_USDC", raising=False)
    monkeypatch.delenv("TRACKER_PASS_30D_USDC", raising=False)
    s = Settings(_env_file=None)
    assert s.tracker_pass_7d_usdc == 0.0
    assert s.tracker_pass_30d_usdc == 0.0


def test_the_warning_triggers_when_the_LONG_pass_is_worse_per_day():
    # 7 days at 10 is 1.43/day; 30 days at 50 is 1.67/day. Buying the long one would be throwing
    # money away, and only a customer who does the math would find out.
    assert avisar_precios_raros(10.0, 50.0) is not None


def test_no_warning_when_the_long_one_is_better():
    assert avisar_precios_raros(10.0, 30.0) is None


def test_no_warning_when_either_one_is_off():
    # With the path turned off there's nothing to compare, and warning would just be noise on
    # every startup.
    assert avisar_precios_raros(0.0, 0.0) is None
    assert avisar_precios_raros(10.0, 0.0) is None
    assert avisar_precios_raros(0.0, 30.0) is None


def test_the_same_price_per_day_does_not_warn():
    # 7 at 7 and 30 at 30 are both 1.0/day. Not a bargain, but not an error either.
    assert avisar_precios_raros(7.0, 30.0) is None
