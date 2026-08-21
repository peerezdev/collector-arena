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
    # 2.01 son 2_010_000 exactos, pero 2.01 * 1_000_000 en coma flotante da 2009999.9999999998:
    # truncar con `int()` perdería un céntimo aquí. Con 12.99 no pasaba (esa cae justo en un
    # valor representable exacto), así que no servía para distinguir redondear de truncar.
    assert precio_base_units(7, 2.01, 30.0) == 2_010_000


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
