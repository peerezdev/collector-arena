"""A charge having been SENT doesn't mean it arrived.

`submit_signed_tx` does `sendTransaction` and returns the signature without waiting for anything.
Activating a pass on top of that would give away access every time a transaction drops after
being sent.
"""
import json

import httpx
import pytest

from app.services.solana_tx import confirmar_firma


class _Resp:
    """Fake response from `getSignatureStatuses`: it only needs `raise_for_status` and `json`."""

    def __init__(self, cuerpo):
        self._cuerpo = cuerpo

    def raise_for_status(self):
        pass

    def json(self):
        return self._cuerpo


class _RompeJSON(_Resp):
    """200 with a body that is NOT JSON: a degraded gateway, a proxy that returns HTML, or an
    empty response under load. `.json()` blows up with `json.JSONDecodeError`, which is NOT a
    subclass of `httpx.HTTPError`: that's exactly the gap this test exists to catch."""

    def __init__(self):
        super().__init__(None)

    def json(self):
        raise json.JSONDecodeError("no es JSON", "doc", 0)


def _cliente(monkeypatch, eventos):
    """Replaces `httpx.AsyncClient` with one that, on every `post`, consumes the next item from
    `eventos`: if it's an exception it raises it (network failure before receiving anything); if
    it's already a `_Resp` it returns it as-is (to force odd bodies like `_RompeJSON`); if it's
    anything else (a dict or `None`) it wraps it as the `value` of `getSignatureStatuses`. Once
    the list is exhausted, it keeps returning `None`: "still don't know". Counts the calls."""
    llamadas = {"n": 0}

    class _Cli:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, timeout=None):
            llamadas["n"] += 1
            evento = eventos.pop(0) if eventos else None
            if isinstance(evento, Exception):
                raise evento
            if isinstance(evento, _Resp):
                return evento
            return _Resp({"jsonrpc": "2.0", "id": 1, "result": {"value": [evento]}})

    monkeypatch.setattr(httpx, "AsyncClient", _Cli)
    return llamadas


@pytest.mark.asyncio
async def test_confirmed_on_the_first_try(monkeypatch):
    llamadas = _cliente(monkeypatch, [{"confirmationStatus": "confirmed", "err": None}])
    assert await confirmar_firma("http://rpc", "5xFirma") is True
    assert llamadas["n"] == 1


@pytest.mark.asyncio
async def test_a_transaction_with_an_error_is_not_confirmed(monkeypatch):
    # The case that matters most: the chain accepted it and executed it WRONG. If this returned
    # True, we'd be activating a pass nobody paid for.
    _cliente(monkeypatch, [{"confirmationStatus": "confirmed", "err": {"InstructionError": 1}}])
    assert await confirmar_firma("http://rpc", "5xFirma") is False


@pytest.mark.asyncio
async def test_waits_while_the_rpc_still_does_not_know(monkeypatch):
    # `null` means "I don't know it yet", which is normal in the first instants.
    llamadas = _cliente(monkeypatch, [None, None, {"confirmationStatus": "finalized", "err": None}])
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=5, espera_s=0) is True
    assert llamadas["n"] == 3


@pytest.mark.asyncio
async def test_if_it_never_confirms_it_returns_indeterminate_and_does_not_blow_up(monkeypatch):
    # `None`, not `False`: we never saw an `err` or a confirmation, so we don't know whether the
    # money moved. Returning `False` here would be asserting "definitely not" about something
    # that isn't known.
    llamadas = _cliente(monkeypatch, [None] * 10)
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=3, espera_s=0) is None
    assert llamadas["n"] == 3  # doesn't poll more than what it was asked to


@pytest.mark.asyncio
async def test_processed_is_not_enough(monkeypatch):
    # `processed` can be reverted. Only `confirmed` and `finalized` count; running out of
    # attempts stuck on `processed` is indeterminate, not a rejection.
    _cliente(monkeypatch, [{"confirmationStatus": "processed", "err": None}] * 3)
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=3, espera_s=0) is None


# ── a network failure or a weird body must not take down the user's request ─────────────────
#
# If `except httpx.HTTPError: continue` were changed to `except httpx.HTTPError: return True`,
# none of the tests above would notice: none of them make `post` fail. These three right here are
# exactly the ones that prove that gap is real, and close it.

@pytest.mark.asyncio
async def test_a_network_failure_does_not_prevent_confirming_later(monkeypatch):
    # The first attempt doesn't even get a response; that shouldn't stop it from trying again.
    llamadas = _cliente(monkeypatch, [httpx.ConnectError("down"),
                                      {"confirmationStatus": "confirmed", "err": None}])
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=5, espera_s=0) is True
    assert llamadas["n"] == 2


@pytest.mark.asyncio
async def test_if_the_network_always_fails_it_returns_indeterminate_and_does_not_blow_up(monkeypatch):
    # The network never responded: it's not a rejection, it's not knowing. `None`, not `False`.
    llamadas = _cliente(monkeypatch, [httpx.ConnectError("down")] * 10)
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=3, espera_s=0) is None
    assert llamadas["n"] == 3


@pytest.mark.asyncio
async def test_a_body_that_is_not_json_does_not_blow_up(monkeypatch):
    # Before, this let `json.JSONDecodeError` escape, which is not `httpx.HTTPError`: the user's
    # request blew up instead of simply retrying.
    llamadas = _cliente(monkeypatch, [_RompeJSON(),
                                      {"confirmationStatus": "confirmed", "err": None}])
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=5, espera_s=0) is True
    assert llamadas["n"] == 2
