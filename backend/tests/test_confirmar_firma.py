"""Que un cobro se haya ENVIADO no es que haya llegado.

`submit_signed_tx` hace `sendTransaction` y devuelve la firma sin esperar nada. Activar un pase
sobre eso regalaría el acceso cada vez que una transacción se cae después de enviarse.
"""
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


def _cliente(monkeypatch, respuestas):
    """Sustituye `httpx.AsyncClient` por uno que devuelve, en cada `post`, el siguiente valor de
    `respuestas` (agotada la lista, `None`: "todavía no lo sé"), y cuenta las llamadas."""
    llamadas = {"n": 0}

    class _Cli:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, timeout=None):
            llamadas["n"] += 1
            valor = respuestas.pop(0) if respuestas else None
            return _Resp({"jsonrpc": "2.0", "id": 1, "result": {"value": [valor]}})

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
async def test_si_nunca_se_confirma_devuelve_false_y_no_revienta(monkeypatch):
    llamadas = _cliente(monkeypatch, [None] * 10)
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=3, espera_s=0) is False
    assert llamadas["n"] == 3  # no sondea más de lo que se le pidió


@pytest.mark.asyncio
async def test_processed_no_basta(monkeypatch):
    # `processed` puede revertirse. Solo valen `confirmed` y `finalized`.
    _cliente(monkeypatch, [{"confirmationStatus": "processed", "err": None}] * 3)
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=3, espera_s=0) is False
