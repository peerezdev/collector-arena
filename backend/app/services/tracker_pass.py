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
