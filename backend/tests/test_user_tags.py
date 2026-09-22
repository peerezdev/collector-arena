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
