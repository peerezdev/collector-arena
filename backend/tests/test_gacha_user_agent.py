"""Every call to Collector Crypt must carry a browser User-Agent.

Their Cloudflare answers 403 to httpx's default agent. Measured on 2026-10-19: the same request
with `python-httpx/0.27.0` gets 403 and with a Chrome agent gets 200, with or without the API key.

It took production down at 11:39 that day — 1,427 failures in 24 h, no machine catalogue, the
gacha dark for players — and the symptom gave no clue: a plain 403 from an endpoint that had
worked for months and whose credentials had not changed.
"""
import httpx
import pytest
import respx

from app.services.gacha import GachaService


@respx.mock
@pytest.mark.asyncio
async def test_las_llamadas_llevan_user_agent_de_navegador():
    # `machines()` consulta además /api/status para saber si las gratis están abiertas.
    respx.get("https://cc.example.com/api/status").mock(
        return_value=httpx.Response(200, json={"freeSpinsEnabled": True}))
    ruta = respx.get("https://cc.example.com/api/machines").mock(
        return_value=httpx.Response(200, json={"machines": []}))
    await GachaService(base_url="https://cc.example.com", api_key="k").machines()

    ua = ruta.calls.last.request.headers.get("user-agent", "")
    assert "Mozilla/" in ua, (
        f"user-agent enviado: {ua!r} — sin uno de navegador, Cloudflare devuelve 403 y el gacha "
        "se queda sin catálogo")


@respx.mock
@pytest.mark.asyncio
async def test_tambien_en_las_llamadas_que_escriben():
    """No solo en las lecturas: una tirada que falle por esto cuesta dinero, no solo una pantalla."""
    respx.get("https://cc.example.com/api/getPoints").mock(
        return_value=httpx.Response(200, json={"points": 0, "usedPoints": 0}))
    ruta = respx.post("https://cc.example.com/api/generateFreePack").mock(
        return_value=httpx.Response(200, json={"nonce": "n"}))
    await GachaService(base_url="https://cc.example.com", api_key="k").generate_free_pack(
        player_address="W", pack_type="pokemon_25")

    assert "Mozilla/" in ruta.calls.last.request.headers.get("user-agent", "")
