"""The pass purchase. This is where a user's real money moves for the first time outside of a
match, so what gets tested the most is the FAILURE path.

The fixtures (`pase_entorno` and friends) live in `tests/conftest.py`, because task 7 reuses
them.

TWO THINGS THIS FILE PROTECTS THAT DON'T SHOW UP READING JUST ONE TEST:

  · Nothing that happens BEFORE sending leaves a row. Building and signing can blow up from
    configuration (a badly written address or mint, an unparseable blockhash), and that doesn't
    depend on who's buying: if it left a row, it would lock out EVERY wallet on its first
    attempt, which is what happened three rounds in a row. Checked with
    `mando["filas_al_firmar"]`, and also with REAL construction (no mocking) in the last section.
  · Every `pending` has a signature. Checked with `mando["firma_en_la_fila_al_enviar"]`: the
    instant the money can move, the row already exists and already knows which transaction to
    watch.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import TrackerPass

from tests.conftest import TRACKER_PASS_WALLET


def test_buying_grants_access_and_says_so(pase_client, pase_cobro_ok):
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 7
    assert r.json()["price_usdc"] == 10.0
    acc = pase_client.get("/gacha/tracker-access", headers=pase_client.hdrs).json()
    assert acc["allowed"] is True and acc["via"] == "pass"


def test_the_pass_ends_up_ACTIVE_and_with_its_signature(pase_client, pase_cobro_ok):
    pase_client.post("/gacha/tracker-pass", json={"days": 30}, headers=pase_client.hdrs)
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "active"
        assert p.tx_signature
        assert p.price_base_units == 30_000_000


def test_pass_until_is_when_the_pass_ENDS_not_when_it_starts(pase_client, pase_cobro_ok):
    # `pass_until` is what the screen paints. If the endpoint returned `desde` instead of
    # `hasta` (easy to mix up: they're both variables from the same `periodo()`), the player
    # would see that their 7-day pass has already expired today.
    ahora = time.time()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    # First purchase, no previous pass: `desde` is "ahora" (now), so `pass_until` has to be
    # ~7 days from this instant. A margin of a few seconds covers however long the request takes.
    assert abs(r.json()["pass_until"] - (ahora + 7 * 86400)) < 5


def test_without_balance_NO_row_gets_created(pase_client, pase_sin_saldo):
    # The 402 happens before anything gets written: no row, no charge, no trace.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 402
    with pase_client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []


def test_balance_RESERVED_for_a_battle_cannot_be_spent_on_a_pass(pase_client,
                                                                           pase_saldo_reservado):
    # If a pass could spend it, the battle would be left without funds when settling.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 402


# ── The order: sign first, write the row after ───────────────────────────────────────────────


def test_while_building_and_signing_there_is_STILL_no_row(pase_client, pase_cobro_ok):
    # The invariant that makes impossible, by construction, the family of failures that locked
    # out wallets three rounds in a row. While building and signing there's no row, so there's
    # no lock to release and it doesn't matter what type of exception gets raised there.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    assert pase_client.mando["filas_al_firmar"] == 0


def test_by_the_time_it_sends_the_row_ALREADY_exists_and_ALREADY_has_its_signature(pase_client, pase_cobro_ok):
    # The other half of the same order: as soon as the money can move, the row exists and knows
    # which transaction to watch. It's what makes it so a `pending` without a signature can NOT
    # exist, which used to be the only cell in this table that needed a person to look at it.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    assert pase_client.mando["firma_en_la_fila_al_enviar"], "the row was born with the signature already set"


def test_nobody_has_access_at_the_instant_of_the_charge(pase_client, pase_cobro_ok):
    # The final state "active" isn't enough to prove the order: the failure branches leave it
    # back in "failed"/"pending" whatever happens before. Looking at ACCESS (via `pase_vigente`,
    # not the `status` column) is the only thing that catches "it got activated before charging"
    # (and along the way, any new state that granted access without being called "active").
    pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert pase_client.mando["acceso_al_cobrar"] is False


# ── Failures BEFORE sending: not a single row, and retryable right away ───────────────────────


def test_a_failure_requesting_the_blockhash_leaves_NO_row_AT_ALL(pase_client, pase_blockhash_revienta):
    # Asking for the blockhash is the first thing that touches the network and it happens before
    # any row exists. Before this round this depended on an `except` classifying it correctly;
    # now it's structural.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []

    # And the retry works: once the RPC is fixed, the wallet does NOT stay locked out.
    pase_client.mando["blockhash"] = "11111111111111111111111111111111"
    r2 = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r2.status_code == 200, r2.text


def test_a_signature_REJECTED_by_privy_leaves_NO_row_AT_ALL(pase_client, pase_firma_rechazada):
    # PrivySignerError while signing: nothing has been broadcast and there's no row to clean up.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []


def test_a_ValueError_while_BUILDING_leaves_NO_row_AT_ALL(pase_client, pase_construccion_revienta):
    # The `ValueError` from solders is the specific exception that locked out wallets in round
    # 3, and the one that can NOT be lightly dropped into a "determined" bucket, because
    # `json.JSONDecodeError` (which can indeed come from after a send) inherits from it. There's
    # no longer a need to tell them apart: this one happens before any row exists, and the other
    # happens after the row already has a signature.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []
    assert pase_client.mando["acceso_al_cobrar"] is None, "nothing ever got sent"


# ── Failures FROM sending onward: the row exists, and always with a signature ──────────────────


def test_a_REJECTED_send_gives_no_access_and_leaves_the_row_as_failed(pase_client,
                                                                   pase_envio_rechazado):
    # RuntimeError: a DEFINITIVE rejection from the RPC (see `submit_signed_tx`). The money
    # didn't move, so the row can be closed as `failed` (and that also releases the lock right
    # away).
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "failed"
        assert p.tx_signature, "even rejected, it is known which transaction it was"
    acc = pase_client.get("/gacha/tracker-access", headers=pase_client.hdrs).json()
    assert acc["allowed"] is False
    # Nobody had access even for the instant the charge was attempted.
    assert pase_client.mando["acceso_al_cobrar"] is False


def test_an_ALREADY_PROCESSED_send_is_not_read_as_a_rejection(pase_client,
                                                              pase_envio_ya_procesado):
    """The one rejection that means the money DID move.

    "This transaction has already been processed" reaches us as the same `RuntimeError` as a real
    rejection, and closing the row as `failed` over it would be a double lie: it would say the
    charge did not happen while the transaction sits on the chain, and it would release the lock,
    so the retry that follows a 502 would charge a second pass.

    It stays `pending` WITH its signature, which is the state that reconciles itself: the next
    purchase by this wallet asks the chain and closes it.
    """
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "pending", "it is on the chain: calling it failed would be a lie"
        assert p.tx_signature, "and it is reconcilable because the signature is right there"


def test_an_INDETERMINATE_send_leaves_the_row_pending_WITH_a_signature(pase_client,
                                                                pase_envio_indeterminado):
    # A timeout, a proxy 5xx after resubmitting... we don't know whether the transaction went
    # through. `failed` here would be lying "definitely not". It stays `pending`, but (and this
    # is this round's difference) WITH its signature: it's no longer a row that needs a person
    # to look at it, it's one that this wallet's next purchase reconciles on its own.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "pending"
        assert p.tx_signature, "it is known which transaction to look up in an explorer"
    acc = pase_client.get("/gacha/tracker-access", headers=pase_client.hdrs).json()
    assert acc["allowed"] is False
    assert pase_client.mando["acceso_al_cobrar"] is False


def test_a_charge_SENT_but_REJECTED_on_chain_also_gives_no_access(pase_client,
                                                                     pase_cobro_sin_confirmar):
    # confirmar_firma returns False: the chain executed it and REJECTED it (`err` present). A
    # definitive rejection, not an indeterminate one, which is why the row CAN be closed as
    # `failed`.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "failed"
        assert p.tx_signature, "the signature is saved anyway, so it can be reconciled by hand"
    assert pase_client.mando["acceso_al_cobrar"] is False


def test_an_INDETERMINATE_confirmation_leaves_it_pending_with_a_signature(pase_client,
                                                                pase_confirmacion_indeterminada):
    # confirmar_firma returns None: the attempts ran out without a verdict. The money MAY have
    # moved, so neither `failed` (it would be lying) nor `active` (it would give away access):
    # it stays `pending` WITH the signature, which is what the next purchase reconciles on its
    # own.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "pending"
        assert p.tx_signature, "it is known which transaction to look up in an explorer"
    acc = pase_client.get("/gacha/tracker-access", headers=pase_client.hdrs).json()
    assert acc["allowed"] is False
    assert pase_client.mando["acceso_al_cobrar"] is False


def test_buying_twice_STACKS(pase_client, pase_cobro_ok):
    pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    with pase_client.session_factory() as s:
        pases = sorted(s.scalars(select(TrackerPass)).all(), key=lambda p: p.starts_at)
        assert pases[1].starts_at == pases[0].ends_at


def test_without_a_token_you_cannot_buy(pase_client):
    assert pase_client.post("/gacha/tracker-pass", json={"days": 7}).status_code == 401


def test_a_duration_we_do_not_sell_is_rejected(pase_client, pase_cobro_ok):
    r = pase_client.post("/gacha/tracker-pass", json={"days": 1}, headers=pase_client.hdrs)
    assert r.status_code == 422


def test_with_the_price_OFF_the_purchase_does_not_exist(pase_precio_apagado):
    r = pase_precio_apagado.post("/gacha/tracker-pass", json={"days": 7},
                                 headers=pase_precio_apagado.hdrs)
    assert r.status_code == 503
    # The code alone isn't enough: a 503 from "privy not configured" (the bug this test had
    # before an earlier review, with an app built without `privy=`) is also 503, and that
    # doesn't prove anything about the price. `detail` is the only thing that tells the two apart.
    assert r.json()["detail"] == "tracker_pass_disabled"


# ── The request throttle ─────────────────────────────────────────────────────────────────────


def test_past_the_purchase_limit_per_window_it_cuts_off_with_a_429(pase_client,
                                                                      pase_blockhash_revienta):
    # It moves money just like `/withdraw` and `/tip`, so it carries the same throttle. A route
    # that FAILS before writing anything (and therefore leaves no lock or pass in the way) is
    # used so that the only thing cutting off attempts is the throttle, not a 409 or an
    # already-bought pass.
    for i in range(5):                       # tracker_pass_rate_limit default
        r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
        assert r.status_code == 502, f"attempt {i}: {r.text}"
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 429
    with pase_client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []


# ── I1: two purchases from the same wallet at the same time ────────────────────────────────────


def _pending(**over):
    """A minimal `pending` row to seed directly into the database, without going through the
    endpoint. Simulates "there's already a purchase in progress" without depending on real
    concurrency. Watch out: without an explicit `tx_signature` it's born WITHOUT a signature,
    which is a state the endpoint no longer ever produces (it can only come from an old row
    predating this round, or from a manual `INSERT`)."""
    ahora = datetime.now(timezone.utc)
    base = dict(id="ya-en-curso", wallet=TRACKER_PASS_WALLET, days=7, price_base_units=10_000_000,
               status="pending", starts_at=ahora, ends_at=ahora + timedelta(days=7))
    base.update(over)
    return TrackerPass(**base)


def test_a_pending_already_there_returns_409_and_does_not_charge_again(pase_client, pase_cobro_ok):
    # The normal case for this is a double click: the first request is still halfway through
    # charging when the second one arrives. Seeding the row by hand represents that instant
    # without needing two real threads.
    with pase_client.session_factory() as s:
        s.add(_pending())
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    assert r.json()["detail"] == "tracker_pass_pending"
    with pase_client.session_factory() as s:
        # Not a single new row, not a charge: the 409 came out before touching the chain.
        assert len(s.scalars(select(TrackerPass)).all()) == 1
    assert pase_client.mando["filas_al_firmar"] is None, "the transaction was not even built"


def test_the_unique_index_prevents_two_pending_at_once(pase_client):
    # This is the ATOMIC part of the guarantee: the app-level check above is a check-then-act
    # (SELECT and then INSERT) and by itself doesn't close the race between two requests that
    # pass the check almost at the same time. The partial unique index in app/db.py
    # (uq_tracker_passes_pending_wallet) is what actually prevents it, at the database level.
    with pase_client.session_factory() as s:
        s.add(_pending(id="uno"))
        s.commit()
        s.add(_pending(id="dos"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_the_real_race_also_returns_409(pase_client, pase_cobro_ok, monkeypatch):
    # The test above (`test_ya_hay_una_pending...`) tests the shortcut: a `pending` that was
    # ALREADY there before the request arrived. This one tests the real race: the request passes
    # the SELECT without seeing anything (there's no `pending` yet), and right AFTER (exploiting
    # the gap in `_require_available`, which is the next thing the endpoint does), another
    # purchase from the same wallet sneaks in. It's the only way to exercise the endpoint's
    # `except IntegrityError` branch without two real threads: no other test touches it. What
    # gets thrown away when losing the race is a transaction that's already signed and never
    # sent: it doesn't move any money.
    import app.main as m

    async def _saldo_que_cuela_una_pending(*a, **k):
        with pase_client.session_factory() as s2:
            s2.add(_pending(id="slipped-in"))
            s2.commit()
        return pase_client.mando["saldo"]

    monkeypatch.setattr(m, "usdc_balance_base_units", _saldo_que_cuela_una_pending)

    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    assert r.json()["detail"] == "tracker_pass_pending"
    with pase_client.session_factory() as s:
        assert len(s.scalars(select(TrackerPass)).all()) == 1
    assert pase_client.mando["acceso_al_cobrar"] is None, "nothing was ever sent"


# ── B: a `pending` WITH a signature reconciles itself on the next purchase ─────────────────────


def test_a_pending_WITH_a_signature_self_reconciles_to_ACTIVE_and_does_NOT_charge_again(pase_client,
                                                                                pase_cobro_ok):
    # A player who sees a 502 and retries (the normal reaction to an error) must NOT end up
    # paying for a second pass on top of the first one, which did get charged. `mando["confirma"]`
    # defaults to True: when asked again, the chain says it did confirm, so the old one closes
    # itself as `active` and THIS request returns THAT pass, without charging anything more.
    with pase_client.session_factory() as s:
        s.add(_pending(tx_signature="FirmaVieja"))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 7
    assert r.json()["price_usdc"] == 10.0
    with pase_client.session_factory() as s:
        filas = {p.id: p.status for p in s.scalars(select(TrackerPass)).all()}
    assert filas == {"ya-en-curso": "active"}, "a single row: the old one, reconciled"
    assert pase_client.mando["filas_al_firmar"] is None, "no new charge at all"


def test_a_pending_WITH_a_REJECTED_signature_closes_and_does_not_block(pase_client, pase_cobro_sin_confirmar):
    # `mando["confirma"]` is False: the chain says it executed and failed. The old one closes
    # itself as `failed` (no more signature to reconcile, no access to give away), and this
    # request's purchase does NOT get blocked with a 409 because of it (although, with this same
    # `confirma`, it ends up failing too, on its own).
    with pase_client.session_factory() as s:
        s.add(_pending(tx_signature="FirmaVieja"))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code != 409, "the old one reconciles itself: it must not block the new purchase"
    with pase_client.session_factory() as s:
        vieja = s.get(TrackerPass, "ya-en-curso")
        assert vieja.status == "failed"


def test_a_pending_WITH_a_signature_that_STAYS_indeterminate_does_not_resolve(pase_client,
                                                                      pase_confirmacion_indeterminada):
    # `mando["confirma"]` is still None: asking again doesn't change anything, because the real
    # chain wouldn't know either, yet. It stays `pending`, and the 409 remains in place: this
    # can't reconcile itself, no matter how many times it's tried.
    with pase_client.session_factory() as s:
        s.add(_pending(tx_signature="FirmaVieja"))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    with pase_client.session_factory() as s:
        vieja = s.get(TrackerPass, "ya-en-curso")
        assert vieja.status == "pending"


def test_a_pending_WITHOUT_a_signature_does_not_try_to_reconcile(pase_client, pase_cobro_ok):
    # Without a signature there's nothing to ask the chain: `confirmar_firma` doesn't even get
    # called. The endpoint no longer creates rows like this (every `pending` is born with its
    # signature), but some can be left over from before this round or from a manual fix, and the
    # path has to keep behaving sensibly.
    with pase_client.session_factory() as s:
        s.add(_pending())     # tx_signature=None by default
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    with pase_client.session_factory() as s:
        vieja = s.get(TrackerPass, "ya-en-curso")
        assert vieja.status == "pending"
    assert "confirma_llamadas" not in pase_client.mando


# ── D: the 409 tells apart "wait a moment" from "this is stuck" ────────────────────────────────


def test_a_FRESHLY_created_pending_gives_the_normal_409(pase_client, pase_cobro_ok):
    with pase_client.session_factory() as s:
        s.add(_pending())
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    assert r.json()["detail"] == "tracker_pass_pending"


def test_an_OLD_pending_gives_a_different_409(pase_client, pase_cobro_ok):
    # Not even the slowest charge reaches this: past the threshold, "wait a moment" is no longer
    # true, and the frontend needs to be able to say something different ("this is stuck, contact
    # support"). 15 minutes leaves plenty of margin over the threshold (300 s) without getting
    # close to the real limit, so this test doesn't depend on how fast the suite happens to run.
    with pase_client.session_factory() as s:
        vieja = datetime.now(timezone.utc) - timedelta(minutes=15)
        s.add(_pending(created_at=vieja))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    assert r.json()["detail"] == "tracker_pass_pending_stuck"


def test_a_two_minute_old_pending_is_NOT_stuck(pase_client, pase_cobro_ok):
    # With a short threshold (60 s, which only counted the waits between `confirmar_firma`
    # retries and forgot about its own timeouts and the send itself) this would have been
    # labeled "stuck" despite comfortably being within what a legitimate charge can take. Sending
    # a player to support over a purchase that's merely slow is worse than saying nothing at all.
    with pase_client.session_factory() as s:
        vieja = datetime.now(timezone.utc) - timedelta(minutes=2)
        s.add(_pending(created_at=vieja))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    assert r.json()["detail"] == "tracker_pass_pending"


# ── H: an unconfigured charge destination wallet is OUR problem ────────────────────────────────


def test_an_empty_fee_destination_gives_a_503_configuration_error_and_leaves_no_trace(pase_fee_dest_vacio):
    # This is no longer what prevents locking out wallets (the order takes care of that), but
    # the status code still matters: a 503 "misconfigured" sends whoever is deploying to check
    # their .env, while a 502 "charge failed" would send them to search the chain for a charge
    # that was never even attempted.
    r = pase_fee_dest_vacio.post("/gacha/tracker-pass", json={"days": 7},
                                 headers=pase_fee_dest_vacio.hdrs)
    assert r.status_code == 503
    assert r.json()["detail"] == "tracker_pass_misconfigured"
    with pase_fee_dest_vacio.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == [], "nothing gets written: it cuts off before that"


# ── I: a reconciled pending's window ALWAYS gets shifted ───────────────────────────────────────


def test_reconciling_an_ALREADY_EXPIRED_window_shifts_it_to_start_from_now(pase_client, pase_cobro_ok):
    # A `pending` from 8 days ago with a 7-day window: by the time it's reconciled, `ends_at` is
    # already in the past. Activating it as-is would give zero access for a pass that did get
    # charged.
    with pase_client.session_factory() as s:
        hace_8_dias = datetime.now(timezone.utc) - timedelta(days=8)
        s.add(_pending(tx_signature="FirmaVieja", starts_at=hace_8_dias,
                       ends_at=hace_8_dias + timedelta(days=7)))
        s.commit()
    ahora = time.time()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    assert abs(r.json()["pass_until"] - (ahora + 7 * 86400)) < 5
    with pase_client.session_factory() as s:
        p = s.get(TrackerPass, "ya-en-curso")
        assert p.status == "active"
        fin = p.ends_at if p.ends_at.tzinfo else p.ends_at.replace(tzinfo=timezone.utc)
        inicio = p.starts_at if p.starts_at.tzinfo else p.starts_at.replace(tzinfo=timezone.utc)
        assert fin > datetime.now(timezone.utc), "it is no longer expired"
        assert fin - inicio == timedelta(days=7), "the purchased number of days was honored"


def test_reconciling_a_window_HALFWAY_THROUGH_ALSO_shifts_it(pase_client, pase_cobro_ok):
    # A `pending` from 3 days ago with a 7-day window. Before, only the one that had expired
    # ENTIRELY got shifted, with the argument that "the player already has access since the row
    # was created". That's false: a `pending` row does NOT grant access (only `active` does), so
    # those 3 days are days that were paid for and not used. Always shifting it is simpler and
    # fairer, and in the normal case (a `pending` that's seconds old) it's a millisecond no-op.
    with pase_client.session_factory() as s:
        hace_3_dias = datetime.now(timezone.utc) - timedelta(days=3)
        s.add(_pending(tx_signature="FirmaVieja", starts_at=hace_3_dias,
                       ends_at=hace_3_dias + timedelta(days=7)))
        s.commit()
    ahora = time.time()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    assert abs(r.json()["pass_until"] - (ahora + 7 * 86400)) < 5, "the 7 days, starting from now"


def test_reconciling_does_not_overlap_a_pass_that_is_ALREADY_active(pase_client, pase_cobro_ok):
    # The limit of "always shifting it": if this wallet already has an ACTIVE pass, starting
    # "now" would overlap the two windows and eat exactly the days the player wanted to stack.
    # That's why the window gets recalculated with `periodo`, which starts where the active pass
    # ends.
    with pase_client.session_factory() as s:
        ahora = datetime.now(timezone.utc)
        fin_del_activo = ahora + timedelta(days=4)
        s.add(TrackerPass(id="el-activo", wallet=TRACKER_PASS_WALLET, days=7,
                          price_base_units=10_000_000, status="active",
                          starts_at=ahora - timedelta(days=3), ends_at=fin_del_activo))
        s.add(_pending(tx_signature="FirmaVieja"))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    esperado = (fin_del_activo + timedelta(days=7)).timestamp()
    assert abs(r.json()["pass_until"] - esperado) < 5, "it starts where the one that was already there ends"


# ── K: self-reconciliation can't hang for minutes ───────────────────────────────────────────────


def test_the_reconciliation_uses_a_short_budget(pase_client, pase_cobro_ok):
    # The reconciliation call (over the OLD signature) carries short `intentos`/`espera_s`.
    # The confirmation of the charge that was just made (over the NEW signature) ALSO carries
    # short ones today (see `_CONFIRMACION_*` in main.py): `confirmar_firma`'s default values
    # (around 213 s) don't fit in a normal HTTP request, and no proxy in front can hold that.
    with pase_client.session_factory() as s:
        s.add(_pending(tx_signature="FirmaVieja"))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    llamadas = pase_client.mando["confirma_llamadas"]
    assert len(llamadas) == 1, "only the reconciliation: it resolved to active and did not charge again"
    reconciliacion = llamadas[0]
    assert reconciliacion.get("intentos", 10) < 10
    assert reconciliacion.get("espera_s", 1.5) <= 1.0


def test_the_confirmation_of_the_new_charge_ALSO_uses_a_short_budget(pase_client,
                                                                          pase_cobro_ok):
    # With no previous `pending` to reconcile, the ONLY call to `confirmar_firma` is the one
    # that confirms the signature that was JUST sent. It used to use the default values (10
    # attempts of 20 s, about 213 s total) and no proxy in front could hold that wait: a purchase
    # that DID work was shown as an error to whoever had just paid. It must carry the short
    # `_CONFIRMACION_*` budget, just like the reconciliation.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    llamadas = pase_client.mando["confirma_llamadas"]
    assert len(llamadas) == 1
    cobro = llamadas[0]
    assert cobro.get("intentos", 10) < 10
    assert cobro.get("espera_s", 1.5) <= 1.0
    assert cobro.get("timeout_s", 20.0) < 20.0


# ── REAL construction, no mocking ────────────────────────────────────────────────────────────
#
# Everything above mocks `construir_y_firmar_cobro`, so none of the tests above can see a real
# `ValueError` from solders (and that's exactly why the same hole kept sneaking in three rounds
# in a row: the mock always "built" fine). There's no mock here: it builds and signs for real
# with `build_token_transfer` + `sign_solana` + `leer_firma`, and the only thing intervened is
# the send.


def test_a_MISSPELLED_MINT_building_FOR_REAL_leaves_no_row(pase_mint_malo):
    # `Pubkey.from_string("no-es-un-mint")` raises ValueError INSIDE `build_token_transfer`.
    # It's configuration: it doesn't depend on who's buying, so if it left a row it would lock
    # out EVERY wallet on its first attempt.
    r = pase_mint_malo.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_mint_malo.hdrs)
    assert r.status_code == 502
    with pase_mint_malo.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []
    # And the second attempt fails again THE SAME WAY, not with a 409. The error repeating is
    # correct (the configuration is still wrong); what's being tested is that it does NOT turn
    # into a lock.
    r2 = pase_mint_malo.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_mint_malo.hdrs)
    assert r2.status_code == 502
    with pase_mint_malo.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []


def test_a_MISSPELLED_OPERATOR_building_FOR_REAL_leaves_no_row(pase_operador_mal_escrito):
    # The exact trap from round 3: the DESTINATION wallet is fine (so the configuration 503
    # doesn't trigger), and the one with the typo is the operator's, which goes as `fee_payer`
    # and which the endpoint doesn't validate. It blows up while building, and leaves no trace.
    c = pase_operador_mal_escrito
    r = c.post("/gacha/tracker-pass", json={"days": 7}, headers=c.hdrs)
    assert r.status_code == 502
    with c.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []
    r2 = c.post("/gacha/tracker-pass", json={"days": 7}, headers=c.hdrs)
    assert r2.status_code == 502, "still broken, but the wallet is not locked out"


def test_a_truly_MALFORMED_blockhash_leaves_no_row_and_the_retry_WORKS(pase_construccion_real):
    # `Hash.from_string` raises `ParseHashError` inside `build_token_transfer`. Unlike the two
    # above, this one does fix itself as soon as the RPC gives out a good blockhash again, and
    # then the purchase goes through end to end: building, signing and reading the signature,
    # all for real.
    c = pase_construccion_real
    c.mando["blockhash"] = "no-es-un-blockhash"
    r = c.post("/gacha/tracker-pass", json={"days": 7}, headers=c.hdrs)
    assert r.status_code == 502
    with c.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == [], "not even one row acting as a lock"

    c.mando["blockhash"] = "11111111111111111111111111111111"
    r2 = c.post("/gacha/tracker-pass", json={"days": 7}, headers=c.hdrs)
    assert r2.status_code == 200, r2.text
    with c.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "active"
        # The signature is the one that came out of signing for real, read from the transaction
        # bytes: 64 bytes in base58, never the all-zeros one (`1111…`) from an unsigned
        # transaction.
        assert p.tx_signature and set(p.tx_signature) != {"1"}
