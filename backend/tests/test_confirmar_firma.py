"""Que un cobro se haya ENVIADO no es que haya llegado.

`submit_signed_tx` hace `sendTransaction` y devuelve la firma sin esperar nada. Activar un pase
sobre eso regalaría el acceso cada vez que una transacción se cae después de enviarse.
"""
import json

import httpx
import pytest

from app.services.solana_tx import confirmar_firma


class _Resp:
    """Respuesta falsa de `getSignatureStatuses`: solo necesita `raise_for_status` y `json`."""

    def __init__(self, cuerpo):
        self._cuerpo = cuerpo

    def raise_for_status(self):
        pass

    def json(self):
        return self._cuerpo


class _RompeJSON(_Resp):
    """200 con un cuerpo que NO es JSON: una pasarela degradada, un proxy que devuelve HTML o una
    respuesta vacía bajo carga. `.json()` revienta con `json.JSONDecodeError`, que NO es subclase
    de `httpx.HTTPError` — es justo el hueco que este test existe para cazar."""

    def __init__(self):
        super().__init__(None)

    def json(self):
        raise json.JSONDecodeError("no es JSON", "doc", 0)


def _cliente(monkeypatch, eventos):
    """Sustituye `httpx.AsyncClient` por uno que, en cada `post`, consume el siguiente elemento de
    `eventos`: si es una excepción la lanza (fallo de red antes de recibir nada); si ya es una
    `_Resp` la devuelve tal cual (para forzar cuerpos raros como `_RompeJSON`); si es cualquier
    otra cosa (un dict o `None`) la envuelve como el `value` de `getSignatureStatuses`. Agotada la
    lista, sigue devolviendo `None`: "todavía no lo sé". Cuenta las llamadas."""
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
async def test_confirmada_a_la_primera(monkeypatch):
    llamadas = _cliente(monkeypatch, [{"confirmationStatus": "confirmed", "err": None}])
    assert await confirmar_firma("http://rpc", "5xFirma") is True
    assert llamadas["n"] == 1


@pytest.mark.asyncio
async def test_una_transaccion_con_error_no_esta_confirmada(monkeypatch):
    # El caso que más importa: la cadena la aceptó y la ejecutó MAL. Si esto devolviera True,
    # activaríamos un pase que nadie pagó.
    _cliente(monkeypatch, [{"confirmationStatus": "confirmed", "err": {"InstructionError": 1}}])
    assert await confirmar_firma("http://rpc", "5xFirma") is False


@pytest.mark.asyncio
async def test_espera_mientras_el_rpc_todavia_no_sabe(monkeypatch):
    # `null` es "no la conozco todavía", que es lo normal en los primeros instantes.
    llamadas = _cliente(monkeypatch, [None, None, {"confirmationStatus": "finalized", "err": None}])
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=5, espera_s=0) is True
    assert llamadas["n"] == 3


@pytest.mark.asyncio
async def test_si_nunca_se_confirma_devuelve_indeterminado_y_no_revienta(monkeypatch):
    # `None`, no `False`: nunca vimos ni un `err` ni una confirmación, así que no sabemos si el
    # dinero se movió. Devolver `False` aquí sería afirmar "seguro que no" sobre algo que no se
    # sabe.
    llamadas = _cliente(monkeypatch, [None] * 10)
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=3, espera_s=0) is None
    assert llamadas["n"] == 3  # no sondea más de lo que se le pidió


@pytest.mark.asyncio
async def test_processed_no_basta(monkeypatch):
    # `processed` puede revertirse. Solo valen `confirmed` y `finalized`; agotar los intentos
    # atascado en `processed` es indeterminado, no un rechazo.
    _cliente(monkeypatch, [{"confirmationStatus": "processed", "err": None}] * 3)
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=3, espera_s=0) is None


# ── un fallo de red o un cuerpo raro no deben tumbar la petición del usuario ─────────────────
#
# Si `except httpx.HTTPError: continue` se cambiara por `except httpx.HTTPError: return True`,
# ningún test de arriba lo notaría: ninguno hace que `post` falle. Son justo estos tres los que
# demuestran que ese hueco es real y lo cierran.

@pytest.mark.asyncio
async def test_un_fallo_de_red_no_impide_confirmar_despues(monkeypatch):
    # El primer intento no llega ni a tener respuesta; eso no debe impedir seguir probando.
    llamadas = _cliente(monkeypatch, [httpx.ConnectError("caída"),
                                      {"confirmationStatus": "confirmed", "err": None}])
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=5, espera_s=0) is True
    assert llamadas["n"] == 2


@pytest.mark.asyncio
async def test_si_la_red_falla_siempre_devuelve_indeterminado_y_no_revienta(monkeypatch):
    # La red nunca respondió: no es un rechazo, es no saber. `None`, no `False`.
    llamadas = _cliente(monkeypatch, [httpx.ConnectError("caída")] * 10)
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=3, espera_s=0) is None
    assert llamadas["n"] == 3


@pytest.mark.asyncio
async def test_un_cuerpo_que_no_es_json_no_revienta(monkeypatch):
    # Antes esto dejaba escapar `json.JSONDecodeError`, que no es `httpx.HTTPError`: la petición
    # del usuario reventaba en vez de simplemente reintentar.
    llamadas = _cliente(monkeypatch, [_RompeJSON(),
                                      {"confirmationStatus": "confirmed", "err": None}])
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=5, espera_s=0) is True
    assert llamadas["n"] == 2
