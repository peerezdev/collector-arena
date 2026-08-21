"""Quién puede ver el Machine Tracker.

El tracker mide lo que paga de verdad cada máquina del gacha, y eso es información que cuesta
dinero producir: escuchar el feed sin parar, barrer 106.000 cartas, y 48 h de datos antes de poder
afirmar nada. Se reserva para quien juega en las mesas, no para quien pasa a mirar.

LA REGLA ES UNA VENTANA RODANTE, NO UN CARNET. Se suma lo apostado en los ÚLTIMOS 7 DÍAS cada vez
que se pregunta, así que no hay nada que caducar ni ningún estado que mantener:

  · hoy apuesto 100 → entro
  · siete días después, sin apostar más, esos 100 ya no están dentro de la ventana → vuelve el aviso
  · si al cuarto día aposté 50, al reaparecer el aviso esos 50 SIGUEN contando, porque siguen
    dentro de los últimos 7 días. El aviso pedirá 50, no 100.

Esa última parte es la que hace que no se pueda implementar con una fecha de caducidad: lo que vale
no es "cuándo llegó a 100", es cuánto lleva apostado en los últimos siete días, mirado ahora.

SOLO PACK BATTLE Y BATTLE ROYALE. El gacha NO cuenta: abrir un sobre no es apostar contra nadie, y
además el tracker existe precisamente para decidir si abrirlo merece la pena. Contarlo sería pedir
que gastes en gacha para poder ver si el gacha te conviene.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BattlePlayer, PackBattle

#: Cuánto hay que llevar apostado, en USDC.
MINIMO_USD = 100.0
#: Hacia atrás. Rodante: se mira desde "ahora", no desde el lunes ni desde que te dieron acceso.
VENTANA_DIAS = 7

USDC = 1_000_000


def _sin_zona(d: Optional[datetime]) -> Optional[datetime]:
    """SQLite devuelve los datetime sin zona aunque se guarden con ella; compararlos con uno que sí
    la lleva revienta. Se les vuelve a poner UTC, que es como se guardaron."""
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def wager_reciente_usd(session: Session, wallet: str, *, dias: int = VENTANA_DIAS,
                       ahora: Optional[datetime] = None) -> float:
    """Lo apostado en batallas por esa wallet en los últimos `dias`, en USDC.

    Solo partidas LIQUIDADAS. Una anulada se devolvió, así que contarla sería contar dinero que el
    jugador recuperó; y una en curso todavía puede anularse.

    Se fecha por `settled_at`, con `created_at` de respaldo para las filas que el backend liquidó
    antes de empezar a guardar esa marca. Sin el respaldo esas partidas no contarían nunca.
    """
    ahora = ahora or datetime.now(timezone.utc)
    desde = ahora - timedelta(days=dias)
    from .users import _entry_base_units

    batallas = session.scalars(
        select(PackBattle)
        .join(BattlePlayer, BattlePlayer.battle_id == PackBattle.id)
        .where(BattlePlayer.player_wallet == wallet, PackBattle.status == "settled")
    ).all()

    total = 0
    for b in batallas:
        cuando = _sin_zona(b.settled_at) or _sin_zona(b.created_at)
        if cuando is not None and cuando >= desde:
            total += _entry_base_units(b)
    return total / USDC


def acceso(session: Session, wallet: Optional[str], *, minimo_usd: float = MINIMO_USD,
           dias: int = VENTANA_DIAS, ahora: Optional[datetime] = None,
           lista_blanca: Optional[set[str]] = None,
           pase_hasta: Optional[datetime] = None,
           precios: Optional[dict] = None) -> dict:
    """Si esa wallet puede ver el tracker, cuánto le falta si no, y por qué motivo entra.

    Sin wallet no hay acceso, pero tampoco es un error: es alguien que no ha entrado, y lo que
    procede es enseñarle qué es esto y qué hace falta, no un 401.

    `missing_usd` se redondea hacia ARRIBA al céntimo. Si faltan 0.004, decir "te faltan 0.00" es
    una trampa: el jugador apostaría creyendo que ya está y seguiría fuera.

    `lista_blanca` son wallets con acceso permanente, para las cuentas de la casa: probar el
    tracker en producción no puede exigir apostar 100 USDC de verdad cada semana. Tres cosas la
    hacen segura, y las tres importan:

      · La fija el DESPLIEGUE (variable de entorno), no la base ni el cliente. No hay ninguna vía
        por la que un jugador se añada, ni pidiéndolo ni escribiendo en la base.
      · La wallet que se compara sale del token de Privy ya verificado (firma ES256 contra su
        JWKS), no de un parámetro. Suplantarla es falsificar ese token, o sea el mismo listón que
        entrar en la cuenta.
      · Se compara la cadena ENTERA. Nada de minúsculas ni de prefijos: base58 distingue
        mayúsculas, y una comparación laxa dejaría entrar wallets que solo se PARECEN a la de
        la casa.

    `pase_hasta` es la tercera vía: quien compró un pase entra sin haber apostado nada, mientras
    ese pase siga vigente. Lo resuelve `tracker_pass.pase_vigente`; aquí solo se confía en la
    fecha si todavía es futura, porque la puerta no puede fiarse de un pase ya caducado.

    Estar en la lista o tener pase abre la puerta pero NO falsea `wagered_usd`: se sigue diciendo
    lo que de verdad se ha apostado, porque esa cifra también se enseña y mentirla haría
    mentirosa a la pantalla entera.

    `via` dice el motivo REAL por el que se entra, resuelto de más fuerte a más débil (casa, pase,
    wager). Sin ese orden alguien con pase Y wager saldría como "wager", y nadie entendería por
    qué sigue dentro el día que el wager se le salga de la ventana.

    `precios` viaja tal cual en `pass_prices`, para que la pantalla pueda pintar el bloque de
    compra sin conocer los settings del backend. Vacío y no con ceros: que la compra esté apagada
    no es algo que la pantalla tenga que aprender a interpretar.
    """
    apostado = wager_reciente_usd(session, wallet, dias=dias, ahora=ahora) if wallet else 0.0
    # `wallet and` va delante a propósito: sin sesión no se compara contra la lista. Sin esa
    # guarda, un None convertiría la lista de la casa en el acceso de cualquiera.
    invitada = bool(wallet and lista_blanca and wallet in lista_blanca)

    ahora_dt = ahora or datetime.now(timezone.utc)
    con_pase = bool(pase_hasta and pase_hasta > ahora_dt)
    por_wager = apostado >= minimo_usd

    # De más fuerte a más débil, para que `via` diga el motivo REAL. Sin este orden, alguien con
    # pase Y wager saldría como "wager" y nadie entendería por qué sigue dentro al caducar.
    via = "house" if invitada else "pass" if con_pase else "wager" if por_wager else None

    # Solo la casa vacía `missing_usd`: esa cuenta no necesita saber cuánto le falta por wager. Un
    # pase no lo vacía porque sigue siendo información real: cuánto llevaría de wager si el pase
    # no estuviera, que es lo que la pantalla puede querer seguir enseñando mientras dura.
    falta = 0.0 if invitada else max(0.0, minimo_usd - apostado)
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
