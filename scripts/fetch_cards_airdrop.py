#!/usr/bin/env python3
"""Genera el fichero de asignaciones del airdrop $CARDS desde el bundle de Collector Crypt.

El fichero se COMMITEA en vez de descargarse en caliente: si CC redespliega su bundle, el
contenido de nuestro claim no puede cambiar sin que aparezca en un diff.

Cada hoja se valida contra la root que está en la cadena ANTES de escribir nada, y a la
primera que no case el script aborta sin dejar fichero. Así un bundle manipulado o una
descarga a medias se detectan aquí y no cuando un jugador le da al botón.

Uso:  backend/.venv/bin/python scripts/fetch_cards_airdrop.py
"""
from __future__ import annotations

import base64
import json
import re
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.services.cards_airdrop import hoja, verificar_proof  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402

CLAIM_URL = "https://claim.collectorcrypt.com"
RPC = "https://api.mainnet-beta.solana.com"
DISTRIBUTOR = "H6k7zSjCn2w5Q4em3b3E7iaPQfLrxVsF6u1bK6kD1Bhq"
MINT = "CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp"
SALIDA = RAIZ / "backend" / "data" / "cards_airdrop_2026-09.json"


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "battle-arena/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf8", "replace")


def _rpc(method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["result"]


def root_on_chain() -> bytes:
    """La root vive dentro de la cuenta del distribuidor: 8 bytes de discriminador, 32 de
    `base`, 1 de bump y luego los 32 de la root."""
    v = _rpc("getAccountInfo", [DISTRIBUTOR, {"encoding": "base64"}])["value"]
    if v is None:
        raise SystemExit("el distribuidor ya no existe en mainnet: CC cerró el airdrop")
    return base64.b64decode(v["data"][0])[41:73]


def lista_del_bundle() -> list[dict]:
    """El hash del nombre del bundle cambia en cada despliegue de CC, así que se lee del
    index en vez de escribirlo a mano."""
    index = _get(CLAIM_URL + "/")
    m = re.search(r'src="(/js/bundle\.[a-f0-9]+\.min\.js)"', index)
    if not m:
        raise SystemExit("no se encontró el bundle en el index de CC: han cambiado su build")
    bundle = _get(CLAIM_URL + m.group(1))

    marca = 'JSON.parse(\'[{"handle"'
    ini = bundle.find(marca)
    if ini < 0:
        raise SystemExit("no se encontró la lista de asignaciones dentro del bundle")
    ini = bundle.index("'", ini)
    fin = ini + 1
    while True:  # el cierre es la primera comilla precedida por una racha PAR de backslashes
        fin = bundle.index("'", fin)
        racha = 0
        while bundle[fin - 1 - racha] == "\\":
            racha += 1
        if racha % 2 == 0:
            break
        fin += 1
    crudo = bundle[ini + 1:fin].replace("\\'", "'").replace("\\\\", "\\")
    return json.loads(crudo)


def main() -> None:
    root = root_on_chain()
    entradas = lista_del_bundle()
    print(f"{len(entradas)} entradas en el bundle de CC")

    salida: dict[str, dict] = {}
    total = 0
    for e in entradas:
        qs = dict(p.split("=", 1) for p in e["url"].split("?", 1)[1].split("&"))
        index = int(qs["index"])
        amount = int(e["amount"])
        proof_b58 = qs["proof"].split(",")
        proof = [bytes(Pubkey.from_string(p)) for p in proof_b58]
        if not verificar_proof(hoja(index, e["handle"], MINT, amount), proof, root):
            raise SystemExit(f"ABORTADO: la hoja de {e['handle']} (index {index}) no casa con la root")
        if e["handle"] in salida:
            # el árbol de Gumdrop está indexado por index, no por wallet: nada impide que una
            # wallet tenga dos hojas. Como el fichero de salida SÍ está indexado por wallet, un
            # duplicado silencioso pisaría una de las dos asignaciones sin que la validación
            # merkle lo detecte, porque las dos hojas son individualmente válidas.
            raise SystemExit(
                f"ABORTADO: {e['handle']} aparece dos veces en la lista de CC "
                f"(índices {salida[e['handle']]['i']} y {index}). El fichero va "
                f"indexado por wallet y perdería una de las dos asignaciones."
            )
        salida[e["handle"]] = {"i": index, "a": amount, "p": proof_b58}
        total += amount

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps(salida, separators=(",", ":")), encoding="utf8")
    print(f"OK: {len(salida)} wallets, {total / 1e6:,.0f} CARDS -> {SALIDA}")


if __name__ == "__main__":
    main()
