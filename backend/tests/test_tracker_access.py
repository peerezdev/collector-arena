"""Quién ve el Machine Tracker: 100 USDC apostados en batallas en los últimos 7 días.

La ventana es RODANTE y eso es lo que se prueba aquí, porque es lo que hace que no se pueda
implementar con una fecha de caducidad.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import BattlePlayer, PackBattle
from app.services.tracker_access import MINIMO_USD, acceso, wager_reciente_usd

AHORA = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
YO = "So1anaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"
OTRO = "So1anaBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB1"
USDC = 1_000_000


def apostar(s, usd, *, hace_dias=0, wallet=YO, mode="pack", estado="settled", n=2):
    """Una batalla liquidada de `usd` por jugador, fechada hace `hace_dias`."""
    bid = f"b{int(hace_dias*100)}-{usd}-{mode}-{estado}-{wallet[:6]}"
    # En pack, `price` ES lo que paga cada uno. En royale se calcula del bote, así que para que el
    # test sea legible se usa pack salvo cuando el caso es justo el royale.
    s.add(PackBattle(id=bid, mode=mode, machine_code="pokemon_50", price=int(usd * USDC),
                     max_players=n, status=estado,
                     created_at=AHORA - timedelta(days=hace_dias),
                     settled_at=(AHORA - timedelta(days=hace_dias)) if estado == "settled" else None))
    s.add(BattlePlayer(battle_id=bid, player_wallet=wallet))
    s.commit()


class TestVentanaRodante:
    def test_lo_de_hoy_cuenta(self, Session):
        with Session() as s:
            apostar(s, 100, hace_dias=0)
            assert acceso(s, YO, ahora=AHORA)["allowed"] is True

    def test_a_los_siete_dias_sin_apostar_mas_vuelve_el_aviso(self, Session):
        """El caso que define la regla: no es un carnet que se otorga, es una ventana que avanza."""
        with Session() as s:
            apostar(s, 100, hace_dias=0)
            assert acceso(s, YO, ahora=AHORA)["allowed"] is True
            # Mismo dato, siete días después: esos 100 ya se salieron de la ventana.
            luego = AHORA + timedelta(days=7, minutes=1)
            r = acceso(s, YO, ahora=luego)
            assert r["allowed"] is False
            assert r["wagered_usd"] == 0.0 and r["missing_usd"] == 100.0

    def test_lo_apostado_a_mitad_SIGUE_contando_cuando_reaparece_el_aviso(self, Session):
        """La parte que descarta cualquier implementación con fecha de caducidad.

        Día 0: apuesto 100 y entro. Día 4: apuesto 50 más. Día 7: los 100 del día 0 caducan, pero
        los 50 del día 4 siguen dentro de la ventana, así que el aviso tiene que pedir 50, no 100.
        """
        with Session() as s:
            apostar(s, 100, hace_dias=0)
            apostar(s, 50, hace_dias=-4)          # cuatro días DESPUÉS de AHORA
            dia7 = AHORA + timedelta(days=7, minutes=1)
            r = acceso(s, YO, ahora=dia7)
            assert r["allowed"] is False
            assert r["wagered_usd"] == 50.0
            assert r["missing_usd"] == 50.0       # no 100

    def test_justo_en_el_borde_de_la_ventana_todavia_cuenta(self, Session):
        with Session() as s:
            apostar(s, 100, hace_dias=6.99)
            assert acceso(s, YO, ahora=AHORA)["allowed"] is True

    def test_un_dia_mas_alla_del_borde_ya_no(self, Session):
        with Session() as s:
            apostar(s, 100, hace_dias=7.01)
            assert acceso(s, YO, ahora=AHORA)["wagered_usd"] == 0.0


class TestQueCuenta:
    def test_suma_varias_partidas_hasta_llegar(self, Session):
        with Session() as s:
            apostar(s, 40, hace_dias=1)
            apostar(s, 60, hace_dias=3)
            assert acceso(s, YO, ahora=AHORA)["allowed"] is True

    def test_las_royale_cuentan_por_su_buy_in_completo(self, Session):
        """En royale `price` es el precio por sobre, no lo que paga el jugador: cuenta el buy-in."""
        with Session() as s:
            apostar(s, 50, hace_dias=1, mode="royale", n=5)
            # El buy-in de una royale de 5 a 50 $ el sobre es muy superior al precio del sobre.
            assert wager_reciente_usd(s, YO, ahora=AHORA) > 50.0

    def test_una_partida_anulada_NO_cuenta(self, Session):
        """Se devolvió el dinero: contarla sería contar lo que el jugador recuperó."""
        with Session() as s:
            apostar(s, 100, hace_dias=1, estado="voided")
            assert acceso(s, YO, ahora=AHORA)["wagered_usd"] == 0.0

    def test_una_partida_en_curso_tampoco(self, Session):
        with Session() as s:
            apostar(s, 100, hace_dias=1, estado="running")
            assert acceso(s, YO, ahora=AHORA)["allowed"] is False

    def test_lo_de_otro_jugador_no_cuenta(self, Session):
        with Session() as s:
            apostar(s, 100, hace_dias=1, wallet=OTRO)
            assert acceso(s, YO, ahora=AHORA)["wagered_usd"] == 0.0

    def test_sin_batallas_no_hay_acceso_y_falta_el_minimo_entero(self, Session):
        with Session() as s:
            r = acceso(s, YO, ahora=AHORA)
            assert r["allowed"] is False and r["missing_usd"] == MINIMO_USD

    def test_sin_wallet_no_es_un_error_sino_un_aviso(self, Session):
        """Alguien que no ha entrado merece que se le explique qué hace falta, no un 401."""
        with Session() as s:
            r = acceso(s, None, ahora=AHORA)
            assert r["allowed"] is False and r["wagered_usd"] == 0.0


class TestLoQueSeDice:
    def test_lo_que_falta_se_redondea_HACIA_ARRIBA(self, Session):
        """Si faltan 0.004 y se dice "0.00", el jugador apuesta creyendo que ya está y sigue fuera."""
        with Session() as s:
            apostar(s, 99.996, hace_dias=1)
            r = acceso(s, YO, ahora=AHORA)
            assert r["allowed"] is False
            assert r["missing_usd"] == 0.01

    def test_llegando_justo_al_minimo_se_entra(self, Session):
        with Session() as s:
            apostar(s, 100, hace_dias=1)
            r = acceso(s, YO, ahora=AHORA)
            assert r["allowed"] is True and r["missing_usd"] == 0.0

    def test_la_respuesta_dice_la_ventana_y_el_minimo(self, Session):
        # La pantalla tiene que poder escribir "en los últimos 7 días" sin repetir la constante.
        with Session() as s:
            r = acceso(s, YO, ahora=AHORA)
            assert r["window_days"] == 7 and r["required_usd"] == 100.0


class TestListaBlanca:
    """Wallets con acceso permanente, sin apostar.

    Existe para las cuentas de la casa: probar el tracker en producción no puede exigir apostar
    100 USDC de verdad cada semana. La lista la fija el DESPLIEGUE (una variable de entorno), no
    la base ni el cliente, así que no hay ningún camino por el que un jugador se añada solo.
    """

    def test_la_wallet_de_la_lista_entra_sin_haber_apostado_nada(self, Session):
        with Session() as s:
            r = acceso(s, YO, ahora=AHORA, lista_blanca={YO})
            assert r["allowed"] is True
            assert r["missing_usd"] == 0.0

    def test_a_los_demas_la_lista_no_les_regala_nada(self, Session):
        """Lo que de verdad importa: que estar la lista no ablande la puerta para el resto."""
        with Session() as s:
            r = acceso(s, OTRO, ahora=AHORA, lista_blanca={YO})
            assert r["allowed"] is False
            assert r["missing_usd"] == MINIMO_USD

    def test_sin_sesion_no_se_hereda_el_acceso_de_nadie(self, Session):
        """Sin wallet no hay a quién comparar, y una lista no vacía no puede abrir la puerta sola.

        Es el caso que convertiría la lista en un agujero: si un `None` colara, cualquiera sin
        entrar tendría el acceso de la casa.
        """
        with Session() as s:
            r = acceso(s, None, ahora=AHORA, lista_blanca={YO})
            assert r["allowed"] is False

    def test_se_compara_la_wallet_ENTERA_y_distinguiendo_mayusculas(self, Session):
        """Base58 distingue mayúsculas y no admite prefijos: parecerse no es ser.

        Sin esto, comparar en minúsculas o por prefijo dejaría entrar a wallets distintas de la
        de la casa, que es justo la suplantación que la lista no puede permitir.
        """
        with Session() as s:
            assert acceso(s, YO.lower(), ahora=AHORA, lista_blanca={YO})["allowed"] is False
            assert acceso(s, YO[:-1], ahora=AHORA, lista_blanca={YO})["allowed"] is False
            assert acceso(s, YO + "x", ahora=AHORA, lista_blanca={YO})["allowed"] is False

    def test_la_lista_vacia_deja_la_puerta_como_estaba(self, Session):
        with Session() as s:
            assert acceso(s, YO, ahora=AHORA, lista_blanca=set())["allowed"] is False
            assert acceso(s, YO, ahora=AHORA)["allowed"] is False

    def test_lo_apostado_se_sigue_diciendo_de_verdad(self, Session):
        """La lista abre la puerta; no falsea la cifra.

        Enseñar 100 apostados a quien no ha apostado nada convertiría la pantalla en mentirosa, y
        es la misma cifra que se usa para explicar cuánto falta cuando la lista no está.
        """
        with Session() as s:
            apostar(s, 30, hace_dias=1)
            r = acceso(s, YO, ahora=AHORA, lista_blanca={YO})
            assert r["allowed"] is True
            assert r["wagered_usd"] == 30.0


class TestPase:
    """El pase de pago, la segunda vía de acceso.

    Se prueba pasando `pase_hasta` directamente: quién lo calcula (`tracker_pass.pase_vigente`)
    ya tiene sus propios tests, y aquí solo importa qué hace la puerta con ese dato.
    """

    def test_un_pase_vigente_abre_la_puerta_sin_haber_apostado(self, Session):
        with Session() as s:
            r = acceso(s, YO, pase_hasta=AHORA + timedelta(days=3), ahora=AHORA)
            assert r["allowed"] is True
            assert r["via"] == "pass"
            assert r["pass_until"] == int((AHORA + timedelta(days=3)).timestamp())

    def test_el_pase_NO_falsea_lo_apostado(self, Session):
        # Por lo mismo que no lo falsea la lista blanca: esa cifra también se enseña, y mentirla
        # haría mentirosa a la pantalla entera.
        with Session() as s:
            r = acceso(s, YO, pase_hasta=AHORA + timedelta(days=3), ahora=AHORA)
            assert r["wagered_usd"] == 0.0
            assert r["missing_usd"] == 100.0

    def test_sin_pase_y_sin_wager_sigue_cerrada(self, Session):
        with Session() as s:
            r = acceso(s, YO, pase_hasta=None, ahora=AHORA)
            assert r["allowed"] is False
            assert r["via"] is None

    def test_el_orden_de_los_motivos_es_casa_pase_wager(self, Session):
        # `via` tiene que decir el motivo REAL, del más fuerte al más débil, para poder explicarlo
        # en pantalla y para poder depurar por qué alguien entra.
        with Session() as s:
            r = acceso(s, YO, pase_hasta=AHORA + timedelta(days=3), lista_blanca={YO}, ahora=AHORA)
            assert r["via"] == "house"

    def test_un_pase_caducado_no_abre_nada(self, Session):
        # `pase_hasta` en el pasado lo resuelve `pase_vigente`, pero la puerta no puede fiarse.
        with Session() as s:
            r = acceso(s, YO, pase_hasta=AHORA - timedelta(seconds=1), ahora=AHORA)
            assert r["allowed"] is False
            assert r["via"] is None

    def test_los_precios_viajan_en_la_respuesta_para_poder_pintarlos(self, Session):
        with Session() as s:
            r = acceso(s, YO, precios={"7": 10.0, "30": 30.0}, ahora=AHORA)
            assert r["pass_prices"] == {"7": 10.0, "30": 30.0}

    def test_sin_precios_configurados_el_bloque_de_compra_no_existe(self, Session):
        # Diccionario vacío y no ceros: la pantalla no tiene que saber que cero significa apagado.
        with Session() as s:
            assert acceso(s, YO, ahora=AHORA)["pass_prices"] == {}
