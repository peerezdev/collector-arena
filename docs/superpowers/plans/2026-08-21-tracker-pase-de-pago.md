# Pase de pago del Machine Tracker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el Machine Tracker se pueda desbloquear pagando un pase de 7 o 30 días en USDC, además de por el wager que ya existe, y cerrar los datos que hoy están abiertos.

**Architecture:** Una tabla nueva (`tracker_passes`) es la única parte con estado; el cálculo del wager no se toca. Un servicio puro decide precio y periodo, el endpoint de compra orquesta el cobro con el raíl de dos firmas que ya existe, y `/gacha/ev` pasa a exigir acceso.

**Tech Stack:** FastAPI + SQLAlchemy 2 (SQLite), React 19 + TypeScript, Privy (auth y firma), USDC en Solana.

**Spec:** `docs/superpowers/specs/2026-08-21-tracker-pase-de-pago-design.md`

## Global Constraints

- Python del venv del backend es **3.9**: nada de `X | None` en firmas, usar `Optional[X]`.
- Los scripts y los tests del backend se lanzan con `PYTHONPATH=.` desde `backend/`.
- El typecheck real del frontend es `npx tsc -b`. No basta con que pasen los tests.
- SQLite no tiene migraciones: columnas nuevas van en `_ENSURE_COLUMNS` de `app/db.py`. **Tablas** nuevas las crea `create_all`, no hay que hacer nada.
- Precio en configuración, **0 = apagado**. Ningún precio se escribe en el código.
- Nunca `git add -A`. Se añaden solo los ficheros de la tarea.
- Los mensajes de commit acaban con `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Nada de guiones largos en texto que ve el usuario final.
- Los secretos (`PRIVY_APP_SECRET`, `PRIVY_AUTH_KEY`) solo en `backend/.env`, jamás impresos.

## Estructura de ficheros

| Fichero | Responsabilidad |
|---|---|
| `backend/app/config.py` (mod) | Los dos precios, y el aviso si el de 30 sale peor por día |
| `backend/app/models.py` (mod) | La tabla `tracker_passes` |
| `backend/app/services/tracker_pass.py` (**nuevo**) | Lógica pura: precio por duración, periodo con apilado, pase vigente |
| `backend/app/services/tracker_access.py` (mod) | Un motivo más de acceso: `pase_hasta` |
| `backend/app/services/solana_tx.py` (mod) | `confirmar_firma`: saber si un cobro llegó de verdad |
| `backend/app/main.py` (mod) | `POST /gacha/tracker-pass`, cierre de `/gacha/ev` y `/gacha/ev/live` |
| `src/onchain/gachaClient.ts` (mod) | Token en las llamadas del tracker, y `buyTrackerPass` |
| `src/ui/screens/MachineTracker/PassOffer.tsx` (**nuevo**) | El bloque de compra dentro de la puerta |
| `src/ui/screens/MachineTracker/TrackerGate.tsx` (mod) | Monta `PassOffer` |
| `src/ui/screens/MachineTracker/MachineTrackerPage.tsx` (mod) | Manda el token, y un 403 enseña la puerta |

---

### Task 1: Los dos precios, en configuración

**Files:**
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_tracker_pass_config.py` (crear)

**Interfaces:**
- Produces: `Settings.tracker_pass_7d_usdc: float`, `Settings.tracker_pass_30d_usdc: float`, y `avisar_precios_raros(s7: float, s30: float) -> Optional[str]` en `app/config.py`.

- [ ] **Step 1: Escribir el test que falla**

Crear `backend/tests/test_tracker_pass_config.py`:

```python
"""Los precios del pase viven en configuración, no en el código.

Cero apaga la vía de pago entera, igual que `gacha_base_url` vacío apaga el gacha. Es lo que
permite desplegar esto sin haber decidido el precio: la compra simplemente no se ofrece.
"""
from app.config import Settings, avisar_precios_raros


def test_por_defecto_la_via_de_pago_esta_APAGADA():
    s = Settings()
    assert s.tracker_pass_7d_usdc == 0.0
    assert s.tracker_pass_30d_usdc == 0.0


def test_el_aviso_salta_cuando_el_pase_LARGO_sale_peor_por_dia():
    # 7 días a 10 son 1.43/día; 30 días a 50 son 1.67/día. Comprar el largo sería tirar dinero,
    # y eso solo lo descubre el cliente que eche la cuenta.
    assert avisar_precios_raros(10.0, 50.0) is not None


def test_sin_aviso_cuando_el_largo_sale_mejor():
    assert avisar_precios_raros(10.0, 30.0) is None


def test_sin_aviso_cuando_alguno_esta_apagado():
    # Con la vía apagada no hay nada que comparar, y avisar sería ruido en cada arranque.
    assert avisar_precios_raros(0.0, 0.0) is None
    assert avisar_precios_raros(10.0, 0.0) is None
    assert avisar_precios_raros(0.0, 30.0) is None


def test_el_mismo_precio_por_dia_no_avisa():
    # 7 a 7 y 30 a 30 son 1.0/día los dos. No es un chollo, pero no es un error.
    assert avisar_precios_raros(7.0, 30.0) is None
```

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_tracker_pass_config.py -q`
Expected: FAIL con `ImportError: cannot import name 'avisar_precios_raros'`

- [ ] **Step 3: Implementar**

En `backend/app/config.py`, dentro de la clase `Settings`, junto al resto de ajustes del tracker:

```python
    # Pase de pago del Machine Tracker, la vía alternativa al wager de 100 USDC.
    # CERO APAGA LA COMPRA, igual que `gacha_base_url` vacío apaga el gacha: así esto se puede
    # desplegar antes de haber decidido el precio, sin ofrecer nada a medias.
    tracker_pass_7d_usdc: float = 0.0      # env: TRACKER_PASS_7D_USDC
    tracker_pass_30d_usdc: float = 0.0     # env: TRACKER_PASS_30D_USDC
```

Y a nivel de módulo, al final del fichero:

```python
def avisar_precios_raros(s7: float, s30: float) -> Optional[str]:
    """Si el pase de 30 días sale MÁS CARO por día que el de 7, devuelve el aviso.

    No es un error que deba impedir arrancar: el precio es una decisión de negocio y quizá alguien
    lo quiere así por un tiempo. Pero es un fallo de configuración que, sin este aviso, solo
    descubre el cliente que eche la cuenta, y para entonces ya ha comprado el caro.
    """
    if s7 <= 0 or s30 <= 0:
        return None                       # con la vía apagada no hay nada que comparar
    if s30 / 30.0 > s7 / 7.0:
        return (f"TRACKER_PASS_30D_USDC ({s30}) sale a {s30 / 30:.3f}/día, más caro que "
                f"TRACKER_PASS_7D_USDC ({s7}) a {s7 / 7:.3f}/día")
    return None
```

Comprobar que `Optional` ya está importado en `config.py`; si no, añadir `from typing import Optional`.

- [ ] **Step 4: Ejecutar y ver que pasa**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_tracker_pass_config.py -q`
Expected: 5 passed

- [ ] **Step 5: Emitir el aviso al arrancar**

En `backend/app/main.py`, dentro de la función que construye la app a partir de los settings (donde ya se pasan `gacha_rate_limit=s.gacha_rate_limit` y compañía, alrededor de la línea 2659), justo antes de llamar a `create_app`:

```python
    _aviso = avisar_precios_raros(s.tracker_pass_7d_usdc, s.tracker_pass_30d_usdc)
    if _aviso:
        logger.warning("precios del pase del tracker: %s", _aviso)
```

Y añadir `avisar_precios_raros` al import que ya existe de `.config`.

- [ ] **Step 6: Suite entera y commit**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q`
Expected: todo verde

```bash
git add backend/app/config.py backend/app/main.py backend/tests/test_tracker_pass_config.py
git commit -m "$(cat <<'EOF'
feat(tracker): los precios del pase, en configuración y apagados por defecto

Cero apaga la compra entera, igual que `gacha_base_url` vacío apaga el gacha. Así esto se
puede desplegar antes de decidir el precio sin ofrecer nada a medias.

Y avisa al arrancar si el pase de 30 días sale más caro por día que el de 7. No bloquea:
el precio es una decisión de negocio. Pero sin el aviso ese error solo lo descubre el
cliente que eche la cuenta, y para entonces ya ha comprado el caro.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: La tabla `tracker_passes`

**Files:**
- Modify: `backend/app/models.py`
- Test: `backend/tests/test_tracker_pass_modelo.py` (crear)

**Interfaces:**
- Produces: clase `TrackerPass` en `app/models.py` con los campos `id`, `wallet`, `days`, `price_base_units`, `status`, `starts_at`, `ends_at`, `tx_signature`, `created_at`.

- [ ] **Step 1: Escribir el test que falla**

Crear `backend/tests/test_tracker_pass_modelo.py`:

```python
"""La tabla del pase. Es la ÚNICA parte con estado del acceso al tracker: la ventana del wager
se sigue recalculando en cada consulta y no guarda nada."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from app.db import init_db, make_session_factory
from app.models import TrackerPass

AHORA = datetime.now(timezone.utc)


def _sf():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    return make_session_factory(engine)


def test_la_tabla_se_crea_sola_y_guarda_un_pase():
    # Es tabla NUEVA, así que la crea `create_all`. No hace falta tocar `_ENSURE_COLUMNS`.
    sf = _sf()
    with sf() as s:
        s.add(TrackerPass(id="p1", wallet="W", days=7, price_base_units=10_000_000,
                          status="pending", starts_at=AHORA, ends_at=AHORA + timedelta(days=7)))
        s.commit()
    with sf() as s:
        p = s.scalars(select(TrackerPass).where(TrackerPass.wallet == "W")).one()
        assert p.days == 7
        assert p.status == "pending"
        assert p.tx_signature is None       # todavía no se ha cobrado


def test_la_firma_se_puede_guardar_antes_de_activar():
    # Es lo que hace reconciliable el hueco entre cobrar y activar: `pending` CON firma significa
    # exactamente "esto se cobró y no se activó".
    sf = _sf()
    with sf() as s:
        s.add(TrackerPass(id="p2", wallet="W", days=30, price_base_units=30_000_000,
                          status="pending", starts_at=AHORA, ends_at=AHORA + timedelta(days=30),
                          tx_signature="5xFirma"))
        s.commit()
    with sf() as s:
        p = s.get(TrackerPass, "p2")
        assert p.status == "pending" and p.tx_signature == "5xFirma"
```

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_tracker_pass_modelo.py -q`
Expected: FAIL con `ImportError: cannot import name 'TrackerPass'`

- [ ] **Step 3: Implementar**

En `backend/app/models.py`, al final del fichero:

```python
class TrackerPass(Base):
    """Un pase de pago del Machine Tracker.

    La ÚNICA parte con estado del acceso al tracker. La ventana del wager se recalcula en cada
    consulta y no guarda nada; ver `tracker_access`. Esa frontera importa: si un día el pase se
    complica, el wager no se entera.

    `status` va de `pending` a `active` o a `failed`, y SOLO `active` da acceso. Se inserta como
    `pending` ANTES de cobrar, así que un fallo del cobro no regala nada.
    """
    __tablename__ = "tracker_passes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    wallet: Mapped[str] = mapped_column(String, index=True)
    days: Mapped[int] = mapped_column(Integer)
    #: Lo cobrado, congelado. Si el precio cambia mañana, lo que se pagó no se reescribe.
    price_base_units: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="pending")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: Se guarda EN CUANTO se conoce, antes de tocar `status`. Un `pending` con firma es la señal
    #: inequívoca de "se cobró y no se activó", que es lo único que hace reconciliable ese hueco.
    tx_signature: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

Comprobar que `String`, `Integer`, `DateTime`, `Mapped`, `mapped_column`, `Optional` y `_now` ya están importados en `models.py` (lo están, los usa `User`).

- [ ] **Step 4: Añadir el índice compuesto**

En `backend/app/db.py`, en `_ENSURE_INDEXES`, añadir la consulta que hará la puerta en cada carga:

```python
    ("ix_tracker_passes_wallet_status_ends", "tracker_passes", "wallet, status, ends_at"),
```

Seguir el formato exacto de las entradas que ya haya en esa lista.

- [ ] **Step 5: Ejecutar y ver que pasa**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_tracker_pass_modelo.py -q`
Expected: 2 passed

- [ ] **Step 6: Suite entera y commit**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q`

```bash
git add backend/app/models.py backend/app/db.py backend/tests/test_tracker_pass_modelo.py
git commit -m "$(cat <<'EOF'
feat(tracker): la tabla del pase, con la firma guardable antes de activar

Es la única parte con estado del acceso al tracker: la ventana del wager se sigue
recalculando en cada consulta y no guarda nada. Esa frontera es deliberada.

`tx_signature` es nullable y se guarda ANTES de tocar `status` a propósito. Un `pending`
con firma significa exactamente "se cobró y no se activó", que es lo único que hace
reconciliable a mano el hueco entre el cobro y la activación.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: La lógica del pase, pura

**Files:**
- Create: `backend/app/services/tracker_pass.py`
- Test: `backend/tests/test_tracker_pass.py` (crear)

**Interfaces:**
- Consumes: `TrackerPass` de la tarea 2.
- Produces:
  - `precio_base_units(days: int, s7: float, s30: float) -> Optional[int]`
  - `pase_vigente(session: Session, wallet: str, ahora: Optional[datetime] = None) -> Optional[datetime]`
  - `periodo(session: Session, wallet: str, days: int, ahora: Optional[datetime] = None) -> Tuple[datetime, datetime]`
  - `DIAS_VALIDOS: Tuple[int, int] = (7, 30)`

- [ ] **Step 1: Escribir el test que falla**

Crear `backend/tests/test_tracker_pass.py`:

```python
"""Precio y periodo del pase del tracker, sin tocar red ni cobros."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.db import init_db, make_session_factory
from app.models import TrackerPass
from app.services.tracker_pass import (DIAS_VALIDOS, pase_vigente, periodo, precio_base_units)

AHORA = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def sf():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    return make_session_factory(engine)


def _pase(s, wallet, dias, *, status, desde, hasta):
    s.add(TrackerPass(id=f"{wallet}-{desde.isoformat()}-{status}", wallet=wallet, days=dias,
                      price_base_units=1, status=status, starts_at=desde, ends_at=hasta))
    s.commit()


# ── Precio ───────────────────────────────────────────────────────────────────

def test_el_precio_llega_en_unidades_BASE_no_en_dolares():
    # Todo el dinero de la aplicación se mueve en unidades base de 6 decimales. Devolver dólares
    # aquí obligaría a convertir en cada llamada, y la primera que se olvidara cobraría un millón.
    assert precio_base_units(7, 10.0, 30.0) == 10_000_000
    assert precio_base_units(30, 10.0, 30.0) == 30_000_000


def test_un_precio_a_CERO_significa_apagado_no_gratis():
    assert precio_base_units(7, 0.0, 30.0) is None
    assert precio_base_units(30, 10.0, 0.0) is None


def test_una_duracion_que_no_vendemos_no_tiene_precio():
    assert precio_base_units(1, 10.0, 30.0) is None
    assert precio_base_units(365, 10.0, 30.0) is None
    assert DIAS_VALIDOS == (7, 30)


def test_los_centimos_no_se_pierden_al_convertir():
    # 12.99 son 12_990_000 exactos. Con float mal redondeado saldría 12_989_999.
    assert precio_base_units(7, 12.99, 30.0) == 12_990_000


# ── Pase vigente ─────────────────────────────────────────────────────────────

def test_sin_pases_no_hay_nada_vigente(sf):
    with sf() as s:
        assert pase_vigente(s, "W", ahora=AHORA) is None


def test_un_pase_ACTIVO_y_en_fecha_vale(sf):
    with sf() as s:
        _pase(s, "W", 7, status="active", desde=AHORA - timedelta(days=1),
              hasta=AHORA + timedelta(days=6))
        assert pase_vigente(s, "W", ahora=AHORA) == AHORA + timedelta(days=6)


def test_un_pase_PENDIENTE_no_da_acceso(sf):
    # Es lo que hace que un cobro fallido no regale nada: la fila se escribe antes de cobrar.
    with sf() as s:
        _pase(s, "W", 7, status="pending", desde=AHORA, hasta=AHORA + timedelta(days=7))
        assert pase_vigente(s, "W", ahora=AHORA) is None


def test_un_pase_FALLIDO_tampoco(sf):
    with sf() as s:
        _pase(s, "W", 7, status="failed", desde=AHORA, hasta=AHORA + timedelta(days=7))
        assert pase_vigente(s, "W", ahora=AHORA) is None


def test_un_pase_caducado_deja_de_valer(sf):
    with sf() as s:
        _pase(s, "W", 7, status="active", desde=AHORA - timedelta(days=8),
              hasta=AHORA - timedelta(seconds=1))
        assert pase_vigente(s, "W", ahora=AHORA) is None


def test_con_varios_pases_manda_el_que_acaba_MAS_TARDE(sf):
    with sf() as s:
        _pase(s, "W", 7, status="active", desde=AHORA, hasta=AHORA + timedelta(days=7))
        _pase(s, "W", 30, status="active", desde=AHORA + timedelta(days=7),
              hasta=AHORA + timedelta(days=37))
        assert pase_vigente(s, "W", ahora=AHORA) == AHORA + timedelta(days=37)


def test_el_pase_de_OTRO_no_cuenta(sf):
    with sf() as s:
        _pase(s, "OTRO", 7, status="active", desde=AHORA, hasta=AHORA + timedelta(days=7))
        assert pase_vigente(s, "W", ahora=AHORA) is None


# ── Periodo, con apilado ─────────────────────────────────────────────────────

def test_sin_pase_previo_empieza_HOY(sf):
    with sf() as s:
        desde, hasta = periodo(s, "W", 7, ahora=AHORA)
        assert desde == AHORA
        assert hasta == AHORA + timedelta(days=7)


def test_comprando_con_pase_vigente_SUMA_AL_FINAL(sf):
    # Si comprar pronto quitara días, la gente aprendería a esperar a que caduque. Peor para
    # ellos y peor para nosotros.
    with sf() as s:
        _pase(s, "W", 7, status="active", desde=AHORA, hasta=AHORA + timedelta(days=5))
        desde, hasta = periodo(s, "W", 30, ahora=AHORA)
        assert desde == AHORA + timedelta(days=5)
        assert hasta == AHORA + timedelta(days=35)


def test_con_el_pase_ya_caducado_vuelve_a_empezar_hoy(sf):
    with sf() as s:
        _pase(s, "W", 7, status="active", desde=AHORA - timedelta(days=10),
              hasta=AHORA - timedelta(days=3))
        desde, _ = periodo(s, "W", 7, ahora=AHORA)
        assert desde == AHORA


def test_un_pendiente_NO_desplaza_la_compra_siguiente(sf):
    # Si lo desplazara, un cobro fallido dejaría al jugador con el pase corrido hacia adelante.
    with sf() as s:
        _pase(s, "W", 7, status="pending", desde=AHORA, hasta=AHORA + timedelta(days=7))
        desde, _ = periodo(s, "W", 7, ahora=AHORA)
        assert desde == AHORA
```

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_tracker_pass.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.services.tracker_pass'`

- [ ] **Step 3: Implementar**

Crear `backend/app/services/tracker_pass.py`:

```python
"""El pase de pago del Machine Tracker: cuánto cuesta y hasta cuándo vale.

Sin red, sin cobros y sin `datetime.now()` escondido: el `ahora` se puede pasar, que es lo que
hace estos tests fiables a cualquier hora. El cobro en sí vive en el endpoint, porque mezcla
firma, RPC y base de datos y no se puede probar sin dobles.

DOS REGLAS MANDAN AQUÍ, y las dos son decisiones, no detalles:

  · SOLO `active` DA ACCESO. Un `pending` es una compra a medias y un `failed` es una compra que
    no fue. Contar cualquiera de los dos regalaría el tracker a quien no pudo pagar.

  · COMPRAR ESTANDO DENTRO SUMA AL FINAL. Si comprar pronto quitara días, la gente aprendería a
    esperar a que el pase caduque, que es peor para todos.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TrackerPass

#: Las duraciones que vendemos. Cualquier otra no tiene precio y la compra se rechaza.
DIAS_VALIDOS: Tuple[int, int] = (7, 30)

USDC = 1_000_000


def precio_base_units(days: int, s7: float, s30: float) -> Optional[int]:
    """Lo que cuesta esa duración, en unidades base de USDC. `None` si no se vende.

    Devuelve unidades base y no dólares porque es como se mueve todo el dinero de la aplicación;
    convertir en cada llamada acabaría con alguien olvidándolo y cobrando un millón.

    Cero es APAGADO, no gratis: es el interruptor que permite desplegar sin precio decidido.
    """
    if days not in DIAS_VALIDOS:
        return None
    precio = s7 if days == 7 else s30
    if precio <= 0:
        return None
    # `round` y no `int`: 12.99 * 1e6 en coma flotante es 12989999.999..., y truncar perdería un
    # céntimo en cada compra con decimales.
    return int(round(precio * USDC))


def _con_zona(d: Optional[datetime]) -> Optional[datetime]:
    """SQLite devuelve fechas SIN zona aunque se guarden con ella. Sin esto, compararlas con un
    `ahora` con zona lanza TypeError, y solo en producción."""
    if d is None:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=timezone.utc)


def pase_vigente(session: Session, wallet: str, ahora: Optional[datetime] = None) -> Optional[datetime]:
    """Hasta cuándo tiene pase esa wallet, o `None` si no tiene ninguno vigente."""
    ahora = ahora or datetime.now(timezone.utc)
    filas = session.scalars(
        select(TrackerPass).where(TrackerPass.wallet == wallet, TrackerPass.status == "active")
    ).all()
    fines = [f for f in (_con_zona(x.ends_at) for x in filas) if f is not None and f > ahora]
    return max(fines) if fines else None


def periodo(session: Session, wallet: str, days: int,
            ahora: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """Desde y hasta cuándo valdría un pase que se comprara AHORA.

    Si ya hay uno vigente, el nuevo empieza donde acaba aquel. El wager NO entra en esta cuenta:
    es una ventana rodante que cambia sola, así que descontarlo obligaría a adivinar cuánto va a
    durar el acceso que ya tienes.
    """
    ahora = ahora or datetime.now(timezone.utc)
    desde = pase_vigente(session, wallet, ahora=ahora) or ahora
    return desde, desde + timedelta(days=days)
```

- [ ] **Step 4: Ejecutar y ver que pasa**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_tracker_pass.py -q`
Expected: 15 passed

- [ ] **Step 5: Mutar para comprobar que los tests sujetan**

Ejecutar estas tres mutaciones a mano; cada una **tiene que** poner rojo `tests/test_tracker_pass.py`. Si alguna pasa, el test correspondiente no prueba nada y hay que arreglarlo antes de seguir:

1. En `pase_vigente`, quitar `TrackerPass.status == "active"` del `where` → deben fallar los tests de `pending` y `failed`.
2. En `periodo`, cambiar `pase_vigente(...) or ahora` por `ahora` → debe fallar el test del apilado.
3. En `precio_base_units`, cambiar `int(round(precio * USDC))` por `int(precio * USDC)` → debe fallar el test de los céntimos.

Restaurar el fichero después de cada una.

- [ ] **Step 6: Suite entera y commit**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q`

```bash
git add backend/app/services/tracker_pass.py backend/tests/test_tracker_pass.py
git commit -m "$(cat <<'EOF'
feat(tracker): precio y periodo del pase, sin red y sin relojes escondidos

Dos reglas mandan y las dos son decisiones. Solo un pase `active` da acceso, así que un
cobro fallido no regala nada. Y comprar estando dentro suma al final en vez de empezar
hoy: si comprar pronto quitara días, la gente aprendería a esperar a que caduque.

El precio sale en unidades base porque es como se mueve todo el dinero de la aplicación.
Se redondea al convertir: 12.99 por un millón en coma flotante es 12989999.999, y truncar
perdía un céntimo en cada compra.

Y las fechas de SQLite se le ponen zona al leerlas, que las devuelve sin ella aunque se
guarden con zona. Sin eso la comparación revienta solo en producción.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: La puerta pasa a tener tres motivos

**Files:**
- Modify: `backend/app/services/tracker_access.py`
- Modify: `backend/app/main.py` (endpoint `/gacha/tracker-access`, línea ~1030)
- Test: `backend/tests/test_tracker_access.py` (existe; añadir al final)

**Interfaces:**
- Consumes: `pase_vigente` de la tarea 3.
- Produces: `acceso(..., pase_hasta: Optional[datetime] = None)` devuelve además `via: Optional[str]`, `pass_until: Optional[int]` y `pass_prices: dict`.

- [ ] **Step 1: Escribir el test que falla**

Añadir al final de `backend/tests/test_tracker_access.py` (usar el `AHORA` y los ayudantes que ya haya en ese fichero; si no existen, replicar el patrón del resto de tests del fichero):

```python
# ── El pase, la segunda vía de acceso ────────────────────────────────────────

def test_un_pase_vigente_abre_la_puerta_sin_haber_apostado(Session):
    with Session() as s:
        r = acceso(s, "W", pase_hasta=AHORA + timedelta(days=3), ahora=AHORA)
        assert r["allowed"] is True
        assert r["via"] == "pass"
        assert r["pass_until"] == int((AHORA + timedelta(days=3)).timestamp())


def test_el_pase_NO_falsea_lo_apostado(Session):
    # Por lo mismo que no lo falsea la lista blanca: esa cifra también se enseña, y mentirla
    # haría mentirosa a la pantalla entera.
    with Session() as s:
        r = acceso(s, "W", pase_hasta=AHORA + timedelta(days=3), ahora=AHORA)
        assert r["wagered_usd"] == 0.0
        assert r["missing_usd"] == 100.0


def test_sin_pase_y_sin_wager_sigue_cerrada(Session):
    with Session() as s:
        r = acceso(s, "W", pase_hasta=None, ahora=AHORA)
        assert r["allowed"] is False
        assert r["via"] is None


def test_el_orden_de_los_motivos_es_casa_pase_wager(Session):
    # `via` tiene que decir el motivo REAL, del más fuerte al más débil, para poder explicarlo
    # en pantalla y para poder depurar por qué alguien entra.
    with Session() as s:
        r = acceso(s, "W", pase_hasta=AHORA + timedelta(days=3), lista_blanca={"W"}, ahora=AHORA)
        assert r["via"] == "house"


def test_un_pase_caducado_no_abre_nada(Session):
    # `pase_hasta` en el pasado lo resuelve `pase_vigente`, pero la puerta no puede fiarse.
    with Session() as s:
        r = acceso(s, "W", pase_hasta=AHORA - timedelta(seconds=1), ahora=AHORA)
        assert r["allowed"] is False
        assert r["via"] is None


def test_los_precios_viajan_en_la_respuesta_para_poder_pintarlos(Session):
    with Session() as s:
        r = acceso(s, "W", precios={"7": 10.0, "30": 30.0}, ahora=AHORA)
        assert r["pass_prices"] == {"7": 10.0, "30": 30.0}


def test_sin_precios_configurados_el_bloque_de_compra_no_existe(Session):
    # Diccionario vacío y no ceros: la pantalla no tiene que saber que cero significa apagado.
    with Session() as s:
        assert acceso(s, "W", ahora=AHORA)["pass_prices"] == {}
```

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_tracker_access.py -q`
Expected: FAIL con `TypeError: acceso() got an unexpected keyword argument 'pase_hasta'`

- [ ] **Step 3: Implementar**

En `backend/app/services/tracker_access.py`, cambiar la firma y el cuerpo de `acceso`:

```python
def acceso(session: Session, wallet: Optional[str], *, minimo_usd: float = MINIMO_USD,
           dias: int = VENTANA_DIAS, ahora: Optional[datetime] = None,
           lista_blanca: Optional[set[str]] = None,
           pase_hasta: Optional[datetime] = None,
           precios: Optional[dict] = None) -> dict:
```

Y dentro, tras calcular `invitada`, sustituir el cálculo de `falta` y el `return` por:

```python
    ahora_dt = ahora or datetime.now(timezone.utc)
    con_pase = bool(pase_hasta and pase_hasta > ahora_dt)
    por_wager = apostado >= minimo_usd

    # De más fuerte a más débil, para que `via` diga el motivo REAL. Sin este orden, alguien con
    # pase Y wager saldría como "wager" y nadie entendería por qué sigue dentro al caducar.
    via = "house" if invitada else "pass" if con_pase else "wager" if por_wager else None

    falta = 0.0 if (invitada or con_pase) else max(0.0, minimo_usd - apostado)
    return {
        "allowed": via is not None,
        "via": via,
        "pass_until": int(pase_hasta.timestamp()) if con_pase else None,
        "wagered_usd": round(apostado, 2),
        "required_usd": minimo_usd,
        "missing_usd": math.ceil(falta * 100) / 100,
        "window_days": dias,
        "pass_prices": dict(precios or {}),
    }
```

Conservar el resto del `return` tal como esté hoy si difiere en algún campo; lo que se añade son `via`, `pass_until` y `pass_prices`.

Ampliar además la docstring de `acceso` explicando la tercera vía, siguiendo el tono del resto del módulo.

- [ ] **Step 4: Conectar el endpoint**

En `backend/app/main.py`, en `/gacha/tracker-access` (línea ~1038), sustituir la llamada:

```python
        pase = tracker_pass.pase_vigente(s, wallet) if wallet else None
        precios = {}
        if tracker_pass.precio_base_units(7, tracker_pass_7d_usdc, tracker_pass_30d_usdc):
            precios["7"] = tracker_pass_7d_usdc
        if tracker_pass.precio_base_units(30, tracker_pass_7d_usdc, tracker_pass_30d_usdc):
            precios["30"] = tracker_pass_30d_usdc
        return tracker_access.acceso(s, wallet, lista_blanca=_tracker_allow,
                                     pase_hasta=pase, precios=precios)
```

Añadir `tracker_pass` al import de `.services` que ya existe, y los dos parámetros `tracker_pass_7d_usdc: float = 0.0` y `tracker_pass_30d_usdc: float = 0.0` a la firma de `create_app`, cableándolos desde los settings donde se cablea `gacha_rate_limit`.

- [ ] **Step 5: Ejecutar y ver que pasa**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_tracker_access.py -q`
Expected: todos verdes, incluidos los 7 nuevos

- [ ] **Step 6: Suite entera y commit**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q`

```bash
git add backend/app/services/tracker_access.py backend/app/main.py backend/tests/test_tracker_access.py
git commit -m "$(cat <<'EOF'
feat(tracker): la puerta pasa a tener tres motivos, y dice cuál

Al wager y a la lista blanca de la casa se suma el pase de pago. `via` dice el motivo real,
resuelto de más fuerte a más débil (casa, pase, wager): sin ese orden, alguien con pase Y
wager saldría como "wager" y nadie entendería por qué sigue dentro cuando caduque.

El pase NO falsea `wagered_usd`, por lo mismo que no lo falsea la lista blanca: esa cifra
se enseña y mentirla haría mentirosa a la pantalla entera.

Y los precios viajan en la respuesta como diccionario, vacío cuando la compra está apagada.
Así la pantalla no tiene que saber que cero significa apagado.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Saber si un cobro llegó de verdad

**Files:**
- Modify: `backend/app/services/solana_tx.py`
- Test: `backend/tests/test_confirmar_firma.py` (crear)

**Interfaces:**
- Produces: `async def confirmar_firma(rpc_url: str, firma: str, *, intentos: int = 10, espera_s: float = 1.5) -> bool`

**Por qué existe:** `submit_signed_tx` solo **envía**; devuelve la firma sin esperar confirmación. Activar el pase sobre un "enviado" que luego falla regalaría el acceso.

- [ ] **Step 1: Escribir el test que falla**

Crear `backend/tests/test_confirmar_firma.py`:

```python
"""Que un cobro se haya ENVIADO no es que haya llegado.

`submit_signed_tx` hace `sendTransaction` y devuelve la firma sin esperar nada. Activar un pase
sobre eso regalaría el acceso cada vez que una transacción se cae después de enviarse.
"""
import pytest

from app.services.solana_tx import confirmar_firma


class _RpcFalso:
    """Responde a `getSignatureStatuses` con lo que se le diga, un valor por llamada."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = 0

    async def post(self, url, json=None, timeout=None):
        self.llamadas += 1
        valor = self.respuestas.pop(0) if self.respuestas else None
        return _Resp({"jsonrpc": "2.0", "id": 1, "result": {"value": [valor]}})


class _Resp:
    def __init__(self, cuerpo):
        self._cuerpo = cuerpo

    def raise_for_status(self):
        return None

    def json(self):
        return self._cuerpo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture()
def cliente(monkeypatch):
    creado = {}

    def _fabrica(*a, **k):
        return _Ctx(creado["rpc"])

    class _Ctx:
        def __init__(self, rpc):
            self.rpc = rpc

        async def __aenter__(self):
            return self.rpc

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("app.services.solana_tx.httpx.AsyncClient", _fabrica)
    return creado


@pytest.mark.asyncio
async def test_confirmada_a_la_primera(cliente, monkeypatch):
    cliente["rpc"] = _RpcFalso([{"confirmationStatus": "confirmed", "err": None}])
    assert await confirmar_firma("http://rpc", "5xFirma") is True


@pytest.mark.asyncio
async def test_una_transaccion_con_ERROR_no_esta_confirmada(cliente):
    # El caso que más importa: la cadena la aceptó y la ejecutó MAL. Si esto devolviera True,
    # activaríamos un pase que nadie pagó.
    cliente["rpc"] = _RpcFalso([{"confirmationStatus": "confirmed", "err": {"InstructionError": 1}}])
    assert await confirmar_firma("http://rpc", "5xFirma") is False


@pytest.mark.asyncio
async def test_espera_mientras_el_RPC_todavia_no_sabe(cliente):
    # `null` es "no la conozco todavía", que es lo normal en los primeros instantes.
    cliente["rpc"] = _RpcFalso([None, None, {"confirmationStatus": "finalized", "err": None}])
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=5, espera_s=0) is True
    assert cliente["rpc"].llamadas == 3


@pytest.mark.asyncio
async def test_si_nunca_se_confirma_devuelve_False_y_NO_revienta(cliente):
    cliente["rpc"] = _RpcFalso([None] * 10)
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=3, espera_s=0) is False
    assert cliente["rpc"].llamadas == 3      # no sondea más de lo que se le pidió


@pytest.mark.asyncio
async def test_processed_no_basta(cliente):
    # `processed` puede revertirse. Solo valen `confirmed` y `finalized`.
    cliente["rpc"] = _RpcFalso([{"confirmationStatus": "processed", "err": None}] * 3)
    assert await confirmar_firma("http://rpc", "5xFirma", intentos=3, espera_s=0) is False
```

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_confirmar_firma.py -q`
Expected: FAIL con `ImportError: cannot import name 'confirmar_firma'`

- [ ] **Step 3: Implementar**

En `backend/app/services/solana_tx.py`, al final del fichero (comprobar que `httpx` y `asyncio` están importados; añadirlos si no):

```python
async def confirmar_firma(rpc_url: str, firma: str, *, intentos: int = 10,
                          espera_s: float = 1.5) -> bool:
    """Si esa transacción llegó a la cadena y salió BIEN.

    Existe porque `submit_signed_tx` solo envía: devuelve la firma sin esperar nada. Dar por
    cobrado un envío es regalar el pase cada vez que una transacción se cae después de salir.

    Dos cosas que parecen detalles y no lo son:

      · Una transacción CONFIRMADA CON ERROR no cuenta. La cadena la aceptó y la ejecutó mal, así
        que el dinero no se movió. Mirar solo `confirmationStatus` daría por bueno un cobro que
        no ocurrió.
      · `processed` no basta: puede revertirse. Solo valen `confirmed` y `finalized`.

    Un fallo de red se trata como "todavía no", nunca como confirmada. Ante la duda, no se activa
    el pase: el jugador reintenta, que es recuperable, en vez de que nosotros regalemos acceso.
    """
    for intento in range(intentos):
        if intento:
            await asyncio.sleep(espera_s)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(rpc_url, json={"jsonrpc": "2.0", "id": 1,
                                                "method": "getSignatureStatuses",
                                                "params": [[firma], {"searchTransactionHistory": True}]},
                                 timeout=20)
                r.raise_for_status()
                d = r.json()
        except httpx.HTTPError:
            continue
        valor = ((d.get("result") or {}).get("value") or [None])[0]
        if not valor:
            continue
        if valor.get("err"):
            return False            # se ejecutó y falló: el dinero NO se movió
        if valor.get("confirmationStatus") in ("confirmed", "finalized"):
            return True
    return False
```

- [ ] **Step 4: Ejecutar y ver que pasa**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_confirmar_firma.py -q`
Expected: 5 passed

- [ ] **Step 5: Suite entera y commit**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q`

```bash
git add backend/app/services/solana_tx.py backend/tests/test_confirmar_firma.py
git commit -m "$(cat <<'EOF'
feat(solana): confirmar_firma, porque enviar no es cobrar

`submit_signed_tx` hace sendTransaction y devuelve la firma sin esperar nada. Dar por
cobrado un envío regalaría el pase cada vez que una transacción se cae después de salir.

Dos cosas que parecen detalles: una transacción confirmada CON error no cuenta, porque el
dinero no se movió, y `processed` no basta porque puede revertirse.

Un fallo de red se trata como "todavía no", nunca como confirmada. Ante la duda no se
activa: el jugador reintenta, que es recuperable, en vez de regalar acceso.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: La compra

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_tracker_pass_api.py` (crear)

**Interfaces:**
- Consumes: `precio_base_units`, `periodo`, `pase_vigente` (tarea 3); `confirmar_firma` (tarea 5); `TrackerPass` (tarea 2); `_require_available`, `current_user`, `current_user_id`, `collect_buyin` (ya existen).
- Produces: `POST /gacha/tracker-pass`, cuerpo `{"days": 7 | 30}`, respuesta `{"pass_until": int, "days": int, "price_usdc": float}`.

- [ ] **Step 1: Escribir el test que falla**

Crear `backend/tests/test_tracker_pass_api.py`:

```python
"""La compra del pase. Aquí se mueve dinero real de un usuario por primera vez fuera de una
partida, así que lo que más se prueba es el camino de FALLO."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from app.db import init_db, make_session_factory
from app.main import create_app
from app.models import TrackerPass
from app.services.gacha import GachaService
from tests.test_chain_mock import MockChainSource

WALLET = "8QDBKx8P3pxkRhiqyXFtYcPPf2CM1F5NiE5A8yjkgtm6"


# Las fixtures (`entorno`, `client`, `cobro_ok`, `sin_saldo`, `saldo_reservado`,
# `cobro_revienta`, `cobro_sin_confirmar`) están en el paso 3 de esta tarea. Van AQUÍ, encima de
# los tests. Lo que no se puede cambiar es qué afirma cada test.


def test_comprar_da_acceso_y_lo_dice(client, cobro_ok):
    r = client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 7
    assert r.json()["price_usdc"] == 10.0
    acc = client.get("/gacha/tracker-access", headers=client.hdrs).json()
    assert acc["allowed"] is True and acc["via"] == "pass"


def test_el_pase_queda_ACTIVO_y_con_su_firma(client, cobro_ok):
    client.post("/gacha/tracker-pass", json={"days": 30}, headers=client.hdrs)
    with client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "active"
        assert p.tx_signature
        assert p.price_base_units == 30_000_000


def test_sin_saldo_NO_se_crea_ninguna_fila(client, sin_saldo):
    # El 402 ocurre antes de escribir nada: ni fila, ni cobro, ni rastro.
    r = client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    assert r.status_code == 402
    with client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []


def test_el_saldo_RESERVADO_para_una_batalla_no_se_puede_gastar_en_un_pase(client, saldo_reservado):
    # Si un pase pudiera gastarlo, la batalla se quedaría sin fondos al liquidar.
    r = client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    assert r.status_code == 402


def test_un_cobro_que_FALLA_no_da_acceso_y_deja_la_fila_en_failed(client, cobro_revienta):
    r = client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    assert r.status_code == 502
    with client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).one().status == "failed"
    acc = client.get("/gacha/tracker-access", headers=client.hdrs).json()
    assert acc["allowed"] is False


def test_un_cobro_ENVIADO_pero_NO_confirmado_tampoco_da_acceso(client, cobro_sin_confirmar):
    # El caso que justifica `confirmar_firma`: la transacción salió y se cayó después.
    r = client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    assert r.status_code == 502
    with client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "failed"
        assert p.tx_signature, "la firma se guarda igual, para poder reconciliar a mano"


def test_comprar_dos_veces_APILA(client, cobro_ok):
    client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    client.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    with client.session_factory() as s:
        pases = sorted(s.scalars(select(TrackerPass)).all(), key=lambda p: p.starts_at)
        assert pases[1].starts_at == pases[0].ends_at


def test_sin_token_no_se_puede_comprar(client):
    assert client.post("/gacha/tracker-pass", json={"days": 7}).status_code == 401


def test_una_duracion_que_no_vendemos_se_rechaza(client, cobro_ok):
    r = client.post("/gacha/tracker-pass", json={"days": 1}, headers=client.hdrs)
    assert r.status_code == 422


def test_con_el_precio_APAGADO_la_compra_no_existe(monkeypatch, cobro_ok):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    app = create_app(make_session_factory(engine), MockChainSource(),
                     gacha=GachaService(base_url="https://dev-gacha.example.com", api_key=""),
                     solana_rpc_url="https://api.devnet.solana.com")   # sin precios
    c = TestClient(app)
    r = c.post("/gacha/tracker-pass", json={"days": 7}, headers=client.hdrs)
    assert r.status_code == 503
```

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_tracker_pass_api.py -q`
Expected: FAIL (404 en el endpoint, y fixtures sin definir)

- [ ] **Step 3: Escribir las fixtures de los dobles**

Sustituir el bloque de comentario del fichero por esto, que va DEBAJO de la fixture `client` y
encima de los tests. Sigue el patrón de autenticación de `tests/test_gacha_api.py`:

```python
from tests.conftest import make_es256, make_id_token
from app.privy import PrivyVerifier

APP_ID = "app-test"
WALLET_ID = "wid-1"


class _FirmanteFalso:
    """No firma nada: el cobro se intercepta antes de llegar aquí."""
    enabled = True


@pytest.fixture()
def entorno(monkeypatch):
    """App con la compra ENCENDIDA y el dinero intervenido.

    `mando` controla los cuatro puntos donde esto puede salir mal, y cada fixture de abajo mueve
    uno solo. Así cada test dice exactamente qué falla.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    init_db(engine)
    sf = make_session_factory(engine)
    priv = make_es256()
    app = create_app(sf, MockChainSource(),
                     gacha=GachaService(base_url="https://dev-gacha.example.com", api_key=""),
                     privy=PrivyVerifier(app_id=APP_ID, key_resolver=lambda kid: priv.public_key()),
                     privy_signer=_FirmanteFalso(),
                     privy_operator_wallet_id="op-id", privy_operator_address="OpAddr",
                     solana_rpc_url="https://rpc.test",
                     tracker_pass_7d_usdc=10.0, tracker_pass_30d_usdc=30.0)

    mando = {"saldo": 1_000_000_000, "reservado": 0, "cobro": "FirmaFalsa", "confirma": True}

    async def _saldo(*a, **k):
        return mando["saldo"]

    def _reservado(*a, **k):
        return mando["reservado"]

    async def _blockhash(*a, **k):
        return "11111111111111111111111111111111"

    async def _cobro(*a, **k):
        if isinstance(mando["cobro"], Exception):
            raise mando["cobro"]
        return mando["cobro"]

    async def _confirma(*a, **k):
        return mando["confirma"]

    monkeypatch.setattr("app.main.usdc_balance_base_units", _saldo)
    monkeypatch.setattr("app.main.reserved_total", _reservado)
    monkeypatch.setattr("app.main.fetch_latest_blockhash", _blockhash)
    monkeypatch.setattr("app.main.collect_buyin", _cobro)
    monkeypatch.setattr("app.main.confirmar_firma", _confirma)

    cuenta = {"type": "wallet", "chain_type": "solana", "connector_type": None,
              "wallet_client_type": "privy", "address": WALLET, "id": WALLET_ID}
    c = TestClient(app, raise_server_exceptions=True)
    c.session_factory = sf
    c.hdrs = {"Authorization": f"Bearer {make_id_token(priv, APP_ID, [cuenta])}"}
    c.mando = mando
    return c


@pytest.fixture()
def client(entorno):
    return entorno


@pytest.fixture()
def cobro_ok(entorno):
    return entorno            # el estado por defecto ya es el camino bueno


@pytest.fixture()
def sin_saldo(entorno):
    entorno.mando["saldo"] = 1_000_000          # 1 USDC, y el pase cuesta 10
    return entorno


@pytest.fixture()
def saldo_reservado(entorno):
    # Tiene 20 USDC pero 15 están comprometidos en una batalla: disponibles quedan 5.
    entorno.mando["saldo"] = 20_000_000
    entorno.mando["reservado"] = 15_000_000
    return entorno


@pytest.fixture()
def cobro_revienta(entorno):
    entorno.mando["cobro"] = RuntimeError("sendTransaction failed")
    return entorno


@pytest.fixture()
def cobro_sin_confirmar(entorno):
    entorno.mando["confirma"] = False           # salió, y se cayó después
    return entorno
```

El test del precio apagado (`test_con_el_precio_APAGADO_la_compra_no_existe`) construye su propia
app copiando el cuerpo de `entorno` y quitando `tracker_pass_7d_usdc` y `tracker_pass_30d_usdc`,
porque necesita justamente lo contrario que la fixture.

- [ ] **Step 4: Implementar el endpoint**

En `backend/app/main.py`, junto a `/gacha/tracker-access`:

```python
    class TrackerPassBody(BaseModel):
        days: int

    @app.post("/gacha/tracker-pass")
    async def gacha_tracker_pass(body: TrackerPassBody,
                                 wallet: str = Depends(current_user),
                                 wallet_id: str = Depends(current_user_id),
                                 s: Session = Depends(db)):
        """Comprar un pase del Machine Tracker.

        EL ORDEN ES LO IMPORTANTE, porque aquí se mueve dinero real de un usuario y las dos cosas
        que tienen que pasar (cobrar en la cadena, anotar en nuestra base) no pueden ser atómicas.

          0. Saldo DISPONIBLE, que es el on-chain menos lo reservado. Sin esto, un pase podría
             gastarse el dinero comprometido en una batalla y dejarla sin fondos al liquidar.
          1. Fila en `pending`, que NO da acceso. Deja constancia de quién compra y cuánto antes
             de tocar el dinero: si el proceso muere después, sabemos a quién mirar.
          2. Cobro, y la firma se guarda EN CUANTO se conoce.
          3. Confirmación de que llegó, y solo entonces `active`.

        Al revés (activar y cobrar después) regala el acceso cuando el cobro falla, y ese fallo lo
        provoca cualquiera con la cuenta vacía.

        Queda un hueco: cobrado y confirmado, y morirse antes del paso 3. Por eso la firma se
        guarda antes: un `pending` CON firma significa exactamente "se cobró y no se activó", que
        es una consulta de una línea y un arreglo a mano. No hay reversión automática de una
        transferencia on-chain, y un camino de devolución que no se ejecuta casi nunca estaría
        roto el día que hiciera falta.
        """
        if body.days not in tracker_pass.DIAS_VALIDOS:
            raise HTTPException(422, "invalid pass duration")
        precio = tracker_pass.precio_base_units(body.days, tracker_pass_7d_usdc,
                                                tracker_pass_30d_usdc)
        if precio is None:
            raise HTTPException(503, "tracker_pass_disabled")

        await _require_available(wallet, precio, s)          # 402 si no llega. Nada escrito aún.

        desde, hasta = tracker_pass.periodo(s, wallet, body.days)
        fila = TrackerPass(id=str(uuid.uuid4()), wallet=wallet, days=body.days,
                           price_base_units=precio, status="pending",
                           starts_at=desde, ends_at=hasta)
        s.add(fila)
        s.commit()

        fee_dest = fee_wallet_address or privy_operator_address
        try:
            blockhash = await fetch_latest_blockhash(solana_rpc_url)
            firma = await collect_buyin(solana_rpc_url, privy_signer, wallet_id, wallet,
                                        privy_operator_wallet_id, privy_operator_address,
                                        fee_dest, cc_usdc_mint, precio, blockhash)
        except Exception as e:
            fila.status = "failed"
            s.commit()
            logger.warning("tracker-pass: el cobro falló para %s: %s", wallet, e)
            raise HTTPException(502, "charge failed")

        # La firma ANTES que el estado, siempre. Es lo que hace reconciliable el hueco.
        fila.tx_signature = firma
        s.commit()

        if not await confirmar_firma(solana_rpc_url, firma):
            fila.status = "failed"
            s.commit()
            logger.warning("tracker-pass: enviado y no confirmado para %s, firma %s", wallet, firma)
            raise HTTPException(502, "charge not confirmed")

        fila.status = "active"
        s.commit()
        return {"pass_until": int(hasta.timestamp()), "days": body.days,
                "price_usdc": precio / 1_000_000}
```

Añadir los imports que falten: `uuid`, `TrackerPass`, `confirmar_firma`, `tracker_pass`.

- [ ] **Step 5: Ejecutar y ver que pasa**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_tracker_pass_api.py -q`
Expected: 10 passed

- [ ] **Step 6: Mutar el orden, que es lo que se está protegiendo**

Cada una tiene que poner rojo el fichero:

1. Mover `fila.status = "active"` a justo después del `s.add(fila)` → deben fallar los tests de cobro fallido y de no confirmado.
2. Quitar la llamada a `confirmar_firma` → debe fallar `test_un_cobro_ENVIADO_pero_NO_confirmado`.
3. Quitar `await _require_available(...)` → deben fallar los dos tests de saldo.
4. Guardar `fila.tx_signature` después de `fila.status = "failed"` en la rama de no confirmado → debe fallar la aserción de la firma en ese test.

- [ ] **Step 7: Suite entera y commit**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q`

```bash
git add backend/app/main.py backend/tests/test_tracker_pass_api.py
git commit -m "$(cat <<'EOF'
feat(tracker): comprar el pase, con el orden que protege al que paga

Cobrar en la cadena y anotar en nuestra base no pueden ser atómicas, así que una va antes
y la otra puede fallar. El orden es saldo disponible, fila pendiente, cobro, confirmación,
y solo entonces activo.

Al revés (activar y cobrar después) regala el acceso cuando el cobro falla, y ese fallo lo
provoca cualquiera con la cuenta vacía.

El saldo se mira DISPONIBLE, descontando lo reservado: sin eso, un pase podía gastarse el
dinero comprometido en una batalla y dejarla sin fondos al liquidar.

Y no basta con que el cobro se envíe. Se confirma, porque `submit_signed_tx` devuelve la
firma sin esperar nada y una transacción puede caerse después de salir.

Queda un hueco entre cobrar y activar. Por eso la firma se guarda antes que el estado: un
`pending` con firma significa exactamente "se cobró y no se activó".

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Cerrar los datos

**Files:**
- Modify: `backend/app/main.py` (`/gacha/ev` línea ~943, `/gacha/ev/live` línea ~1040)
- Test: `backend/tests/test_ev_cerrado.py` (crear)

**Interfaces:**
- Consumes: el mismo cálculo de acceso de la tarea 4.
- Produces: `/gacha/ev` y `/gacha/ev/live` responden **403** con `{"detail": "tracker_locked"}` sin acceso.

- [ ] **Step 1: Escribir el test que falla**

Crear `backend/tests/test_ev_cerrado.py`:

```python
"""El tracker deja de ser público en cuanto se cobra por él.

Hasta ahora `/gacha/ev` era abierto y el cliente ni le mandaba el token: la puerta escondía la
pantalla, no los datos. Un `curl` devolvía el tracker entero. Era defendible mientras fuese
gratis; deja de serlo en cuanto alguien paga.

Lo que se protege es lo único irreconstruible: el `getAllWinners` de CC tope en 200 tiradas por
máquina y no hay forma de mirar más atrás, así que la medición realizada solo la tiene quien
lleva escuchando el feed desde antes.
"""


def test_sin_token_el_tracker_esta_CERRADO(client):
    r = client.get("/gacha/ev")
    assert r.status_code == 403
    assert r.json()["detail"] == "tracker_locked"


def test_el_carril_rapido_tambien(client):
    # Lleva las rachas, que es medición nuestra igual que el edge.
    assert client.get("/gacha/ev/live").status_code == 403


def test_con_token_pero_SIN_acceso_sigue_cerrado(client, usuario_sin_acceso):
    assert client.get("/gacha/ev", headers=client.hdrs).status_code == 403


def test_con_acceso_por_WAGER_se_abre(client, usuario_con_wager):
    assert client.get("/gacha/ev", headers=client.hdrs).status_code == 200


def test_con_acceso_por_PASE_se_abre(client, usuario_con_pase):
    assert client.get("/gacha/ev", headers=client.hdrs).status_code == 200


def test_un_pase_CADUCADO_vuelve_a_cerrar(client, usuario_con_pase_caducado):
    assert client.get("/gacha/ev", headers=client.hdrs).status_code == 403


def test_el_403_NO_es_un_502_disfrazado(client):
    """La pantalla distingue "no puedes" de "se ha roto algo", y tiene que poder seguir
    haciéndolo: un 403 enseña la puerta, un 502 enseña el aviso de fallo."""
    assert client.get("/gacha/ev").status_code == 403
```

Las fixtures se construyen así, reutilizando el `entorno` de la tarea 6 (copiarlo a este fichero
o extraerlo a `tests/conftest.py`; extraerlo es mejor y no cuesta nada):

```python
@pytest.fixture()
def usuario_sin_acceso(client):
    return client                     # ni wager ni pase: la puerta cerrada


@pytest.fixture()
def usuario_con_wager(client):
    """100 USDC apostados dentro de la ventana. Se siembra una batalla liquidada."""
    from app.models import PackBattle, BattlePlayer
    with client.session_factory() as s:
        s.add(PackBattle(id="b1", status="settled", machine_code="pokemon_50",
                         price_base_units=100_000_000, max_players=2,
                         settled_at=datetime.now(timezone.utc)))
        s.add(BattlePlayer(battle_id="b1", player_wallet=WALLET))
        s.commit()
    return client


@pytest.fixture()
def usuario_con_pase(client):
    ahora = datetime.now(timezone.utc)
    with client.session_factory() as s:
        s.add(TrackerPass(id="p1", wallet=WALLET, days=7, price_base_units=10_000_000,
                          status="active", starts_at=ahora, ends_at=ahora + timedelta(days=7)))
        s.commit()
    return client


@pytest.fixture()
def usuario_con_pase_caducado(client):
    ahora = datetime.now(timezone.utc)
    with client.session_factory() as s:
        s.add(TrackerPass(id="p2", wallet=WALLET, days=7, price_base_units=10_000_000,
                          status="active", starts_at=ahora - timedelta(days=8),
                          ends_at=ahora - timedelta(seconds=1)))
        s.commit()
    return client
```

Los campos exactos de `PackBattle` y `BattlePlayer` se copian de cómo los siembra
`tests/test_tracker_access.py`, que ya tiene ese sembrado resuelto. Los tests usan `client.hdrs`.

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_ev_cerrado.py -q`
Expected: FAIL, devuelve 200 donde se espera 403

- [ ] **Step 3: Implementar**

En `backend/app/main.py`, añadir un ayudante junto a `/gacha/tracker-access`:

```python
    def _exigir_tracker(authorization: Optional[str], s: Session) -> None:
        """403 si quien pregunta no puede ver el tracker.

        Antes esto era público, y el comentario de entonces decía que cerrarlo "daría una falsa
        sensación de exclusividad a cambio de romper los enlaces que se comparten". Eso valía
        mientras el tracker era gratis. Desde que se cobra por él, dejarlo abierto sería cobrar
        por algo que un `curl` regala.

        Y lo de los enlaces resultó no ser cierto: el único consumidor de `/gacha/ev` es la propia
        pantalla, que ya no lo llama con la puerta puesta. Compartir `/machine-tracker` con
        alguien sin acceso YA le enseñaba la puerta.
        """
        wallet = _wallet_opcional(authorization)
        pase = tracker_pass.pase_vigente(s, wallet) if wallet else None
        if not tracker_access.acceso(s, wallet, lista_blanca=_tracker_allow,
                                     pase_hasta=pase)["allowed"]:
            raise HTTPException(403, "tracker_locked")
```

Extraer de `/gacha/tracker-access` la resolución opcional de la wallet a `_wallet_opcional(authorization) -> Optional[str]` para no duplicarla, y usarla en los dos sitios.

Añadir a las firmas de `/gacha/ev` y `/gacha/ev/live` el parámetro `authorization: Optional[str] = Header(None)` y `s: Session = Depends(db)`, y como primera línea del cuerpo `_exigir_tracker(authorization, s)`.

Actualizar la docstring de `/gacha/tracker-access` (línea ~1026), que hoy afirma que el tracker sigue siendo público.

- [ ] **Step 4: Ejecutar y ver que pasa**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_ev_cerrado.py -q`
Expected: 7 passed

- [ ] **Step 5: Comprobar que no se rompió el respaldo de CC**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_ev_respaldo.py tests/test_ev_live_api.py -q`
Expected: verde. Si fallan, es porque esos tests llaman sin token: **darles acceso**, no quitar la comprobación.

- [ ] **Step 6: Suite entera y commit**

Run: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q`

```bash
git add backend/app/main.py backend/tests/test_ev_cerrado.py backend/tests/test_ev_respaldo.py backend/tests/test_ev_live_api.py
git commit -m "$(cat <<'EOF'
fix(tracker): /gacha/ev deja de ser público, ahora que se cobra por él

La puerta escondía la pantalla, no los datos: el endpoint era abierto y el cliente ni le
mandaba el token, así que un curl devolvía el tracker entero con edge, veredicto, rachas y
modelo. Era una decisión consciente y defendible mientras fuese gratis.

Lo que se protege es lo único irreconstruible. El getAllWinners de CC tope en 200 tiradas
por máquina y no hay forma de mirar más atrás, así que la medición realizada solo la tiene
quien lleva escuchando el feed desde antes.

El comentario de entonces temía romper los enlaces compartidos. Resultó no ser cierto: el
único consumidor es la propia pantalla, que ya no lo llama con la puerta puesta.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: El cliente

**Files:**
- Modify: `src/onchain/gachaClient.ts`
- Modify: `src/ui/screens/MachineTracker/MachineTrackerPage.tsx`
- Test: `src/onchain/gachaClient.trackerPass.test.ts` (crear)

**Interfaces:**
- Consumes: `POST /gacha/tracker-pass` (tarea 6), el 403 (tarea 7), `TrackerAccess` ampliado (tarea 4).
- Produces:
  - `fetchEvRows(token?: string | null)` y `fetchEvLive(token?: string | null)`
  - `buyTrackerPass(days: 7 | 30, token: string): Promise<{ pass_until: number; days: number; price_usdc: number }>`
  - `TrackerAccess` con `via`, `pass_until`, `pass_prices`

- [ ] **Step 1: Escribir el test que falla**

Crear `src/onchain/gachaClient.trackerPass.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { buyTrackerPass, fetchEvRows, fetchEvLive } from './gachaClient'

const fetchMock = vi.fn()
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset() })
afterEach(() => vi.unstubAllGlobals())

const ok = (body: unknown) =>
  fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => body })

describe('el cliente del pase del tracker', () => {
  it('las filas del tracker viajan CON token, que antes no lo llevaban', async () => {
    ok({ rows: [], updated_at: 0 })
    await fetchEvRows(undefined, 'tok')
    const [, init] = fetchMock.mock.calls[0]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok')
  })

  it('el carril rápido también', async () => {
    ok({ rows: [], updated_at: 0 })
    await fetchEvLive('tok')
    const [, init] = fetchMock.mock.calls[0]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok')
  })

  it('comprar manda la duración y el token', async () => {
    ok({ pass_until: 123, days: 30, price_usdc: 30 })
    const r = await buyTrackerPass(30, 'tok')
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/gacha/tracker-pass')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ days: 30 })
    expect(r.pass_until).toBe(123)
  })

  it('un 403 se distingue de un fallo, para poder enseñar la puerta', async () => {
    // Si se confundiera con un error de red, la pantalla diría "se ha roto algo" cuando lo que
    // pasa es que se acabó el pase.
    fetchMock.mockResolvedValue({ ok: false, status: 403, json: async () => ({ detail: 'tracker_locked' }) })
    await expect(fetchEvRows(undefined, 'tok')).rejects.toMatchObject({ status: 403 })
  })
})
```

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `npx vitest run src/onchain/gachaClient.trackerPass.test.ts`
Expected: FAIL, `buyTrackerPass` no existe

- [ ] **Step 3: Implementar el cliente**

En `src/onchain/gachaClient.ts`:

```ts
/** El tracker dejó de ser público cuando se empezó a cobrar por él, así que estas dos llamadas
 *  viajan con token. Un 403 significa "no tienes acceso", NO que algo se haya roto. */
export function fetchEvRows(hours?: number, token?: string | null): Promise<{ rows: EvRow[]; updated_at: number }> {
  const q = hours ? `?hours=${hours}` : ''
  return gachaFetch(`/gacha/ev${q}`, token ? { headers: { Authorization: `Bearer ${token}` } } : undefined)
}

export function fetchEvLive(token?: string | null): Promise<{ rows: EvLive[]; updated_at: number }> {
  return gachaFetch('/gacha/ev/live', token ? { headers: { Authorization: `Bearer ${token}` } } : undefined)
}

export function buyTrackerPass(days: 7 | 30, token: string):
  Promise<{ pass_until: number; days: number; price_usdc: number }> {
  return gachaFetch('/gacha/tracker-pass', {
    method: 'POST', headers: authHeaders(token), body: JSON.stringify({ days }),
  })
}
```

Ampliar la interfaz `TrackerAccess` con `via: 'wager' | 'pass' | 'house' | null`, `pass_until: number | null` y `pass_prices: Record<string, number>`.

Conservar la firma existente de `fetchEvRows` en cuanto al parámetro `hours`, para no romper a quien la llame.

- [ ] **Step 4: Pasar el token desde la pantalla**

En `src/ui/screens/MachineTracker/MachineTrackerPage.tsx`, `PanelEv` pasa a recibir el token y el acceso:

```tsx
function PanelEv({ token, onSinAcceso }: { token: string | null; onSinAcceso: () => void }) {
```

Las llamadas de los dos carriles pasan `token`, y sus `.catch` distinguen el 403:

```tsx
        .catch((e) => {
          // Un 403 no es un fallo: es que el pase caducó con la pantalla abierta. Enseñar
          // "Couldn't load the tracker" diría que algo se ha roto cuando no se ha roto nada.
          if ((e as { status?: number })?.status === 403) { onSinAcceso(); return }
          if (!cancelado) setFallo((antes) => antes || filasRef.current == null)
        })
```

`onSinAcceso` vuelve a pedir `/gacha/tracker-access`, lo que hace aparecer la puerta.

- [ ] **Step 5: Ejecutar y ver que pasa**

Run: `npx vitest run src/onchain src/ui/screens/MachineTracker && npx tsc -b`
Expected: verde y typecheck limpio

- [ ] **Step 6: Commit**

```bash
git add src/onchain/gachaClient.ts src/onchain/gachaClient.trackerPass.test.ts src/ui/screens/MachineTracker/MachineTrackerPage.tsx
git commit -m "$(cat <<'EOF'
feat(tracker): el cliente manda el token y sabe comprar el pase

Las dos llamadas del tracker no mandaban nada porque el endpoint era abierto. Ahora viajan
con el token de Privy, que la pantalla ya tenía para consultar el acceso.

Y un 403 se trata aparte de un fallo de red: significa que el pase caducó con la pantalla
abierta, así que se vuelve a pedir el acceso y aparece la puerta. Mezclarlo con el aviso de
carga diría "se ha roto algo" cuando no se ha roto nada.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: La compra en la puerta

**Files:**
- Create: `src/ui/screens/MachineTracker/PassOffer.tsx`
- Create: `src/ui/screens/MachineTracker/PassOffer.test.tsx`
- Modify: `src/ui/screens/MachineTracker/TrackerGate.tsx`
- Modify: `src/ui/screens/MachineTracker/MachineTrackerPage.tsx`

**Interfaces:**
- Consumes: `buyTrackerPass` y `TrackerAccess.pass_prices` (tarea 8).
- Produces: `<PassOffer prices={...} token={...} onComprado={() => void} />`

- [ ] **Step 1: Escribir el test que falla**

Crear `src/ui/screens/MachineTracker/PassOffer.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const mocks = vi.hoisted(() => ({ comprar: vi.fn() }))
vi.mock('../../../onchain/gachaClient', () => ({ buyTrackerPass: mocks.comprar }))

import { PassOffer } from './PassOffer'

beforeEach(() => mocks.comprar.mockReset())

describe('la compra del pase, dentro de la puerta', () => {
  it('sin precios configurados NO se ofrece nada', () => {
    // Cero significa apagado. Un botón deshabilitado prometería algo que no existe.
    const { container } = render(<PassOffer prices={{}} token="t" onComprado={() => {}} />)
    expect(container.textContent).toBe('')
  })

  it('enseña las dos duraciones con su precio', () => {
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    expect(screen.getByText(/7 days/i)).toBeTruthy()
    expect(screen.getByText(/30 days/i)).toBeTruthy()
  })

  it('dice el precio POR DÍA, que es lo que deja comparar', () => {
    // 30 a 30 son 1.00/día; 7 a 10 son 1.43/día. Sin esta cifra nadie ve cuál sale mejor.
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    expect(screen.getByText(/1\.43/)).toBeTruthy()
    expect(screen.getByText(/1\.00/)).toBeTruthy()
  })

  it('avisa de que no hay devoluciones ANTES de pagar', () => {
    // Si compras 30 días y mañana apuestas 100, has pagado por algo que ya tenías.
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    expect(screen.getByText(/no refunds/i)).toBeTruthy()
  })

  it('comprar avisa a quien lo montó, para que refresque el acceso', async () => {
    mocks.comprar.mockResolvedValue({ pass_until: 1, days: 7, price_usdc: 10 })
    const onComprado = vi.fn()
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={onComprado} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    await waitFor(() => expect(onComprado).toHaveBeenCalled())
    expect(mocks.comprar).toHaveBeenCalledWith(7, 't')
  })

  it('mientras se cobra no se puede pulsar dos veces', async () => {
    // Un doble clic sobre un cobro son dos cobros.
    mocks.comprar.mockReturnValue(new Promise(() => {}))
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    const boton = screen.getByRole('button', { name: /7 days/i })
    fireEvent.click(boton)
    fireEvent.click(boton)
    expect(mocks.comprar).toHaveBeenCalledTimes(1)
  })

  it('sin saldo lo dice con lo que hay que hacer, no con un error genérico', async () => {
    mocks.comprar.mockRejectedValue({ status: 402 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/not enough usdc/i)).toBeTruthy()
  })

  it('un fallo de cobro dice que se reintente, y NO que deposite', async () => {
    // Tiene el dinero: pedirle depositar sería insultante y no arreglaría nada.
    mocks.comprar.mockRejectedValue({ status: 502 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/try again/i)).toBeTruthy()
  })
})
```

- [ ] **Step 2: Ejecutar y ver que falla**

Run: `npx vitest run src/ui/screens/MachineTracker/PassOffer.test.tsx`
Expected: FAIL, el módulo no existe

- [ ] **Step 3: Implementar**

Crear `src/ui/screens/MachineTracker/PassOffer.tsx`:

```tsx
import { useState } from 'react'
import { COLORS, FONTS } from '../../theme'
import { buyTrackerPass } from '../../../onchain/gachaClient'

const DURACIONES = [7, 30] as const

/**
 * Comprar el pase, dentro de la puerta.
 *
 * Va aquí y no en un ajuste aparte porque este es el momento en que alguien siente que le falta
 * la herramienta: acaba de leer cuánto le queda para entrar jugando.
 *
 * SIN PRECIOS NO SE RENDERIZA NADA. Cero significa que la compra no existe todavía, no que esté
 * rota, así que un botón deshabilitado prometería algo que no hay.
 */
export function PassOffer({ prices, token, onComprado }: {
  prices: Record<string, number>
  token: string | null
  onComprado: () => void
}) {
  const [cobrando, setCobrando] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const disponibles = DURACIONES.filter((d) => (prices[String(d)] ?? 0) > 0)
  if (disponibles.length === 0 || !token) return null

  async function comprar(dias: 7 | 30) {
    if (cobrando !== null) return          // un doble clic sobre un cobro son dos cobros
    setCobrando(dias)
    setError(null)
    try {
      await buyTrackerPass(dias, token!)
      onComprado()
    } catch (e) {
      // Los dos fallos piden cosas OPUESTAS. Sin saldo hay que depositar; si el cobro falló, el
      // dinero está y pedirle depositar sería inútil además de insultante.
      const status = (e as { status?: number })?.status
      setError(status === 402
        ? 'Not enough USDC. Deposit and try again.'
        : "We couldn't take the payment. Try again.")
    } finally {
      setCobrando(null)
    }
  }

  return (
    <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid #ffffff14' }}>
      <div style={{
        fontFamily: FONTS.mono, fontSize: 9, letterSpacing: '.22em', color: COLORS.muted,
        marginBottom: 10,
      }}>
        OR UNLOCK NOW
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {disponibles.map((dias) => {
          const precio = prices[String(dias)]
          return (
            <button
              key={dias}
              type="button"
              onClick={() => comprar(dias)}
              disabled={cobrando !== null}
              style={{
                flex: '1 1 140px', minHeight: 58, borderRadius: 12, cursor: 'pointer',
                border: `1px solid ${COLORS.border}`, background: '#ffffff0a',
                color: COLORS.text, fontFamily: FONTS.display, fontWeight: 700,
                display: 'flex', flexDirection: 'column', gap: 3, padding: '9px 12px',
                opacity: cobrando !== null && cobrando !== dias ? 0.5 : 1,
              }}
            >
              <span style={{ fontSize: 14 }}>
                {cobrando === dias ? 'Paying…' : `${dias} days · $${precio}`}
              </span>
              {/* El precio por día es lo ÚNICO que deja comparar 7 con 30 de un vistazo. */}
              <span style={{ fontFamily: FONTS.mono, fontSize: 9.5, color: COLORS.muted }}>
                ${(precio / dias).toFixed(2)} per day
              </span>
            </button>
          )
        })}
      </div>

      {error && (
        <div style={{ marginTop: 9, fontFamily: FONTS.mono, fontSize: 10, color: '#ff6ba4' }}>
          {error}
        </div>
      )}

      {/* Visible y ANTES de pagar, no en letra pequeña: si compras 30 días y mañana apuestas
          100 USDC, has pagado por algo que ya tenías. */}
      <div style={{ marginTop: 9, fontFamily: FONTS.mono, fontSize: 9.5, color: COLORS.muted }}>
        No refunds. Wagering still unlocks it for free.
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Montarlo en la puerta**

En `TrackerGate.tsx`, tras el botón `Find a match →`, insertar:

```tsx
      <PassOffer prices={acceso.pass_prices} token={token} onComprado={onComprado} />
```

`TrackerGate` pasa a recibir `token: string | null` y `onComprado: () => void`, que `MachineTrackerPage` le da (el mismo `onSinAcceso` de la tarea 8, que vuelve a pedir el acceso).

- [ ] **Step 5: Enseñar la caducidad dentro del tracker**

En `MachineTrackerPage.tsx`, en la cabecera donde se pinta `UPDATED`/`STALE`, cuando `acceso.via === 'pass'`:

```tsx
        {acceso?.via === 'pass' && acceso.pass_until && (
          <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: COLORS.muted }}>
            PASS UNTIL {new Date(acceso.pass_until * 1000).toLocaleDateString()}
          </span>
        )}
```

- [ ] **Step 6: Ejecutar todo y comprobar el tipado**

Run: `npx vitest run src && npx tsc -b && npm run build`
Expected: todo verde, typecheck limpio, build correcto

- [ ] **Step 7: Commit**

```bash
git add src/ui/screens/MachineTracker/PassOffer.tsx src/ui/screens/MachineTracker/PassOffer.test.tsx src/ui/screens/MachineTracker/TrackerGate.tsx src/ui/screens/MachineTracker/MachineTrackerPage.tsx
git commit -m "$(cat <<'EOF'
feat(tracker): comprar el pase desde la propia puerta

La puerta ya decía cuánto falta y ofrecía buscar partida. Ahora ofrece además las dos
duraciones, que es el momento exacto en que alguien siente que le falta la herramienta.

Con los precios apagados no se renderiza nada, ni un botón deshabilitado: cero significa
que la compra no existe, no que esté rota.

Se enseña el precio por día porque es lo único que deja comparar 7 con 30, y el aviso de
que no hay devoluciones va visible y antes de pagar, no en letra pequeña: si compras 30
días y mañana apuestas 100, has pagado por algo que ya tenías.

Y los dos fallos dicen cosas distintas. Sin saldo, deposita. Cobro fallido, reinténtalo:
tiene el dinero, y pedirle depositar sería insultante además de inútil.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Verificación final

Antes de dar el trabajo por terminado:

1. `cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q` → todo verde
2. `npx vitest run src` → todo verde
3. `npx tsc -b` → sin salida
4. `npm run build` → compila
5. **A mano, con los servicios levantados** (ver `docs/STARTUP.md`): con `TRACKER_PASS_7D_USDC` y `TRACKER_PASS_30D_USDC` sin poner, comprobar que la puerta NO ofrece comprar y que `POST /gacha/tracker-pass` responde 503. Es el estado en que esto se va a desplegar.
6. **`curl http://127.0.0.1:9090/gacha/ev` sin token tiene que devolver 403.** Es la comprobación que cierra el motivo de todo esto.

## Lo que este plan NO hace

- No decide el precio. Se despliega apagado y se enciende cuando se decida.
- No hay renovación automática ni cobro recurrente. Está descartado en la spec, con motivo.
- No hay devoluciones ni proceso de reembolso.
- No hay panel de administración de pases. Un `pending` con firma se arregla a mano con SQL, que es lo que la spec asume.
