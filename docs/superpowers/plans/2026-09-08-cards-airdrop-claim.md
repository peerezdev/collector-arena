# Claim del airdrop $CARDS desde la app - plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un jugador cuya wallet embedded esté en el airdrop $CARDS de Collector Crypt pueda reclamarlo desde `/claim` sin tener SOL, pagando el operador.

**Architecture:** El backend construye la transacción entera (ATA idempotente + `claim` de Gumdrop) con el operador de fee payer, la firma el jugador vía session signer de Privy, la firma el operador y se envía. El frontend solo consulta y dispara. La lista de asignaciones es un fichero commiteado que se carga en memoria al arrancar.

**Tech Stack:** Python 3.9 / FastAPI / SQLAlchemy / solders / pycryptodome en el backend; React 19 / react-router / vitest en el frontend.

**Spec:** `docs/superpowers/specs/2026-09-08-cards-airdrop-claim-design.md`

## Global Constraints

- **Solo mainnet.** Con las variables `CARDS_AIRDROP_*` vacías la función queda apagada, que es el estado en devnet y en cualquier entorno sin configurar.
- **Solo la wallet embedded.** La wallet sale SIEMPRE del identity token (`Depends(current_user)`). El cliente nunca manda una wallet en el cuerpo ni en la query.
- **Paga el operador, siempre.** Sin `privy_operator_wallet_id` y `privy_operator_address` los dos endpoints responden 503. Nunca se cae hacia "que pague el jugador".
- **No saber no es saber que no.** Un fallo de RPC, de Privy o del fichero es un error reintentable (502/503). Jamás se traduce a "no elegible".
- **Valores de la ronda** (verificados contra mainnet el 2026-09-08):
  - programa Gumdrop `gdrpGjVffourzkdDRrQmySw4aTHr8a3xmQzzxSwFD1a`
  - distribuidor `H6k7zSjCn2w5Q4em3b3E7iaPQfLrxVsF6u1bK6kD1Bhq`
  - bóveda `5TBR7KQHbPsf3wHZ11dyL9iifztCnN9Ccr6rzoCvYqW7`
  - mint `CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp` (6 decimales)
  - root `edef2b3b6e41e35a7843cd3521490a576651df938ffc40fb3e79c9de146503bd`
- **La ronda se nombra por su distribuidor**, no por un trimestre. No inventar etiquetas tipo "Q2".
- **Comentarios en castellano**, siguiendo el estilo del repo: explican POR QUÉ, no qué hace la línea.

## Estructura de ficheros

| Fichero | Responsabilidad |
|---|---|
| `backend/app/services/cards_airdrop.py` | Todo lo del airdrop que no habla con la red: merkle, PDA, carga del fichero y construcción de la tx |
| `backend/tests/test_cards_airdrop.py` | Tests del servicio, con datos reales de mainnet |
| `scripts/fetch_cards_airdrop.py` | Genera el fichero de asignaciones desde el bundle de CC, validando contra la cadena |
| `backend/data/cards_airdrop_2026-09.json` | Las 4.453 asignaciones, commiteadas |
| `backend/app/config.py` | Las cinco variables nuevas |
| `backend/app/models.py` | Tabla `airdrop_claims` |
| `backend/app/main.py` | Los dos endpoints y sus guardas |
| `backend/tests/test_cards_airdrop_api.py` | Tests de los endpoints |
| `src/onchain/airdropClient.ts` | Cliente de los dos endpoints |
| `src/ui/screens/Claim/ClaimScreen.tsx` | La pantalla y sus estados |
| `src/App.tsx` | La ruta `/claim` |

---

### Task 1: Primitivas del merkle de Gumdrop

Es la pieza que puede fallar en silencio: un hashing mal hecho no revienta, solo hace que la cadena rechace la transacción. Por eso se prueba contra una entrada real de mainnet y no contra un árbol de mentira.

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/app/services/cards_airdrop.py`
- Test: `backend/tests/test_cards_airdrop.py`

**Interfaces:**
- Consumes: nada.
- Produces: `hoja(index: int, claimant: str, mint: str, amount: int) -> bytes`, `verificar_proof(hoja_bytes: bytes, proof: list[bytes], root: bytes) -> bool`, `claim_status_pda(index: int, distributor: str) -> tuple[Pubkey, int]`, constante `GUMDROP_PROGRAM: Pubkey`.

- [ ] **Step 1: Añadir la dependencia de keccak**

El backend no tiene keccak-256 y `SHA3_256` NO sirve como sustituto: es el mismo Keccak pero con otro padding, así que da hashes distintos. Añade al final de `backend/requirements.txt`:

```
pycryptodome==3.23.0
```

Instálalo:

```bash
backend/.venv/bin/pip install pycryptodome==3.23.0
```

- [ ] **Step 2: Escribir el test que falla**

Crea `backend/tests/test_cards_airdrop.py`:

```python
"""Datos REALES de mainnet, capturados el 2026-09-08.

El test dorado es el primero: si el hashing del árbol se desvía lo más mínimo, deja de
casar con la root que está en la cadena y este test se pone rojo. Sin él, un cambio en
esas 20 líneas no rompe nada visible hasta que un jugador le da al botón y la cadena le
rechaza la transacción.
"""
from solders.pubkey import Pubkey

from app.services.cards_airdrop import claim_status_pda, hoja, verificar_proof

DISTRIBUTOR = "H6k7zSjCn2w5Q4em3b3E7iaPQfLrxVsF6u1bK6kD1Bhq"
MINT = "CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp"
ROOT = bytes.fromhex("edef2b3b6e41e35a7843cd3521490a576651df938ffc40fb3e79c9de146503bd")

WALLET = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"
INDEX = 1687
AMOUNT = 1_483_000_000
PROOF = [
    "9Ad5fSi8QPvs8CimVj8vFwSXJkkZ5fgvnhmefmr6QEKv",
    "AyiMrqFWe4hSPXmxuMqAzVLWExLuCH4LiRBQZnWNdnzM",
    "AS2kiSZyCnX31sYKDMxnxCXcyuhXR1VRvSRLJPHFpYUW",
    "E6xE2sR5PztQDaRZZ3eNBGEiuE48Mk7JegjkWH8x3RmK",
    "6AoBdX8FvPH4cQ1XShbT3oeJCsqCCehtbQx3EFzz7neu",
    "8HbxK8ihpGv2nSQ9fXZ8Wiqogn51YnyTLqDbYVGQKYsh",
    "PrrjJUtKGF5fmTR9Yae6J6QbCKz2SzrvhqEmPVQ2XgZ",
    "4utS1WxK3ox27bMrx5uxrBQnWCX2bwFYTqK8f6doNsg9",
    "GV61PisXTh1C6f9PVWnnX9LJWwGyDcfKmXz69yq6nSsg",
    "Hg1tmDKuGtTZ4sEvZR9YDR3pqmQ9VVg3YmUJHosZ2SG",
    "HV2qmcXHb6cm7eZ1m6faMP79rR132jDJyWjcHSxVZsJ",
    "5ye3ARu1aNukAebBW8jGFLgYktiCR4C4et8ipEuQzaAo",
    "FXyuDoR4Vczd2RUfPCkQT7bviNZwDz1T3k8KWfq77mkm",
]


def proof_bytes() -> list[bytes]:
    return [bytes(Pubkey.from_string(p)) for p in PROOF]


def test_la_hoja_real_valida_contra_la_root_de_mainnet():
    assert verificar_proof(hoja(INDEX, WALLET, MINT, AMOUNT), proof_bytes(), ROOT)


def test_una_cantidad_cambiada_no_valida():
    # Es la mitad que importa: si esto pasara, cualquiera podría pedir lo que quisiera.
    assert not verificar_proof(hoja(INDEX, WALLET, MINT, AMOUNT + 1), proof_bytes(), ROOT)


def test_otra_wallet_con_el_mismo_proof_no_valida():
    otra = "FzRt4Pnh6tBpavXqkwQH1WVByeotDSefyyACKXC5kGHZ"
    assert not verificar_proof(hoja(INDEX, otra, MINT, AMOUNT), proof_bytes(), ROOT)


def test_la_hoja_tiene_la_forma_que_espera_gumdrop():
    b = hoja(INDEX, WALLET, MINT, AMOUNT)
    assert len(b) == 80                                  # 8 + 32 + 32 + 8
    assert b[:8] == INDEX.to_bytes(8, "little")
    assert b[8:40] == bytes(Pubkey.from_string(WALLET))
    assert b[40:72] == bytes(Pubkey.from_string(MINT))
    assert b[72:] == AMOUNT.to_bytes(8, "little")


def test_la_pda_de_claim_status_es_la_de_la_cadena():
    pda, bump = claim_status_pda(INDEX, DISTRIBUTOR)
    assert str(pda) == "E1frLrGw1V1mVN6R7KcTwvBYD87ezWZKrspbs5dWJH6g"
    assert bump == 255
```

- [ ] **Step 3: Ejecutar el test y verificar que falla**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cards_airdrop.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.services.cards_airdrop'`

- [ ] **Step 4: Escribir la implementación mínima**

Crea `backend/app/services/cards_airdrop.py`:

```python
"""Claim del airdrop $CARDS de Collector Crypt, que es un Metaplex Gumdrop.

Las piezas puras van separadas de todo lo que habla con la red porque son las únicas que
se pueden probar de verdad: el hashing del árbol se contrasta contra una entrada real de
mainnet sin abrir un socket.
"""
from __future__ import annotations

import struct

from Crypto.Hash import keccak
from solders.pubkey import Pubkey

GUMDROP_PROGRAM = Pubkey.from_string("gdrpGjVffourzkdDRrQmySw4aTHr8a3xmQzzxSwFD1a")


def _keccak256(data: bytes) -> bytes:
    h = keccak.new(digest_bits=256)
    h.update(data)
    return h.digest()


def hoja(index: int, claimant: str, mint: str, amount: int) -> bytes:
    """La hoja del árbol: index_le8 || claimant(32) || mint(32) || amount_le8.

    El orden y el endianness no son negociables: es lo que hashea el programa on-chain
    para comprobar el proof, así que cualquier variación lo invalida.
    """
    return (
        struct.pack("<Q", index)
        + bytes(Pubkey.from_string(claimant))
        + bytes(Pubkey.from_string(mint))
        + struct.pack("<Q", amount)
    )


def verificar_proof(hoja_bytes: bytes, proof: list[bytes], root: bytes) -> bool:
    """Rehace el camino de la hoja hasta la raíz y compara.

    Gumdrop prefija 0x00 a las hojas y 0x01 a los nodos internos (para que una hoja no
    pueda hacerse pasar por un nodo), y ordena los dos hijos byte a byte antes de
    juntarlos, así que el proof no necesita decir si cada hermano va a izquierda o derecha.
    """
    h = _keccak256(b"\x00" + hoja_bytes)
    for hermano in proof:
        izq, der = sorted([h, hermano])
        h = _keccak256(b"\x01" + izq + der)
    return h == root


def claim_status_pda(index: int, distributor: str) -> tuple[Pubkey, int]:
    """Que esta cuenta exista es la ÚNICA señal fiable de "ya reclamado".

    El saldo de la wallet no vale: los tokens se pueden haber vendido y seguiría siendo
    cierto que ya se reclamaron. Y nuestra propia tabla tampoco, porque alguien puede
    haber reclamado en la web de CC sin pasar por aquí.
    """
    return Pubkey.find_program_address(
        [b"ClaimStatus", struct.pack("<Q", index), bytes(Pubkey.from_string(distributor))],
        GUMDROP_PROGRAM,
    )
```

- [ ] **Step 5: Ejecutar los tests y verificar que pasan**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cards_airdrop.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/app/services/cards_airdrop.py backend/tests/test_cards_airdrop.py
git commit -m "feat(airdrop): merkle de Gumdrop, anclado a una hoja real de mainnet

El hashing del árbol es lo único de esto que puede fallar sin hacer ruido: si se
desvía, no revienta nada, simplemente la cadena rechaza el claim meses después.
Por eso el test dorado usa la entrada real del índice 1687 y la root que está en
la cuenta del distribuidor, en vez de un árbol sintético que solo se compararía
consigo mismo.

Entra pycryptodome porque el backend no tenía keccak-256 y SHA3_256 no sirve:
es el mismo Keccak con otro padding, así que da hashes distintos."
```

---

### Task 2: Script generador del fichero de asignaciones

**Files:**
- Create: `scripts/fetch_cards_airdrop.py`
- Create: `backend/data/cards_airdrop_2026-09.json` (lo genera el script)

**Interfaces:**
- Consumes: `hoja` y `verificar_proof` de la Task 1.
- Produces: el fichero `backend/data/cards_airdrop_2026-09.json` con la forma `{ wallet: {"i": index, "a": amount, "p": [proof en base58]} }`.

- [ ] **Step 1: Escribir el script**

Crea `scripts/fetch_cards_airdrop.py`:

```python
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
    while True:  # el cierre es la primera comilla simple NO escapada
        fin = bundle.index("'", fin)
        if bundle[fin - 1] != "\\":
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
        salida[e["handle"]] = {"i": index, "a": amount, "p": proof_b58}
        total += amount

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps(salida, separators=(",", ":")), encoding="utf8")
    print(f"OK: {len(salida)} wallets, {total / 1e6:,.0f} CARDS -> {SALIDA}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Ejecutarlo y comprobar la salida**

Run: `backend/.venv/bin/python scripts/fetch_cards_airdrop.py`
Expected: `OK: 4453 wallets, 14,999,777 CARDS -> .../backend/data/cards_airdrop_2026-09.json`

Si el número de wallets o el total no son esos, PARA: significa que CC ha cambiado la ronda y hay que revisar el spec antes de seguir.

- [ ] **Step 3: Comprobar a mano que la entrada dorada está en el fichero**

Run:

```bash
backend/.venv/bin/python -c "
import json
d = json.load(open('backend/data/cards_airdrop_2026-09.json'))
e = d['8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6']
assert e['i'] == 1687 and e['a'] == 1483000000 and len(e['p']) == 13, e
print('entrada dorada OK:', e['i'], e['a'], len(e['p']), 'hashes')
"
```

Expected: `entrada dorada OK: 1687 1483000000 13 hashes`

- [ ] **Step 4: Commit**

```bash
git add scripts/fetch_cards_airdrop.py backend/data/cards_airdrop_2026-09.json
git commit -m "feat(airdrop): script que baja las asignaciones y las valida contra la cadena

El fichero se commitea en vez de descargarse al vuelo para que el contenido de
nuestro claim no pueda cambiar sin aparecer en un diff, ya que sale del bundle de
un tercero.

El script aborta sin escribir a la primera hoja que no case con la root que está
en la cuenta del distribuidor, así que un bundle manipulado o una descarga a
medias mueren aquí y no delante de un jugador."
```

---

### Task 3: Carga del fichero y variables de configuración

**Files:**
- Modify: `backend/app/services/cards_airdrop.py`
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_cards_airdrop.py`

**Interfaces:**
- Consumes: el fichero de la Task 2.
- Produces: `cargar_asignaciones(path: str) -> dict[str, dict]`; ajustes `cards_airdrop_file`, `cards_airdrop_distributor`, `cards_airdrop_vault`, `cards_airdrop_mint`, `cards_airdrop_round` en `Settings`.

- [ ] **Step 1: Escribir los tests que fallan**

Añade al final de `backend/tests/test_cards_airdrop.py`:

```python
import json

from app.services.cards_airdrop import cargar_asignaciones


def test_carga_el_fichero_de_asignaciones(tmp_path):
    f = tmp_path / "a.json"
    f.write_text(json.dumps({WALLET: {"i": INDEX, "a": AMOUNT, "p": PROOF}}))
    d = cargar_asignaciones(str(f))
    assert d[WALLET]["i"] == INDEX


def test_sin_ruta_devuelve_vacio_en_vez_de_reventar():
    # Es el estado normal en devnet: la función está apagada, no rota.
    assert cargar_asignaciones("") == {}


def test_un_fichero_que_no_existe_devuelve_vacio(tmp_path):
    assert cargar_asignaciones(str(tmp_path / "no-esta.json")) == {}


def test_un_fichero_corrupto_devuelve_vacio(tmp_path):
    # Vacío hace que los endpoints respondan 503. Lo que NO puede pasar es que un
    # fichero ilegible acabe diciéndole a un jugador elegible que no lo es.
    f = tmp_path / "roto.json"
    f.write_text("{esto no es json")
    assert cargar_asignaciones(str(f)) == {}
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cards_airdrop.py -v`
Expected: FAIL con `ImportError: cannot import name 'cargar_asignaciones'`

- [ ] **Step 3: Implementar la carga**

Añade a `backend/app/services/cards_airdrop.py`, debajo de los imports (añadiendo `import json` y `import logging` arriba, y `logger = logging.getLogger(__name__)`):

```python
def cargar_asignaciones(path: str) -> dict[str, dict]:
    """Lee el fichero de asignaciones una sola vez, al arrancar.

    Devuelve un dict vacío ante cualquier problema, y eso hace que los endpoints
    respondan 503. La alternativa —seguir con media lista— sería peor: le diría a un
    jugador elegible que no lo es, que es exactamente el error que no nos podemos
    permitir aquí.
    """
    if not path:
        return {}
    try:
        with open(path, encoding="utf8") as f:
            return json.load(f)
    except (OSError, ValueError):
        logger.exception("airdrop: no se pudo leer el fichero de asignaciones %s", path)
        return {}
```

- [ ] **Step 4: Añadir los ajustes**

En `backend/app/config.py`, dentro de `Settings`, después de `battle_fee_pct_cap`:

```python
    # Claim del airdrop $CARDS de Collector Crypt. Vacías = apagado, que es el estado en
    # devnet y en cualquier entorno sin configurar. La ronda se identifica por la dirección
    # del distribuidor y no por un trimestre: el criterio con el que CC repartió no lo
    # sabemos, y la única suposición que hicimos al respecto resultó falsa.
    # env: CARDS_AIRDROP_FILE / _DISTRIBUTOR / _VAULT / _MINT / _ROUND
    cards_airdrop_file: str = ""
    cards_airdrop_distributor: str = ""
    cards_airdrop_vault: str = ""
    cards_airdrop_mint: str = ""
    cards_airdrop_round: str = ""
```

En `backend/.env.example`, al final:

```
# Claim del airdrop $CARDS (solo mainnet). Vacías = apagado.
CARDS_AIRDROP_FILE=data/cards_airdrop_2026-09.json
CARDS_AIRDROP_DISTRIBUTOR=H6k7zSjCn2w5Q4em3b3E7iaPQfLrxVsF6u1bK6kD1Bhq
CARDS_AIRDROP_VAULT=5TBR7KQHbPsf3wHZ11dyL9iifztCnN9Ccr6rzoCvYqW7
CARDS_AIRDROP_MINT=CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp
CARDS_AIRDROP_ROUND=2026-09
```

- [ ] **Step 5: Ejecutar los tests y verificar que pasan**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cards_airdrop.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/cards_airdrop.py backend/app/config.py backend/.env.example backend/tests/test_cards_airdrop.py
git commit -m "feat(airdrop): carga del fichero de asignaciones y sus variables

Ante un fichero ausente, ilegible o corrupto se devuelve vacío y los endpoints
responden 503. Seguir con media lista sería peor que no tenerla: le diría a un
jugador elegible que no lo es, y ese es el único error que no nos podemos permitir."
```

---

### Task 4: Construcción de la transacción del claim

**Files:**
- Modify: `backend/app/services/cards_airdrop.py`
- Test: `backend/tests/test_cards_airdrop.py`

**Interfaces:**
- Consumes: `claim_status_pda` y `GUMDROP_PROGRAM` de la Task 1.
- Produces: `ata(owner: Pubkey, mint: Pubkey) -> Pubkey`; `instrucciones_claim(*, claimant, index, amount, proof, distributor, vault, mint, operador, crear_ata) -> list[Instruction]`; `build_claim_tx(*, claimant, index, amount, proof, distributor, vault, mint, operador, blockhash, crear_ata) -> str` (base64).

- [ ] **Step 1: Escribir los tests que fallan**

Añade al final de `backend/tests/test_cards_airdrop.py`:

```python
import base64

from solders.transaction import Transaction as SoldersTx

from app.services.cards_airdrop import ata, build_claim_tx, instrucciones_claim

VAULT = "5TBR7KQHbPsf3wHZ11dyL9iifztCnN9Ccr6rzoCvYqW7"
OPERADOR = "3q6Ucr1s7Knkp5nRQKQe3dYPzoh72XQGnn2oCgSS9S34"
BLOCKHASH = "11111111111111111111111111111111"

GUMDROP = "gdrpGjVffourzkdDRrQmySw4aTHr8a3xmQzzxSwFD1a"
SYS = "11111111111111111111111111111111"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def _ixs(crear_ata=True):
    return instrucciones_claim(
        claimant=WALLET, index=INDEX, amount=AMOUNT, proof=PROOF,
        distributor=DISTRIBUTOR, vault=VAULT, mint=MINT,
        operador=OPERADOR, crear_ata=crear_ata,
    )


def test_las_cuentas_del_claim_van_en_el_orden_del_idl():
    ix = _ixs()[-1]
    assert str(ix.program_id) == GUMDROP
    cuentas = [str(a.pubkey) for a in ix.accounts]
    claim_status, _ = claim_status_pda(INDEX, DISTRIBUTOR)
    assert cuentas == [
        DISTRIBUTOR, str(claim_status), VAULT,
        str(ata(Pubkey.from_string(WALLET), Pubkey.from_string(MINT))),
        WALLET, OPERADOR, SYS, TOKEN,
    ]


def test_firma_el_jugador_como_temporal_y_el_operador_como_payer():
    # Es LA decisión del diseño: Gumdrop admite que `payer` sea otro, y por eso el
    # jugador no necesita SOL. Comprobado por simulación contra mainnet.
    ix = _ixs()[-1]
    assert [a.is_signer for a in ix.accounts] == [False, False, False, False, True, True, False, False]
    assert str(ix.accounts[4].pubkey) == WALLET      # temporal
    assert str(ix.accounts[5].pubkey) == OPERADOR    # payer, y es quien suelta el rent


def test_los_argumentos_llevan_discriminador_bump_index_amount_wallet_y_proof():
    import hashlib
    ix = _ixs()[-1]
    d = bytes(ix.data)
    _, bump = claim_status_pda(INDEX, DISTRIBUTOR)
    assert d[:8] == hashlib.sha256(b"global:claim").digest()[:8]
    assert d[8] == bump
    assert d[9:17] == INDEX.to_bytes(8, "little")
    assert d[17:25] == AMOUNT.to_bytes(8, "little")
    assert d[25:57] == bytes(Pubkey.from_string(WALLET))
    assert d[57:61] == len(PROOF).to_bytes(4, "little")
    assert len(d) == 61 + 32 * len(PROOF)


def test_la_ata_la_paga_el_operador_y_la_posee_el_jugador():
    ix = _ixs()[0]
    assert bytes(ix.data) == bytes([1])              # CreateIdempotent
    assert str(ix.accounts[0].pubkey) == OPERADOR and ix.accounts[0].is_signer
    assert str(ix.accounts[2].pubkey) == WALLET


def test_sin_crear_ata_solo_va_la_instruccion_del_claim():
    ixs = _ixs(crear_ata=False)
    assert len(ixs) == 1
    assert str(ixs[0].program_id) == GUMDROP


def test_el_fee_payer_de_la_transaccion_es_el_operador():
    b64 = build_claim_tx(
        claimant=WALLET, index=INDEX, amount=AMOUNT, proof=PROOF,
        distributor=DISTRIBUTOR, vault=VAULT, mint=MINT,
        operador=OPERADOR, blockhash=BLOCKHASH, crear_ata=True,
    )
    msg = SoldersTx.from_bytes(base64.b64decode(b64)).message
    assert str(msg.account_keys[0]) == OPERADOR
    assert msg.header.num_required_signatures == 2
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cards_airdrop.py -v`
Expected: FAIL con `ImportError: cannot import name 'ata'`

- [ ] **Step 3: Implementar**

Añade a `backend/app/services/cards_airdrop.py` (con `import base64`, `import hashlib` arriba y estos imports de solders):

```python
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.transaction import Transaction

TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYS_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
_DISC_CLAIM = hashlib.sha256(b"global:claim").digest()[:8]


def ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)], ATA_PROGRAM
    )[0]


def instrucciones_claim(*, claimant: str, index: int, amount: int, proof: list[str],
                        distributor: str, vault: str, mint: str, operador: str,
                        crear_ata: bool) -> list[Instruction]:
    """Las instrucciones del claim: la ATA idempotente delante (si hace falta) y el
    `claim` de Gumdrop detrás.

    El operador va de `payer` en las dos, y el jugador solo firma como `temporal`. El
    programa admite que sean cuentas distintas —comprobado por simulación contra
    mainnet— y de ahí sale que el jugador no necesite tener SOL.
    """
    jugador = Pubkey.from_string(claimant)
    op = Pubkey.from_string(operador)
    mint_pk = Pubkey.from_string(mint)
    destino = ata(jugador, mint_pk)
    claim_status, bump = claim_status_pda(index, distributor)

    data = (
        _DISC_CLAIM
        + bytes([bump])
        + struct.pack("<Q", index)
        + struct.pack("<Q", amount)
        + bytes(jugador)                      # claimantSecret: para method=wallets es la propia wallet
        + struct.pack("<I", len(proof))
        + b"".join(bytes(Pubkey.from_string(p)) for p in proof)
    )

    ixs: list[Instruction] = []
    if crear_ata:
        ixs.append(Instruction(ATA_PROGRAM, bytes([1]), [   # 1 = CreateIdempotent
            AccountMeta(op, is_signer=True, is_writable=True),
            AccountMeta(destino, is_signer=False, is_writable=True),
            AccountMeta(jugador, is_signer=False, is_writable=False),
            AccountMeta(mint_pk, is_signer=False, is_writable=False),
            AccountMeta(SYS_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(TOKEN_PROGRAM, is_signer=False, is_writable=False),
        ]))
    ixs.append(Instruction(GUMDROP_PROGRAM, data, [
        AccountMeta(Pubkey.from_string(distributor), is_signer=False, is_writable=True),
        AccountMeta(claim_status, is_signer=False, is_writable=True),
        AccountMeta(Pubkey.from_string(vault), is_signer=False, is_writable=True),
        AccountMeta(destino, is_signer=False, is_writable=True),
        AccountMeta(jugador, is_signer=True, is_writable=False),
        AccountMeta(op, is_signer=True, is_writable=True),
        AccountMeta(SYS_PROGRAM, is_signer=False, is_writable=False),
        AccountMeta(TOKEN_PROGRAM, is_signer=False, is_writable=False),
    ]))
    return ixs


def build_claim_tx(*, claimant: str, index: int, amount: int, proof: list[str],
                   distributor: str, vault: str, mint: str, operador: str,
                   blockhash: str, crear_ata: bool) -> str:
    """La misma transacción, legacy y sin firmar, en base64. Fee payer: el operador."""
    ixs = instrucciones_claim(
        claimant=claimant, index=index, amount=amount, proof=proof,
        distributor=distributor, vault=vault, mint=mint,
        operador=operador, crear_ata=crear_ata,
    )
    msg = Message.new_with_blockhash(ixs, Pubkey.from_string(operador), Hash.from_string(blockhash))
    return base64.b64encode(bytes(Transaction.new_unsigned(msg))).decode()
```

- [ ] **Step 4: Ejecutar los tests y verificar que pasan**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cards_airdrop.py -v`
Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/cards_airdrop.py backend/tests/test_cards_airdrop.py
git commit -m "feat(airdrop): construcción de la tx, con el operador de payer

Gumdrop admite que \`payer\` y \`temporal\` sean cuentas distintas, y en eso se apoya
todo esto: el jugador firma como temporal y el operador suelta el rent del
ClaimStatus y el de la ATA, así que una embedded a cero puede reclamar. Lo
comprobamos por simulación contra mainnet antes de escribir nada, porque ninguna
transacción real lo demostraba: la web de CC pone al usuario en las dos cuentas.

Los tests fijan el orden exacto de las ocho cuentas y el encoding de los
argumentos, que es lo que la cadena rechaza sin explicar si se tuerce."
```

---

### Task 5: Tabla `airdrop_claims` y endpoint de consulta

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_cards_airdrop_api.py`

**Interfaces:**
- Consumes: `cargar_asignaciones`, `claim_status_pda` de las tasks anteriores.
- Produces: modelo `AirdropClaim`; parámetros nuevos de `create_app`: `cards_airdrop: dict | None = None`, `cards_airdrop_distributor: str = ""`, `cards_airdrop_vault: str = ""`, `cards_airdrop_mint: str = ""`, `cards_airdrop_round: str = ""`; endpoint `GET /users/me/airdrop/cards`.

- [ ] **Step 1: Escribir los tests que fallan**

Crea `backend/tests/test_cards_airdrop_api.py`:

```python
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.db import init_db, make_session_factory
from app.main import create_app
from tests.test_chain_mock import MockChainSource

APP_ID = "testapp"
WALLET = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"
WALLET_ID = "wallet-id-aaa"
AJENA = "FzRt4Pnh6tBpavXqkwQH1WVByeotDSefyyACKXC5kGHZ"
DISTRIBUTOR = "H6k7zSjCn2w5Q4em3b3E7iaPQfLrxVsF6u1bK6kD1Bhq"
VAULT = "5TBR7KQHbPsf3wHZ11dyL9iifztCnN9Ccr6rzoCvYqW7"
MINT = "CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp"
OPERADOR = "3q6Ucr1s7Knkp5nRQKQe3dYPzoh72XQGnn2oCgSS9S34"
PROOF = ["9Ad5fSi8QPvs8CimVj8vFwSXJkkZ5fgvnhmefmr6QEKv"]

ASIGNACIONES = {WALLET: {"i": 1687, "a": 1_483_000_000, "p": PROOF}}


def _headers(priv, addr=WALLET, wallet_id=WALLET_ID):
    now = int(time.time())
    cuenta = {"type": "wallet", "chain_type": "solana", "connector_type": None,
              "wallet_client_type": "privy", "address": addr, "id": wallet_id}
    payload = {"aud": APP_ID, "iss": "privy.io", "sub": f"did:privy:{addr[:8]}",
               "iat": now, "exp": now + 3600, "linked_accounts": json.dumps([cuenta])}
    tok = jwt.encode(payload, priv, algorithm="ES256", headers={"kid": "test-kid", "alg": "ES256"})
    return {"Authorization": f"Bearer {tok}"}


class FakeSigner:
    """Firma sin red y recuerda con qué wallet_id se le pidió cada firma."""
    def __init__(self):
        self.firmas: list[tuple[str, str]] = []
        self.enabled = True

    async def sign_solana(self, wallet_id: str, tx: str) -> str:
        self.firmas.append((wallet_id, tx))
        return f"signed::{tx}"

    async def podemos_firmar(self, wallet_id: str) -> bool:
        return True


class FakePrivy:
    def __init__(self, priv):
        self._pub = priv.public_key()

    def embedded_solana_wallet(self, token: str) -> str:
        import jwt as _jwt
        d = _jwt.decode(token, self._pub, algorithms=["ES256"], audience=APP_ID)
        return json.loads(d["linked_accounts"])[0]["address"]

    def embedded_solana_wallet_id(self, token: str) -> str:
        import jwt as _jwt
        d = _jwt.decode(token, self._pub, algorithms=["ES256"], audience=APP_ID)
        return json.loads(d["linked_accounts"])[0]["id"]


def _cliente(**over):
    priv = ec.generate_private_key(ec.SECP256R1())
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)
    kwargs = dict(
        privy=FakePrivy(priv), privy_signer=FakeSigner(),
        privy_operator_wallet_id="op-wallet-id", privy_operator_address=OPERADOR,
        cards_airdrop=dict(ASIGNACIONES),
        cards_airdrop_distributor=DISTRIBUTOR, cards_airdrop_vault=VAULT,
        cards_airdrop_mint=MINT, cards_airdrop_round="2026-09",
        solana_rpc_url="https://api.devnet.solana.com",
    )
    kwargs.update(over)
    app = create_app(sf, MockChainSource(), **kwargs)
    return TestClient(app), priv, kwargs


@pytest.fixture
def sin_pda(monkeypatch):
    """Por defecto, la cuenta de ClaimStatus no existe: nadie ha reclamado."""
    async def _cuenta(rpc_url, pubkey, **kw):
        return None
    monkeypatch.setattr("app.main._airdrop_cuenta", _cuenta)
    return _cuenta


def test_elegible_sin_reclamar(sin_pda):
    c, priv, _ = _cliente()
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.status_code == 200
    assert r.json() == {"eligible": True, "amount": 1_483_000_000,
                        "claimed": False, "signature": None}


def test_no_elegible(sin_pda):
    c, priv, _ = _cliente()
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv, addr=AJENA, wallet_id="otro"))
    assert r.status_code == 200
    assert r.json()["eligible"] is False


def test_ya_reclamado_lo_dice_la_cadena(monkeypatch):
    async def _cuenta(rpc_url, pubkey, **kw):
        return {"lamports": 1}
    monkeypatch.setattr("app.main._airdrop_cuenta", _cuenta)
    c, priv, _ = _cliente()
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.json()["claimed"] is True


def test_sin_fichero_es_503_y_no_no_elegible(sin_pda):
    # La distinción importa: decirle "no eres elegible" a alguien que sí lo es por una
    # avería nuestra es el peor fallo posible aquí.
    c, priv, _ = _cliente(cards_airdrop={})
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.status_code == 503


def test_sin_operador_es_503(sin_pda):
    c, priv, _ = _cliente(privy_operator_wallet_id="", privy_operator_address="")
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.status_code == 503


def test_si_el_rpc_falla_es_502_y_no_no_elegible(monkeypatch):
    async def _cuenta(rpc_url, pubkey, **kw):
        raise RuntimeError("rpc caído")
    monkeypatch.setattr("app.main._airdrop_cuenta", _cuenta)
    c, priv, _ = _cliente()
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.status_code == 502
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cards_airdrop_api.py -v`
Expected: FAIL con `TypeError: create_app() got an unexpected keyword argument 'cards_airdrop'`

- [ ] **Step 3: Añadir el modelo**

En `backend/app/models.py`, al final:

```python
class AirdropClaim(Base):
    """Constancia de un claim de airdrop hecho desde la app.

    No manda sobre nada: la elegibilidad la decide el fichero de asignaciones y si está
    reclamado lo decide la cadena, porque el jugador puede haber reclamado en la web de
    CC sin pasar por aquí. Esta fila existe para poder reenseñarle la firma cuando
    vuelva y para saber cuánto SOL nos ha costado la operación.
    """
    __tablename__ = "airdrop_claims"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(String, index=True)
    ronda: Mapped[str] = mapped_column(String, index=True)
    amount: Mapped[int] = mapped_column(Integer)          # unidades base de CARDS (6 decimales)
    signature: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

- [ ] **Step 4: Añadir el endpoint**

En `backend/app/main.py`, añade los parámetros a `create_app` (junto a los otros, después de `privy_operator_address`):

```python
               cards_airdrop: dict | None = None,
               cards_airdrop_distributor: str = "",
               cards_airdrop_vault: str = "",
               cards_airdrop_mint: str = "",
               cards_airdrop_round: str = "",
```

Añade en `backend/app/services/nft_transfer.py`, justo debajo de la definición de `_get_account`, un alias público para no importar un nombre privado desde fuera:

```python
# Alias público: main.py lo usa para saber si existe una PDA o una ATA sin duplicar el RPC.
leer_cuenta = _get_account
```

Importa arriba en `backend/app/main.py`:

```python
from .models import AirdropClaim
from .services.cards_airdrop import build_claim_tx, claim_status_pda
from .services.nft_transfer import leer_cuenta
```

Y define este helper **a nivel de módulo** en `backend/app/main.py`, fuera de `create_app` (justo debajo de `logger = logging.getLogger(__name__)`):

```python
async def _airdrop_cuenta(rpc_url: str, pubkey: str):
    """¿Existe esta cuenta en la cadena? Devuelve la cuenta o None.

    Vive AQUÍ y no dentro de `create_app` a propósito: un closure no se puede sustituir
    desde un test, y esta es la única llamada a la red de todo el airdrop. A nivel de
    módulo, `monkeypatch.setattr("app.main._airdrop_cuenta", ...)` funciona porque
    Python resuelve el global en el momento de la llamada.
    """
    return await leer_cuenta(rpc_url, pubkey)
```

Y dentro de `create_app`, junto a los demás endpoints:

```python
    _airdrop = cards_airdrop or {}

    def _airdrop_o_503() -> None:
        """Apagado y averiado se responden igual, con 503, y por la misma razón: en
        ninguno de los dos casos sabemos si el jugador es elegible."""
        if not (_airdrop and cards_airdrop_distributor and cards_airdrop_vault and cards_airdrop_mint):
            raise HTTPException(503, "airdrop_unavailable")
        if not (privy_operator_wallet_id and privy_operator_address):
            raise HTTPException(503, "airdrop_unavailable")
        if privy_signer is None:
            raise HTTPException(503, "airdrop_unavailable")

    async def _ya_reclamado(index: int) -> bool:
        pda, _ = claim_status_pda(index, cards_airdrop_distributor)
        try:
            return await _airdrop_cuenta(solana_rpc_url, str(pda)) is not None
        except Exception as exc:
            # Reintentable a propósito. Si el RPC no contesta no sabemos si reclamó, y
            # contestar "no" haría que le saliera el botón para reclamar dos veces.
            raise HTTPException(502, f"airdrop check failed: {exc}")

    @app.get("/users/me/airdrop/cards")
    async def me_airdrop_cards(wallet: str = Depends(current_user), s: Session = Depends(db)):
        _airdrop_o_503()
        e = _airdrop.get(wallet)
        if e is None:
            return {"eligible": False, "amount": 0, "claimed": False, "signature": None}
        reclamado = await _ya_reclamado(int(e["i"]))
        fila = s.query(AirdropClaim).filter(
            AirdropClaim.wallet == wallet, AirdropClaim.ronda == cards_airdrop_round
        ).first()
        return {"eligible": True, "amount": int(e["a"]), "claimed": reclamado,
                "signature": fila.signature if fila else None}
```

- [ ] **Step 5: Ejecutar los tests y verificar que pasan**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cards_airdrop_api.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/main.py backend/tests/test_cards_airdrop_api.py
git commit -m "feat(airdrop): consulta de elegibilidad, y la cadena como fuente de la verdad

Si está reclamado lo dice la PDA de ClaimStatus, no nuestra tabla: el jugador
puede haber reclamado en la web de CC sin pasar por aquí, y el saldo de su wallet
tampoco vale porque puede haber vendido.

Apagado y averiado responden lo mismo, 503, porque en los dos casos la verdad es
que no sabemos si es elegible. Lo que no puede pasar es que una avería nuestra le
diga 'no eres elegible' a alguien que sí lo es."
```

---

### Task 6: Endpoint del claim

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_cards_airdrop_api.py`

**Interfaces:**
- Consumes: `build_claim_tx`, `ata`, `_airdrop_o_503`, `_ya_reclamado` de las tasks anteriores; `fetch_latest_blockhash` y `submit_signed_tx`, ya presentes en `main.py`.
- Produces: endpoint `POST /users/me/airdrop/cards/claim` que devuelve `{"signature": str, "amount": int}`.

- [ ] **Step 1: Escribir los tests que fallan**

Añade al final de `backend/tests/test_cards_airdrop_api.py`:

```python
@pytest.fixture
def cadena_falsa(monkeypatch):
    """Sin red: blockhash fijo, la ATA no existe, y el submit devuelve una firma."""
    async def _bh(rpc_url):
        return "11111111111111111111111111111111"
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)

    enviadas = []

    async def _submit(rpc_url, tx_b64):
        enviadas.append(tx_b64)
        return "firma-de-mentira-1"
    monkeypatch.setattr("app.main.submit_signed_tx", _submit)
    return enviadas


def test_el_claim_firma_primero_el_jugador_y_luego_el_operador(sin_pda, cadena_falsa):
    c, priv, kw = _cliente()
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 200
    assert r.json() == {"signature": "firma-de-mentira-1", "amount": 1_483_000_000}
    # El orden importa: el dueño autoriza y el operador paga, nunca al revés.
    firmantes = [w for w, _ in kw["privy_signer"].firmas]
    assert firmantes == [WALLET_ID, "op-wallet-id"]


def test_el_claim_deja_constancia_en_la_tabla(sin_pda, cadena_falsa):
    from app.models import AirdropClaim
    c, priv, _ = _cliente()
    c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    r = c.get("/users/me/airdrop/cards", headers=_headers(priv))
    assert r.json()["signature"] == "firma-de-mentira-1"


def test_reclamar_dos_veces_da_409(cadena_falsa, monkeypatch):
    async def _cuenta(rpc_url, pubkey, **kw):
        return {"lamports": 1}
    monkeypatch.setattr("app.main._airdrop_cuenta", _cuenta)
    c, priv, _ = _cliente()
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 409


def test_un_no_elegible_no_puede_reclamar(sin_pda, cadena_falsa):
    c, priv, _ = _cliente()
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv, addr=AJENA, wallet_id="otro"))
    assert r.status_code == 403


def test_sin_operador_no_se_reclama(sin_pda, cadena_falsa):
    c, priv, _ = _cliente(privy_operator_wallet_id="", privy_operator_address="")
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 503


def test_sin_delegar_es_409_con_instrucciones_y_no_un_502_pelado(sin_pda, cadena_falsa):
    # Sin delegación no podemos firmar por él. Que se entere con el mensaje que ya usa el
    # juego, y no con un 502 que no le dice qué hacer.
    firmante = FakeSigner()

    async def _no(wallet_id):
        return False
    firmante.podemos_firmar = _no

    c, priv, _ = _cliente(privy_signer=firmante)
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 409


def test_si_otra_pestana_se_adelanta_sale_ya_reclamado(sin_pda, monkeypatch):
    # La PDA no existía al comprobar, pero para cuando llega la tx sí. La cadena responde
    # "already in use" y para el jugador eso NO es un fallo: sus tokens están en su sitio.
    async def _bh(rpc_url):
        return "11111111111111111111111111111111"
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _bh)

    async def _submit(rpc_url, tx_b64):
        raise RuntimeError("Allocate: account Address { ... } already in use")
    monkeypatch.setattr("app.main.submit_signed_tx", _submit)

    c, priv, _ = _cliente()
    r = c.post("/users/me/airdrop/cards/claim", headers=_headers(priv))
    assert r.status_code == 409
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cards_airdrop_api.py -v`
Expected: FAIL con 405 o 404 en los tests nuevos (la ruta no existe).

- [ ] **Step 3: Implementar el endpoint**

En `backend/app/main.py`, justo debajo de `me_airdrop_cards`:

```python
    @app.post("/users/me/airdrop/cards/claim")
    async def me_airdrop_cards_claim(wallet: str = Depends(current_user),
                                     wallet_id: str = Depends(current_user_id),
                                     s: Session = Depends(db)):
        """Reclama el airdrop del jugador. La wallet sale del identity token, así que
        nadie puede reclamar lo de otro ni aunque se invente el cuerpo de la petición.

        Dos firmas: el jugador autoriza como `temporal` y el operador paga. Es el mismo
        reparto que en /users/me/nft/withdraw.
        """
        _airdrop_o_503()
        # Antes que nada: sin delegación no podemos firmar por él, y más vale decírselo con
        # el mensaje que ya conoce del juego que dejarle chocar contra un 502 de Privy.
        await _exigir_delegacion(wallet_id)
        e = _airdrop.get(wallet)
        if e is None:
            raise HTTPException(403, "not eligible for this airdrop")
        index, amount = int(e["i"]), int(e["a"])
        if await _ya_reclamado(index):
            raise HTTPException(409, "already claimed")

        destino = ata(Pubkey.from_string(wallet), Pubkey.from_string(cards_airdrop_mint))
        try:
            crear_ata = await _airdrop_cuenta(solana_rpc_url, str(destino)) is None
        except Exception as exc:
            raise HTTPException(502, f"airdrop check failed: {exc}")

        blockhash = await fetch_latest_blockhash(solana_rpc_url)
        tx = build_claim_tx(
            claimant=wallet, index=index, amount=amount, proof=list(e["p"]),
            distributor=cards_airdrop_distributor, vault=cards_airdrop_vault,
            mint=cards_airdrop_mint, operador=privy_operator_address,
            blockhash=blockhash, crear_ata=crear_ata,
        )
        try:
            firmada = await privy_signer.sign_solana(wallet_id, tx)                 # el dueño autoriza
            firmada = await privy_signer.sign_solana(privy_operator_wallet_id, firmada)  # el operador paga
            sig = await submit_signed_tx(solana_rpc_url, firmada)
        except Exception as exc:
            # "already in use" = otra pestaña se adelantó y la PDA ya existe. Para el
            # jugador eso no es un fallo: sus tokens están donde tienen que estar.
            if "already in use" in str(exc):
                raise HTTPException(409, "already claimed")
            raise HTTPException(502, f"airdrop claim failed: {exc}")

        s.add(AirdropClaim(wallet=wallet, ronda=cards_airdrop_round, amount=amount, signature=sig))
        s.commit()
        logger.info("airdrop: %s reclamó %s unidades, sig=%s", wallet, amount, sig)
        return {"signature": sig, "amount": amount}
```

Añade `ata` al import del servicio y `Pubkey` si no está:

```python
from .services.cards_airdrop import ata, build_claim_tx, claim_status_pda
from solders.pubkey import Pubkey
```

- [ ] **Step 4: Ejecutar los tests y verificar que pasan**

Run: `cd backend && .venv/bin/python -m pytest tests/test_cards_airdrop.py tests/test_cards_airdrop_api.py -v`
Expected: PASS, 28 tests.

- [ ] **Step 5: Enganchar la configuración real al arranque**

Busca en `backend/app/main.py` (o donde se construya la app de producción) la llamada a `create_app` con los `settings`. Añade `cargar_asignaciones` al import del servicio:

```python
from .services.cards_airdrop import ata, build_claim_tx, cargar_asignaciones, claim_status_pda
```

Y pásale los cinco ajustes:

```python
        cards_airdrop=cargar_asignaciones(settings.cards_airdrop_file),
        cards_airdrop_distributor=settings.cards_airdrop_distributor,
        cards_airdrop_vault=settings.cards_airdrop_vault,
        cards_airdrop_mint=settings.cards_airdrop_mint,
        cards_airdrop_round=settings.cards_airdrop_round,
```

- [ ] **Step 6: Pasar la batería entera del backend**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS, sin regresiones.

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/tests/test_cards_airdrop_api.py
git commit -m "feat(airdrop): el claim, con el jugador autorizando y el operador pagando

La wallet sale del identity token y nunca del cuerpo de la petición, así que por
construcción nadie puede reclamar lo de otro.

Dos pestañas dando al botón a la vez acaban en 'already in use' en la cadena, y eso
se traduce a 409 'ya reclamado' en vez de a un error: para el jugador no ha fallado
nada, sus tokens están donde tienen que estar."
```

---

### Task 7: Cliente del frontend

**Files:**
- Create: `src/onchain/airdropClient.ts`
- Test: `src/onchain/airdropClient.test.ts`

**Interfaces:**
- Consumes: `config.backendUrl`.
- Produces: `fetchAirdrop(token: string): Promise<AirdropStatus>`, `claimAirdrop(token: string): Promise<AirdropClaimResult>`, tipos `AirdropStatus` y `AirdropClaimResult`, clase `AirdropError` con `kind`.

- [ ] **Step 1: Escribir el test que falla**

Crea `src/onchain/airdropClient.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fetchAirdrop, claimAirdrop, AirdropError } from './airdropClient'

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
const ko = (status: number) => ({ ok: false, status, json: async () => ({}) })

beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })

describe('airdropClient', () => {
  it('lee el estado del airdrop', async () => {
    vi.mocked(fetch).mockResolvedValue(
      ok({ eligible: true, amount: 1483000000, claimed: false, signature: null }) as never)
    const s = await fetchAirdrop('tok')
    expect(s).toEqual({ eligible: true, amount: 1483000000, claimed: false, signature: null })
  })

  it('manda el token en la cabecera', async () => {
    vi.mocked(fetch).mockResolvedValue(ok({ eligible: false, amount: 0, claimed: false, signature: null }) as never)
    await fetchAirdrop('tok-123')
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok-123')
  })

  it('traduce el 503 a unavailable y NO a no elegible', async () => {
    // La distinción es la del backend: no saber no es saber que no.
    vi.mocked(fetch).mockResolvedValue(ko(503) as never)
    await expect(fetchAirdrop('tok')).rejects.toMatchObject({ kind: 'unavailable' })
  })

  it('traduce el 409 a already_claimed', async () => {
    vi.mocked(fetch).mockResolvedValue(ko(409) as never)
    await expect(claimAirdrop('tok')).rejects.toMatchObject({ kind: 'already_claimed' })
  })

  it('traduce el 403 a not_eligible', async () => {
    vi.mocked(fetch).mockResolvedValue(ko(403) as never)
    await expect(claimAirdrop('tok')).rejects.toMatchObject({ kind: 'not_eligible' })
  })

  it('cualquier otro fallo es failed', async () => {
    vi.mocked(fetch).mockResolvedValue(ko(500) as never)
    await expect(claimAirdrop('tok')).rejects.toBeInstanceOf(AirdropError)
  })

  it('devuelve la firma al reclamar', async () => {
    vi.mocked(fetch).mockResolvedValue(ok({ signature: 'sig-1', amount: 1483000000 }) as never)
    expect(await claimAirdrop('tok')).toEqual({ signature: 'sig-1', amount: 1483000000 })
  })
})
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `npx vitest run src/onchain/airdropClient.test.ts`
Expected: FAIL, no se puede resolver `./airdropClient`.

- [ ] **Step 3: Implementar el cliente**

Crea `src/onchain/airdropClient.ts`:

```ts
// Cliente del claim del airdrop $CARDS. La wallet NUNCA viaja en la petición: el backend
// la saca del identity token, y eso es lo que impide reclamar lo de otro.
import { config } from './config'

export type AirdropErrorKind =
  | 'not_eligible'     // 403: esta wallet no está en la lista
  | 'already_claimed'  // 409: la PDA de ClaimStatus ya existe
  | 'unavailable'      // 503: airdrop apagado, sin operador o sin fichero
  | 'chain'            // 502: RPC o Privy no contestan. REINTENTABLE
  | 'failed'

export class AirdropError extends Error {
  kind: AirdropErrorKind

  constructor(kind: AirdropErrorKind) {
    super(kind)
    this.kind = kind
  }
}

export interface AirdropStatus {
  eligible: boolean
  amount: number          // unidades base de CARDS (6 decimales)
  claimed: boolean
  signature: string | null
}

export interface AirdropClaimResult {
  signature: string
  amount: number
}

const BY_STATUS: Record<number, AirdropErrorKind> = {
  403: 'not_eligible', 409: 'already_claimed', 503: 'unavailable', 502: 'chain',
}

function headers(token: string): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
    'ngrok-skip-browser-warning': 'true',
  }
}

async function pedir<T>(path: string, token: string, method: 'GET' | 'POST'): Promise<T> {
  const r = await fetch(`${config.backendUrl}${path}`, { method, headers: headers(token) })
  if (!r.ok) throw new AirdropError(BY_STATUS[r.status] ?? 'failed')
  return (await r.json()) as T
}

export function fetchAirdrop(token: string): Promise<AirdropStatus> {
  return pedir<AirdropStatus>('/users/me/airdrop/cards', token, 'GET')
}

export function claimAirdrop(token: string): Promise<AirdropClaimResult> {
  return pedir<AirdropClaimResult>('/users/me/airdrop/cards/claim', token, 'POST')
}
```

- [ ] **Step 4: Ejecutar los tests y verificar que pasan**

Run: `npx vitest run src/onchain/airdropClient.test.ts`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/onchain/airdropClient.ts src/onchain/airdropClient.test.ts
git commit -m "feat(airdrop): cliente del claim

La wallet no viaja en la petición: la saca el backend del identity token, y eso
es lo único que impide reclamar lo de otro.

El 503 se traduce a 'unavailable' y no a 'no elegible', que es la misma distinción
que hace el backend: si el airdrop está apagado o averiado no sabemos si el jugador
tiene derecho, y decirle que no sería mentirle."
```

---

### Task 8: Pantalla `/claim`

**Files:**
- Create: `src/ui/screens/Claim/ClaimScreen.tsx`
- Test: `src/ui/screens/Claim/ClaimScreen.test.tsx`
- Modify: `src/App.tsx`

**Interfaces:**
- Consumes: `fetchAirdrop`, `claimAirdrop`, `AirdropError` de la Task 7; `useIdentityToken` de Privy; `config.isDevnet`.
- Produces: componente `ClaimScreen` y la ruta `/claim` dentro de `AppShell`.

- [ ] **Step 1: Escribir el test que falla**

Crea `src/ui/screens/Claim/ClaimScreen.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const mocks = vi.hoisted(() => ({ fetchAirdrop: vi.fn(), claimAirdrop: vi.fn(), isDevnet: false }))
vi.mock('../../../onchain/airdropClient', async () => {
  const real = await vi.importActual<typeof import('../../../onchain/airdropClient')>(
    '../../../onchain/airdropClient')
  return { ...real, fetchAirdrop: mocks.fetchAirdrop, claimAirdrop: mocks.claimAirdrop }
})
vi.mock('../../../onchain/config', () => ({ config: { get isDevnet() { return mocks.isDevnet } } }))
vi.mock('@privy-io/react-auth', () => ({ useIdentityToken: () => ({ identityToken: 'tok' }) }))

import { ClaimScreen } from './ClaimScreen'

beforeEach(() => {
  mocks.fetchAirdrop.mockReset(); mocks.claimAirdrop.mockReset(); mocks.isDevnet = false
})

describe('ClaimScreen', () => {
  it('en devnet avisa y no llama al backend', async () => {
    mocks.isDevnet = true
    render(<ClaimScreen />)
    expect(screen.getByText(/only exists on mainnet/i)).toBeTruthy()
    expect(mocks.fetchAirdrop).not.toHaveBeenCalled()
  })

  it('enseña la cantidad cuando es elegible', async () => {
    mocks.fetchAirdrop.mockResolvedValue(
      { eligible: true, amount: 1483000000, claimed: false, signature: null })
    render(<ClaimScreen />)
    expect(await screen.findByText('1,483')).toBeTruthy()
    expect(screen.getByRole('button', { name: /claim/i })).toBeEnabled()
  })

  it('dice que no es elegible sin ofrecer botón', async () => {
    mocks.fetchAirdrop.mockResolvedValue({ eligible: false, amount: 0, claimed: false, signature: null })
    render(<ClaimScreen />)
    expect(await screen.findByText(/not eligible/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /claim/i })).toBeNull()
  })

  it('al reclamar enseña la firma', async () => {
    mocks.fetchAirdrop.mockResolvedValue(
      { eligible: true, amount: 1483000000, claimed: false, signature: null })
    mocks.claimAirdrop.mockResolvedValue({ signature: 'sig-abc', amount: 1483000000 })
    render(<ClaimScreen />)
    fireEvent.click(await screen.findByRole('button', { name: /claim/i }))
    await waitFor(() => expect(screen.getByText(/sig-abc/)).toBeTruthy())
  })

  it('si ya estaba reclamado no ofrece reclamar otra vez', async () => {
    mocks.fetchAirdrop.mockResolvedValue(
      { eligible: true, amount: 1483000000, claimed: true, signature: 'sig-vieja' })
    render(<ClaimScreen />)
    expect(await screen.findByText(/already claimed/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^claim/i })).toBeNull()
  })

  it('un fallo de cadena se lee como reintentable y no como no elegible', async () => {
    const { AirdropError } = await import('../../../onchain/airdropClient')
    mocks.fetchAirdrop.mockRejectedValue(new AirdropError('chain'))
    render(<ClaimScreen />)
    expect(await screen.findByText(/try again/i)).toBeTruthy()
    expect(screen.queryByText(/not eligible/i)).toBeNull()
  })
})
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `npx vitest run src/ui/screens/Claim/ClaimScreen.test.tsx`
Expected: FAIL, no se puede resolver `./ClaimScreen`.

- [ ] **Step 3: Implementar la pantalla**

Crea `src/ui/screens/Claim/ClaimScreen.tsx`:

```tsx
// Claim del airdrop $CARDS. Ruta enlazable desde fuera y fuera de la barra lateral a
// propósito: deja de tener sentido en cuanto CC cierre la bóveda, y una entrada muerta
// en el menú es peor que no tenerla.
import { useEffect, useState } from 'react'
import { useIdentityToken } from '@privy-io/react-auth'
import { config } from '../../../onchain/config'
import { fetchAirdrop, claimAirdrop, AirdropError } from '../../../onchain/airdropClient'
import type { AirdropStatus } from '../../../onchain/airdropClient'

const CARDS = (base: number): string => (base / 1_000_000).toLocaleString('en-US')

type Fase = 'cargando' | 'listo' | 'reclamando' | 'error'

export function ClaimScreen() {
  const { identityToken } = useIdentityToken()
  const [fase, setFase] = useState<Fase>('cargando')
  const [estado, setEstado] = useState<AirdropStatus | null>(null)
  const [firma, setFirma] = useState<string | null>(null)
  const [aviso, setAviso] = useState<string>('')

  useEffect(() => {
    if (config.isDevnet || !identityToken) return
    let vivo = true
    fetchAirdrop(identityToken)
      .then((s) => { if (vivo) { setEstado(s); setFirma(s.signature); setFase('listo') } })
      .catch((e) => {
        if (!vivo) return
        // Nunca "no eres elegible" por un fallo nuestro: eso se lo diría a gente que sí lo es.
        setAviso(e instanceof AirdropError && e.kind === 'unavailable'
          ? 'The airdrop claim is not available right now. Please try again later.'
          : 'Could not check your airdrop right now. Please try again in a moment.')
        setFase('error')
      })
    return () => { vivo = false }
  }, [identityToken])

  if (config.isDevnet) {
    return <p>The $CARDS airdrop only exists on mainnet.</p>
  }
  if (!identityToken) {
    return <p>Log in to check your $CARDS airdrop.</p>
  }
  if (fase === 'cargando') {
    return <p>Checking your airdrop…</p>
  }
  if (fase === 'error') {
    return <p>{aviso}</p>
  }
  if (!estado?.eligible) {
    return <p>This wallet is not eligible for the $CARDS airdrop.</p>
  }

  const yaEsta = estado.claimed || firma !== null

  async function reclamar() {
    if (!identityToken) return
    setFase('reclamando')
    try {
      const r = await claimAirdrop(identityToken)
      setFirma(r.signature)
      setEstado((s) => (s ? { ...s, claimed: true } : s))
      setFase('listo')
    } catch (e) {
      setAviso(e instanceof AirdropError && e.kind === 'already_claimed'
        ? 'You have already claimed your $CARDS airdrop.'
        : 'The claim could not be completed. Please try again in a moment.')
      setFase('error')
    }
  }

  return (
    <div>
      <h1>$CARDS airdrop</h1>
      <p>
        <strong>{CARDS(estado.amount)}</strong> $CARDS
      </p>
      {yaEsta ? (
        <>
          <p>Already claimed.</p>
          {firma && (
            <a href={`https://solscan.io/tx/${firma}`} target="_blank" rel="noopener noreferrer">
              {firma}
            </a>
          )}
        </>
      ) : (
        <button onClick={reclamar} disabled={fase === 'reclamando'}>
          {fase === 'reclamando' ? 'Claiming…' : 'Claim $CARDS'}
        </button>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Añadir la ruta**

En `src/App.tsx`, dentro del bloque `<Route element={<AppShell />}>`, junto a `/help`:

```tsx
          <Route path="/claim" element={<ClaimScreen />} />
```

Y su import arriba, con los demás:

```tsx
import { ClaimScreen } from './ui/screens/Claim/ClaimScreen'
```

- [ ] **Step 5: Ejecutar los tests y verificar que pasan**

Run: `npx vitest run src/ui/screens/Claim/ClaimScreen.test.tsx`
Expected: PASS, 6 tests.

- [ ] **Step 6: Pasar la batería entera y el linter**

Run: `npx vitest run && npx tsc --noEmit -p tsconfig.app.json && npx eslint src`
Expected: PASS, sin regresiones ni avisos nuevos.

- [ ] **Step 7: Commit**

```bash
git add src/ui/screens/Claim src/App.tsx
git commit -m "feat(airdrop): pantalla /claim

Ruta propia y fuera de la barra lateral: se puede enlazar desde un tuit y deja de
tener sentido en cuanto CC cierre la bóveda, así que una entrada fija en el menú
envejecería mal.

Un fallo de RPC o de Privy sale como reintentable y nunca como 'no eres elegible':
lo segundo sería mentirle a alguien que sí tiene derecho a sus tokens."
```

---

## Verificación final (necesita a Mauro)

Los tests no gastan SOL ni tocan mainnet, así que hasta aquí nada demuestra que un claim de verdad funcione. La simulación contra mainnet ya salió limpia, pero eso no es lo mismo que verlo mover tokens.

- [ ] **Configurar el operador en mainnet.** `backend/.env.mainnet` no tiene `PRIVY_OPERATOR_WALLET_ID` ni `PRIVY_OPERATOR_ADDRESS`; sin ellos los dos endpoints responden 503 por diseño. Comprobar también el `.env` real del mini PC, que puede no ser este fichero.
- [ ] **Comprobar el saldo del operador.** Hacen falta ~0,004 SOL por reclamante. `3q6Ucr1s7Knkp5nRQKQe3dYPzoh72XQGnn2oCgSS9S34` tenía 0,47 SOL en mainnet el 2026-09-08, de sobra.
- [ ] **Poner las cinco variables `CARDS_AIRDROP_*`** en el entorno de mainnet, con `CARDS_AIRDROP_FILE=data/cards_airdrop_2026-09.json`.
- [ ] **Un claim real** con una de las wallets elegibles conocidas, y comprobar en Solscan que los CARDS llegan a la ATA del jugador y que el SOL lo puso el operador.
- [ ] **Cruzar la db de producción de hoy** con el fichero para saber cuántos elegibles hay de verdad. La copia del 26 de agosto daba 3 de 12 usuarios, pero es vieja.

## Fuera de alcance

Wallets externas enlazadas, varias rondas a la vez, que pague el jugador y reclamar en lote desde el operador. Todo eso está argumentado en el spec.
