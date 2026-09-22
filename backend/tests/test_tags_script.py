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
