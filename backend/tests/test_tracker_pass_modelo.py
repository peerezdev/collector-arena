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
