"""La compra del pase. Aquí se mueve dinero real de un usuario por primera vez fuera de una
partida, así que lo que más se prueba es el camino de FALLO.

Las fixtures (`pase_entorno`, `pase_client`, `pase_cobro_ok`, `pase_precio_apagado`,
`pase_sin_saldo`, `pase_saldo_reservado`, `pase_cobro_revienta`, `pase_cobro_indeterminado`,
`pase_cobro_sin_confirmar`, `pase_confirmacion_indeterminada`) están en `tests/conftest.py`,
porque la tarea 7 las reutiliza."""
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import TrackerPass

from tests.conftest import TRACKER_PASS_WALLET


def test_comprar_da_acceso_y_lo_dice(pase_client, pase_cobro_ok):
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 7
    assert r.json()["price_usdc"] == 10.0
    acc = pase_client.get("/gacha/tracker-access", headers=pase_client.hdrs).json()
    assert acc["allowed"] is True and acc["via"] == "pass"


def test_el_pase_queda_ACTIVO_y_con_su_firma(pase_client, pase_cobro_ok):
    pase_client.post("/gacha/tracker-pass", json={"days": 30}, headers=pase_client.hdrs)
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "active"
        assert p.tx_signature
        assert p.price_base_units == 30_000_000


def test_pass_until_es_cuando_TERMINA_el_pase_no_cuando_empieza(pase_client, pase_cobro_ok):
    # `pass_until` es lo que pinta la pantalla. Si el endpoint devolviera `desde` en vez de
    # `hasta` (fácil de confundir: las dos son variables del mismo `periodo()`), el jugador vería
    # que su pase de 7 días ya caducó hoy.
    ahora = time.time()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    # Primera compra, sin pase previo: `desde` es "ahora", así que `pass_until` tiene que estar a
    # ~7 días de este instante. Un margen de segundos cubre lo que tarda la petición.
    assert abs(r.json()["pass_until"] - (ahora + 7 * 86400)) < 5


def test_sin_saldo_NO_se_crea_ninguna_fila(pase_client, pase_sin_saldo):
    # El 402 ocurre antes de escribir nada: ni fila, ni cobro, ni rastro.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 402
    with pase_client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []


def test_el_saldo_RESERVADO_para_una_batalla_no_se_puede_gastar_en_un_pase(pase_client,
                                                                           pase_saldo_reservado):
    # Si un pase pudiera gastarlo, la batalla se quedaría sin fondos al liquidar.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 402


def test_un_cobro_RECHAZADO_no_da_acceso_y_deja_la_fila_en_failed(pase_client, pase_cobro_revienta):
    # RuntimeError: rechazo DEFINITIVO del RPC (ver `submit_signed_tx`). El dinero no se movió.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).one().status == "failed"
    acc = pase_client.get("/gacha/tracker-access", headers=pase_client.hdrs).json()
    assert acc["allowed"] is False
    # Nadie tuvo acceso ni siquiera en el instante en que se intentó cobrar.
    assert pase_client.mando["acceso_al_cobrar"] is False


def test_un_cobro_INDETERMINADO_deja_la_fila_pending_sin_firma(pase_client, pase_cobro_indeterminado):
    # Un timeout, un 5xx del proxy tras reenviar... no sabemos si la transacción salió. `failed`
    # aquí mentiría "seguro que no"; la fila se queda en `pending`, que es "hay que mirarlo".
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "pending"
        assert p.tx_signature is None, "nunca hubo firma: ni siquiera sabemos si se envió"
    acc = pase_client.get("/gacha/tracker-access", headers=pase_client.hdrs).json()
    assert acc["allowed"] is False
    assert pase_client.mando["acceso_al_cobrar"] is False


def test_un_fallo_al_pedir_el_blockhash_deja_FAILED_no_pending(pase_client, pase_blockhash_revienta):
    # La regresión de la ronda anterior: pedir el blockhash es lo PRIMERO que toca la red, antes
    # de construir o firmar nada. Un fallo aquí es tan determinado como un rechazo — nada se
    # difundió — así que tiene que cerrar en `failed`, no dejar la fila en `pending` (que ahora
    # bloquearía cualquier compra siguiente vía el 409 de la fila "en curso").
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "failed"
        assert p.tx_signature is None
    # collect_buyin ni se llega a invocar: el fallo fue antes de intentar cobrar.
    assert pase_client.mando["acceso_al_cobrar"] is None

    # Y el reintento funciona: arreglado el RPC, la wallet NO queda encerrada.
    pase_client.mando["blockhash"] = "11111111111111111111111111111111"
    r2 = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r2.status_code == 200, r2.text


def test_una_firma_RECHAZADA_por_privy_deja_FAILED_no_pending(pase_client, pase_firma_rechazada):
    # PrivySignerError: `sign_solana` falló antes de que hubiera nada que mandar a Solana. Mismo
    # razonamiento que el blockhash: nada se difundió, así que es determinado.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "failed"
        assert p.tx_signature is None


def test_un_cobro_ENVIADO_pero_RECHAZADO_en_cadena_tampoco_da_acceso(pase_client,
                                                                     pase_cobro_sin_confirmar):
    # confirmar_firma devuelve False: la cadena la ejecutó y la RECHAZÓ (`err` presente). Rechazo
    # definitivo, no indeterminado — por eso la fila SÍ puede cerrarse en `failed`.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "failed"
        assert p.tx_signature, "la firma se guarda igual, para poder reconciliar a mano"
    assert pase_client.mando["acceso_al_cobrar"] is False


def test_una_confirmacion_INDETERMINADA_deja_pending_con_firma(pase_client,
                                                                pase_confirmacion_indeterminada):
    # confirmar_firma devuelve None: se agotaron los intentos sin veredicto. El dinero PUEDE
    # haberse movido, así que ni `failed` (mentiría) ni `active` (regalaría acceso): se queda en
    # `pending` CON la firma, que es la consulta de una línea que promete el docstring.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "pending"
        assert p.tx_signature, "se sabe qué transacción mirar en un explorador"
    acc = pase_client.get("/gacha/tracker-access", headers=pase_client.hdrs).json()
    assert acc["allowed"] is False
    assert pase_client.mando["acceso_al_cobrar"] is False


def test_nadie_tiene_acceso_en_el_instante_del_cobro(pase_client, pase_cobro_ok):
    # El estado final "active" no basta para probar el orden: las ramas de fallo lo vuelven a
    # dejar en "failed"/"pending" pase lo que pase antes. Mirar el ACCESO (vía `pase_vigente`, no
    # la columna `status`) es lo único que caza "se activó antes de cobrar" — y de paso cualquier
    # estado nuevo que diera acceso sin llamarse "active".
    pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert pase_client.mando["acceso_al_cobrar"] is False


def test_comprar_dos_veces_APILA(pase_client, pase_cobro_ok):
    pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    with pase_client.session_factory() as s:
        pases = sorted(s.scalars(select(TrackerPass)).all(), key=lambda p: p.starts_at)
        assert pases[1].starts_at == pases[0].ends_at


def test_sin_token_no_se_puede_comprar(pase_client):
    assert pase_client.post("/gacha/tracker-pass", json={"days": 7}).status_code == 401


def test_una_duracion_que_no_vendemos_se_rechaza(pase_client, pase_cobro_ok):
    r = pase_client.post("/gacha/tracker-pass", json={"days": 1}, headers=pase_client.hdrs)
    assert r.status_code == 422


def test_con_el_precio_APAGADO_la_compra_no_existe(pase_precio_apagado):
    r = pase_precio_apagado.post("/gacha/tracker-pass", json={"days": 7},
                                 headers=pase_precio_apagado.hdrs)
    assert r.status_code == 503
    # No basta con el código: un 503 por "privy no configurado" (el bug que tenía este test antes
    # de esta revisión, con una app construida sin `privy=`) también es 503, y ese no prueba nada
    # sobre el precio. `detail` es lo único que distingue los dos.
    assert r.json()["detail"] == "tracker_pass_disabled"


# ── I1: dos compras a la vez de la misma wallet ──────────────────────────────────────────────


def _pending(**over):
    """Una fila `pending` mínima para sembrar directamente en la base, sin pasar por el
    endpoint. Simula "ya hay una compra en curso" sin depender de concurrencia real."""
    ahora = datetime.now(timezone.utc)
    base = dict(id="ya-en-curso", wallet=TRACKER_PASS_WALLET, days=7, price_base_units=10_000_000,
               status="pending", starts_at=ahora, ends_at=ahora + timedelta(days=7))
    base.update(over)
    return TrackerPass(**base)


def test_ya_hay_una_pending_devuelve_409_y_no_cobra_otra_vez(pase_client, pase_cobro_ok):
    # El caso normal de esto es un doble clic: la primera petición todavía está a medio cobrar
    # cuando llega la segunda. Sembrar la fila a mano representa ese instante sin necesitar dos
    # hilos de verdad.
    with pase_client.session_factory() as s:
        s.add(_pending())
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    assert r.json()["detail"] == "tracker_pass_pending"
    with pase_client.session_factory() as s:
        # Ni una fila nueva, ni un cobro: el 409 salió antes de tocar la cadena.
        assert len(s.scalars(select(TrackerPass)).all()) == 1
    assert pase_client.mando["acceso_al_cobrar"] is None, "collect_buyin no se llegó a invocar"


def test_el_indice_unico_impide_dos_pending_a_la_vez(pase_client):
    # Esta es la parte ATÓMICA de la garantía: el check de la app de arriba es un
    # check-then-act (SELECT y luego INSERT) y por sí solo no cierra la carrera entre dos
    # peticiones que pasan la comprobación casi a la vez. El índice único parcial de app/db.py
    # (uq_tracker_passes_pending_wallet) es lo que de verdad lo impide, a nivel de base de datos.
    with pase_client.session_factory() as s:
        s.add(_pending(id="uno"))
        s.commit()
        s.add(_pending(id="dos"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_la_carrera_de_verdad_tambien_devuelve_409(pase_client, pase_cobro_ok, monkeypatch):
    # El test de arriba (`test_ya_hay_una_pending...`) prueba el atajo: una `pending` que YA
    # estaba antes de que llegara la petición. Este prueba la carrera de verdad: la petición pasa
    # el SELECT sin ver nada (todavía no hay ninguna `pending`), y justo DESPUÉS —aprovechando el
    # hueco de `_require_available`, que es lo siguiente que el endpoint hace— se cuela otra
    # compra de la misma wallet. Es la única forma de ejercitar la rama `except IntegrityError`
    # del endpoint sin dos hilos de verdad: ningún otro test la toca.
    import app.main as m

    async def _saldo_que_cuela_una_pending(*a, **k):
        with pase_client.session_factory() as s2:
            s2.add(_pending(id="se-coló"))
            s2.commit()
        return pase_client.mando["saldo"]

    monkeypatch.setattr(m, "usdc_balance_base_units", _saldo_que_cuela_una_pending)

    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    assert r.json()["detail"] == "tracker_pass_pending"
    with pase_client.session_factory() as s:
        assert len(s.scalars(select(TrackerPass)).all()) == 1


# ── B: una `pending` CON firma se reconcilia sola en la siguiente compra ────────────────────


def test_una_pending_CON_firma_se_autorreconcilia_a_ACTIVA_y_el_usuario_sigue(pase_client,
                                                                              pase_cobro_ok):
    # `mando["confirma"]` por defecto es True: al preguntar otra vez, la cadena dice que sí se
    # confirmó. La vieja se cierra sola en `active` y la compra de ESTE request sigue su camino
    # sin que nadie tenga que mirarla a mano.
    with pase_client.session_factory() as s:
        s.add(_pending(tx_signature="FirmaVieja"))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    with pase_client.session_factory() as s:
        filas = {p.id: p.status for p in s.scalars(select(TrackerPass)).all()}
    assert filas["ya-en-curso"] == "active"    # la vieja, reconciliada sola
    assert len(filas) == 2                     # + la fila de la compra de este request


def test_una_pending_CON_firma_RECHAZADA_se_cierra_y_no_bloquea(pase_client, pase_cobro_sin_confirmar):
    # `mando["confirma"]` es False: la cadena dice que se ejecutó y falló. La vieja se cierra
    # sola en `failed` — sin firma que reconciliar más, sin acceso que regalar — y la compra de
    # este request NO se bloquea con un 409 por su culpa (aunque, con este mismo `confirma`,
    # termine fallando también, por su cuenta).
    with pase_client.session_factory() as s:
        s.add(_pending(tx_signature="FirmaVieja"))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code != 409, "la vieja se reconcilia sola: no debe bloquear la compra nueva"
    with pase_client.session_factory() as s:
        vieja = s.get(TrackerPass, "ya-en-curso")
        assert vieja.status == "failed"


def test_una_pending_CON_firma_que_SIGUE_indeterminada_no_se_resuelve(pase_client,
                                                                      pase_confirmacion_indeterminada):
    # `mando["confirma"]` sigue siendo None: preguntar otra vez no cambia nada, porque la cadena
    # de verdad tampoco lo sabría todavía. Se queda `pending`, y el 409 sigue en pie — esto no se
    # puede reconciliar solo, cada vez que se intenta.
    with pase_client.session_factory() as s:
        s.add(_pending(tx_signature="FirmaVieja"))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    with pase_client.session_factory() as s:
        vieja = s.get(TrackerPass, "ya-en-curso")
        assert vieja.status == "pending"


def test_una_pending_SIN_firma_no_intenta_reconciliar(pase_client, pase_cobro_ok):
    # Sin firma no hay nada que preguntarle a la cadena: `confirmar_firma` ni se llama. Esta es
    # justo la única `pending` que de verdad necesita que la mire una persona.
    with pase_client.session_factory() as s:
        s.add(_pending())     # tx_signature=None por defecto
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    with pase_client.session_factory() as s:
        vieja = s.get(TrackerPass, "ya-en-curso")
        assert vieja.status == "pending"


# ── D: el 409 distingue "espera un momento" de "esto está atascado" ─────────────────────────


def test_una_pending_RECIEN_creada_da_el_409_normal(pase_client, pase_cobro_ok):
    with pase_client.session_factory() as s:
        s.add(_pending())
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    assert r.json()["detail"] == "tracker_pass_pending"


def test_una_pending_VIEJA_da_un_409_distinto(pase_client, pase_cobro_ok):
    # Ni el cobro más lento tarda minutos: pasado el umbral, "espera un momento" ya no es verdad,
    # y el frontend necesita poder decir algo distinto ("esto está atascado, contacta soporte").
    with pase_client.session_factory() as s:
        vieja = datetime.now(timezone.utc) - timedelta(minutes=5)
        s.add(_pending(created_at=vieja))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    assert r.json()["detail"] == "tracker_pass_pending_stuck"
