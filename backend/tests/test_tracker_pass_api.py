"""La compra del pase. Aquí se mueve dinero real de un usuario por primera vez fuera de una
partida, así que lo que más se prueba es el camino de FALLO.

Las fixtures (`pase_entorno` y compañía) están en `tests/conftest.py`, porque la tarea 7 las
reutiliza.

DOS COSAS QUE ESTE FICHERO PROTEGE Y QUE NO SE VEN LEYENDO UN SOLO TEST:

  · Nada de lo que ocurre ANTES del envío deja fila. Construir y firmar pueden reventar por
    configuración —una dirección o un mint mal escritos, un blockhash ininteligible— y eso no
    depende de quién compra: si dejara fila, encerraría a TODAS las wallets en su primer intento,
    que es lo que pasó tres rondas seguidas. Se comprueba con `mando["filas_al_firmar"]`, y
    además con construcción REAL (sin mockear) en la última sección.
  · Toda `pending` tiene firma. Se comprueba con `mando["firma_en_la_fila_al_enviar"]`: en el
    instante en que el dinero se mueve, la fila ya existe y ya sabe qué transacción mirar.
"""
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


# ── El orden: primero firmar, luego escribir la fila ─────────────────────────────────────────


def test_al_construir_y_firmar_TODAVIA_no_hay_ninguna_fila(pase_client, pase_cobro_ok):
    # La invariante que hace imposible por construcción la familia de fallos que encerró wallets
    # tres rondas seguidas. Mientras se construye y se firma no hay fila, así que no hay candado
    # que liberar y da igual de qué tipo sea la excepción que salte ahí.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    assert pase_client.mando["filas_al_firmar"] == 0


def test_al_enviar_la_fila_YA_existe_y_YA_tiene_su_firma(pase_client, pase_cobro_ok):
    # La otra mitad del mismo orden: en cuanto el dinero puede moverse, la fila existe y sabe qué
    # transacción mirar. Es lo que hace que NO exista una `pending` sin firma, que era la única
    # celda de esta tabla que necesitaba que la mirase una persona.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    assert pase_client.mando["firma_en_la_fila_al_enviar"], "la fila nació ya con la firma puesta"


def test_nadie_tiene_acceso_en_el_instante_del_cobro(pase_client, pase_cobro_ok):
    # El estado final "active" no basta para probar el orden: las ramas de fallo lo vuelven a
    # dejar en "failed"/"pending" pase lo que pase antes. Mirar el ACCESO (vía `pase_vigente`, no
    # la columna `status`) es lo único que caza "se activó antes de cobrar" — y de paso cualquier
    # estado nuevo que diera acceso sin llamarse "active".
    pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert pase_client.mando["acceso_al_cobrar"] is False


# ── Fallos ANTES del envío: ni una fila, y reintentable al momento ───────────────────────────


def test_un_fallo_al_pedir_el_blockhash_NO_deja_NINGUNA_fila(pase_client, pase_blockhash_revienta):
    # Pedir el blockhash es lo primero que toca la red y ocurre antes de que exista fila. Antes de
    # esta ronda esto dependía de un `except` que lo clasificara bien; ahora es estructural.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []

    # Y el reintento funciona: arreglado el RPC, la wallet NO queda encerrada.
    pase_client.mando["blockhash"] = "11111111111111111111111111111111"
    r2 = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r2.status_code == 200, r2.text


def test_una_firma_RECHAZADA_por_privy_NO_deja_NINGUNA_fila(pase_client, pase_firma_rechazada):
    # PrivySignerError al firmar: nada se ha difundido y no hay fila que limpiar.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []


def test_un_ValueError_al_CONSTRUIR_NO_deja_NINGUNA_fila(pase_client, pase_construccion_revienta):
    # El `ValueError` de solders es la excepción concreta que encerró wallets en la ronda 3, y la
    # que NO se puede meter en un bucket "determinado" a la ligera, porque `json.JSONDecodeError`
    # —que sí puede venir de después de un envío— hereda de ella. Ya no hace falta distinguirlas:
    # esta pasa antes de que exista fila, y la otra pasa después de que la fila tenga firma.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []
    assert pase_client.mando["acceso_al_cobrar"] is None, "no se llegó a enviar nada"


# ── Fallos DESDE el envío: la fila existe, y siempre con firma ───────────────────────────────


def test_un_envio_RECHAZADO_no_da_acceso_y_deja_la_fila_en_failed(pase_client,
                                                                   pase_envio_rechazado):
    # RuntimeError: rechazo DEFINITIVO del RPC (ver `submit_signed_tx`). El dinero no se movió,
    # así que la fila puede cerrarse en `failed` — y eso además libera el candado al momento.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "failed"
        assert p.tx_signature, "aun rechazada, se sabe qué transacción era"
    acc = pase_client.get("/gacha/tracker-access", headers=pase_client.hdrs).json()
    assert acc["allowed"] is False
    # Nadie tuvo acceso ni siquiera en el instante en que se intentó cobrar.
    assert pase_client.mando["acceso_al_cobrar"] is False


def test_un_envio_INDETERMINADO_deja_la_fila_pending_CON_firma(pase_client,
                                                                pase_envio_indeterminado):
    # Un timeout, un 5xx del proxy tras reenviar... no sabemos si la transacción salió. `failed`
    # aquí mentiría "seguro que no". Se queda `pending`, pero —y esta es la diferencia de esta
    # ronda— CON su firma: ya no es una fila que necesite que la mire una persona, es una que la
    # siguiente compra de esta wallet reconcilia sola.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "pending"
        assert p.tx_signature, "se sabe qué transacción mirar en un explorador"
    acc = pase_client.get("/gacha/tracker-access", headers=pase_client.hdrs).json()
    assert acc["allowed"] is False
    assert pase_client.mando["acceso_al_cobrar"] is False


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
    # `pending` CON la firma, que es lo que la siguiente compra reconcilia sola.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 502
    with pase_client.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "pending"
        assert p.tx_signature, "se sabe qué transacción mirar en un explorador"
    acc = pase_client.get("/gacha/tracker-access", headers=pase_client.hdrs).json()
    assert acc["allowed"] is False
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
    # de una revisión anterior, con una app construida sin `privy=`) también es 503, y ese no
    # prueba nada sobre el precio. `detail` es lo único que distingue los dos.
    assert r.json()["detail"] == "tracker_pass_disabled"


# ── El freno de peticiones ───────────────────────────────────────────────────────────────────


def test_pasado_el_limite_de_compras_por_ventana_se_corta_con_un_429(pase_client,
                                                                      pase_blockhash_revienta):
    # Mueve dinero igual que `/withdraw` y `/tip`, así que lleva el mismo freno. Se usa una ruta
    # que FALLA antes de escribir nada (y por tanto no deja candado ni pase que estorbe) para que
    # lo único que corte los intentos sea el freno, y no un 409 ni un pase ya comprado.
    for i in range(5):                       # tracker_pass_rate_limit por defecto
        r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
        assert r.status_code == 502, f"intento {i}: {r.text}"
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 429
    with pase_client.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []


# ── I1: dos compras a la vez de la misma wallet ──────────────────────────────────────────────


def _pending(**over):
    """Una fila `pending` mínima para sembrar directamente en la base, sin pasar por el
    endpoint. Simula "ya hay una compra en curso" sin depender de concurrencia real. Ojo: sin
    `tx_signature` explícito nace SIN firma, que es un estado que el endpoint ya no produce nunca
    — solo puede venir de una fila vieja anterior a esta ronda, o de un `INSERT` a mano."""
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
    assert pase_client.mando["filas_al_firmar"] is None, "ni se llegó a construir la transacción"


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
    # del endpoint sin dos hilos de verdad: ningún otro test la toca. Lo que se tira al perder la
    # carrera es una transacción ya firmada y nunca enviada: no mueve dinero.
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
    assert pase_client.mando["acceso_al_cobrar"] is None, "nunca se envió nada"


# ── B: una `pending` CON firma se reconcilia sola en la siguiente compra ────────────────────


def test_una_pending_CON_firma_se_autorreconcilia_a_ACTIVA_y_NO_cobra_otra_vez(pase_client,
                                                                                pase_cobro_ok):
    # El jugador que ve un 502 y reintenta (la reacción normal ante un error) NO puede pagar un
    # segundo pase encima del primero, que sí se cobró. `mando["confirma"]` por defecto es True:
    # al preguntar otra vez, la cadena dice que sí se confirmó, así que la vieja se cierra sola en
    # `active` y ESTA petición devuelve ESE pase, sin cobrar nada más.
    with pase_client.session_factory() as s:
        s.add(_pending(tx_signature="FirmaVieja"))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 7
    assert r.json()["price_usdc"] == 10.0
    with pase_client.session_factory() as s:
        filas = {p.id: p.status for p in s.scalars(select(TrackerPass)).all()}
    assert filas == {"ya-en-curso": "active"}, "una sola fila: la vieja, reconciliada"
    assert pase_client.mando["filas_al_firmar"] is None, "nada de cobro nuevo"


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
    # Sin firma no hay nada que preguntarle a la cadena: `confirmar_firma` ni se llama. El
    # endpoint ya no crea filas así (toda `pending` nace con su firma), pero pueden quedar de
    # antes de esta ronda o de un arreglo a mano, y el camino tiene que seguir siendo sensato.
    with pase_client.session_factory() as s:
        s.add(_pending())     # tx_signature=None por defecto
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    with pase_client.session_factory() as s:
        vieja = s.get(TrackerPass, "ya-en-curso")
        assert vieja.status == "pending"
    assert "confirma_llamadas" not in pase_client.mando


# ── D: el 409 distingue "espera un momento" de "esto está atascado" ─────────────────────────


def test_una_pending_RECIEN_creada_da_el_409_normal(pase_client, pase_cobro_ok):
    with pase_client.session_factory() as s:
        s.add(_pending())
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    assert r.json()["detail"] == "tracker_pass_pending"


def test_una_pending_VIEJA_da_un_409_distinto(pase_client, pase_cobro_ok):
    # Ni el cobro más lento llega a esto: pasado el umbral, "espera un momento" ya no es verdad, y
    # el frontend necesita poder decir algo distinto ("esto está atascado, contacta soporte"). 15
    # minutos deja margen de sobra sobre el umbral (300 s) sin acercarse al límite real, para que
    # este test no dependa de lo rápido que corra la suite.
    with pase_client.session_factory() as s:
        vieja = datetime.now(timezone.utc) - timedelta(minutes=15)
        s.add(_pending(created_at=vieja))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    assert r.json()["detail"] == "tracker_pass_pending_stuck"


def test_una_pending_de_dos_minutos_NO_esta_atascada(pase_client, pase_cobro_ok):
    # Con un umbral corto (60 s, que solo contaba las esperas entre reintentos de `confirmar_firma`
    # y se olvidaba de sus propios timeouts y del envío) esto se habría etiquetado "atascada" a
    # pesar de estar cómodamente dentro de lo que puede tardar un cobro legítimo. Mandar a un
    # jugador a soporte por una compra que solo va lenta es peor que no decir nada.
    with pase_client.session_factory() as s:
        vieja = datetime.now(timezone.utc) - timedelta(minutes=2)
        s.add(_pending(created_at=vieja))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 409
    assert r.json()["detail"] == "tracker_pass_pending"


# ── H: la wallet de destino del cobro sin configurar es un problema NUESTRO ─────────────────


def test_fee_dest_vacio_da_un_503_de_configuracion_y_no_deja_rastro(pase_fee_dest_vacio):
    # Ya no es esto lo que evita encerrar wallets (de eso se encarga el orden), pero el código de
    # estado sigue importando: un 503 "misconfigured" manda a quien despliega a mirar su .env, y
    # un 502 "charge failed" lo mandaría a buscar en la cadena un cobro que nunca se intentó.
    r = pase_fee_dest_vacio.post("/gacha/tracker-pass", json={"days": 7},
                                 headers=pase_fee_dest_vacio.hdrs)
    assert r.status_code == 503
    assert r.json()["detail"] == "tracker_pass_misconfigured"
    with pase_fee_dest_vacio.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == [], "nada se escribe: se corta antes"


# ── I: la ventana de una pending reconciliada se corre SIEMPRE ──────────────────────────────


def test_reconciliar_una_ventana_YA_EXPIRADA_la_corre_a_partir_de_ahora(pase_client, pase_cobro_ok):
    # Una `pending` de hace 8 días con ventana de 7: para cuando se reconcilia, `ends_at` ya
    # quedó en el pasado. Activarla tal cual regalaría cero acceso por un pase que sí se cobró.
    with pase_client.session_factory() as s:
        hace_8_dias = datetime.now(timezone.utc) - timedelta(days=8)
        s.add(_pending(tx_signature="FirmaVieja", starts_at=hace_8_dias,
                       ends_at=hace_8_dias + timedelta(days=7)))
        s.commit()
    ahora = time.time()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    assert abs(r.json()["pass_until"] - (ahora + 7 * 86400)) < 5
    with pase_client.session_factory() as s:
        p = s.get(TrackerPass, "ya-en-curso")
        assert p.status == "active"
        fin = p.ends_at if p.ends_at.tzinfo else p.ends_at.replace(tzinfo=timezone.utc)
        inicio = p.starts_at if p.starts_at.tzinfo else p.starts_at.replace(tzinfo=timezone.utc)
        assert fin > datetime.now(timezone.utc), "ya no está expirada"
        assert fin - inicio == timedelta(days=7), "se respetaron los días que se compraron"


def test_reconciliar_una_ventana_A_MEDIAS_TAMBIEN_la_corre(pase_client, pase_cobro_ok):
    # Una `pending` de hace 3 días con ventana de 7. Antes solo se corría la que había caducado
    # ENTERA, con el argumento de que "el jugador ya tiene acceso desde que se creó la fila". Es
    # falso: una fila `pending` NO da acceso (solo `active` lo da), así que esos 3 días son días
    # pagados y no usados. Correrla siempre es más simple y más justo, y en el caso normal —una
    # `pending` de segundos— es un no-op de milisegundos.
    with pase_client.session_factory() as s:
        hace_3_dias = datetime.now(timezone.utc) - timedelta(days=3)
        s.add(_pending(tx_signature="FirmaVieja", starts_at=hace_3_dias,
                       ends_at=hace_3_dias + timedelta(days=7)))
        s.commit()
    ahora = time.time()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    assert abs(r.json()["pass_until"] - (ahora + 7 * 86400)) < 5, "los 7 días, a partir de ahora"


def test_reconciliar_no_solapa_con_un_pase_que_YA_esta_vigente(pase_client, pase_cobro_ok):
    # El límite de "correrla siempre": si esta wallet ya tiene un pase ACTIVO, empezar "ahora"
    # solaparía las dos ventanas y le comería al jugador justo los días que quería apilar. Por eso
    # la ventana se recalcula con `periodo`, que arranca donde acaba el pase vigente.
    with pase_client.session_factory() as s:
        ahora = datetime.now(timezone.utc)
        fin_del_activo = ahora + timedelta(days=4)
        s.add(TrackerPass(id="el-activo", wallet=TRACKER_PASS_WALLET, days=7,
                          price_base_units=10_000_000, status="active",
                          starts_at=ahora - timedelta(days=3), ends_at=fin_del_activo))
        s.add(_pending(tx_signature="FirmaVieja"))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    esperado = (fin_del_activo + timedelta(days=7)).timestamp()
    assert abs(r.json()["pass_until"] - esperado) < 5, "empieza donde acaba el que ya estaba"


# ── K: la autorreconciliación no puede colgarse minutos ──────────────────────────────────────


def test_la_reconciliacion_usa_un_presupuesto_corto(pase_client, pase_cobro_ok):
    # La llamada de la reconciliación (sobre la firma VIEJA) lleva `intentos`/`espera_s` cortos.
    # La confirmación del cobro que se acaba de hacer (sobre la firma NUEVA) TAMBIÉN los lleva
    # cortos hoy (ver `_CONFIRMACION_*` en main.py): los valores por defecto de `confirmar_firma`
    # (unos 213 s) no caben en una petición HTTP normal, y ningún proxy delante los aguanta.
    with pase_client.session_factory() as s:
        s.add(_pending(tx_signature="FirmaVieja"))
        s.commit()
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    llamadas = pase_client.mando["confirma_llamadas"]
    assert len(llamadas) == 1, "solo la reconciliación: se resolvió a active y no cobró de nuevo"
    reconciliacion = llamadas[0]
    assert reconciliacion.get("intentos", 10) < 10
    assert reconciliacion.get("espera_s", 1.5) <= 1.0


def test_la_confirmacion_del_cobro_nuevo_TAMBIEN_usa_un_presupuesto_corto(pase_client,
                                                                          pase_cobro_ok):
    # Sin ninguna `pending` previa que reconciliar, la ÚNICA llamada a `confirmar_firma` es la
    # que confirma la firma RECIÉN enviada. Antes usaba los valores por defecto (10 intentos de
    # 20 s, unos 213 s en total) y ningún proxy delante aguantaba esa espera: una compra que SÍ
    # funcionaba se le enseñaba como un error a quien acababa de pagar. Debe llevar el
    # presupuesto corto de `_CONFIRMACION_*`, igual que la reconciliación.
    r = pase_client.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_client.hdrs)
    assert r.status_code == 200, r.text
    llamadas = pase_client.mando["confirma_llamadas"]
    assert len(llamadas) == 1
    cobro = llamadas[0]
    assert cobro.get("intentos", 10) < 10
    assert cobro.get("espera_s", 1.5) <= 1.0
    assert cobro.get("timeout_s", 20.0) < 20.0


# ── Construcción REAL, sin mockear ───────────────────────────────────────────────────────────
#
# Todo lo de arriba mockea `construir_y_firmar_cobro`, así que ningún test de arriba puede ver un
# `ValueError` de verdad de solders — y ese es exactamente el motivo de que el mismo agujero se
# colara tres rondas seguidas: el mock siempre "construía" bien. Aquí no hay mock: se construye y
# se firma con `build_token_transfer` + `sign_solana` + `leer_firma` de verdad, y lo único
# intervenido es el envío.


def test_un_MINT_mal_escrito_construyendo_DE_VERDAD_no_deja_ninguna_fila(pase_mint_malo):
    # `Pubkey.from_string("no-es-un-mint")` levanta ValueError DENTRO de `build_token_transfer`.
    # Es configuración: no depende de quién compra, así que si dejara fila encerraría a TODAS las
    # wallets en su primer intento.
    r = pase_mint_malo.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_mint_malo.hdrs)
    assert r.status_code == 502
    with pase_mint_malo.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []
    # Y el segundo intento vuelve a fallar IGUAL — no con un 409. Que el error se repita es lo
    # correcto (la configuración sigue mal); que NO se convierta en un candado es lo que se prueba.
    r2 = pase_mint_malo.post("/gacha/tracker-pass", json={"days": 7}, headers=pase_mint_malo.hdrs)
    assert r2.status_code == 502
    with pase_mint_malo.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []


def test_un_OPERADOR_mal_escrito_construyendo_DE_VERDAD_no_deja_ninguna_fila(pase_operador_mal_escrito):
    # La trampa exacta de la ronda 3: la wallet de DESTINO está bien (así que el 503 de
    # configuración no salta), y la que tiene el typo es la del operador, que va como `fee_payer`
    # y que el endpoint no valida. Revienta al construir, y no deja rastro.
    c = pase_operador_mal_escrito
    r = c.post("/gacha/tracker-pass", json={"days": 7}, headers=c.hdrs)
    assert r.status_code == 502
    with c.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == []
    r2 = c.post("/gacha/tracker-pass", json={"days": 7}, headers=c.hdrs)
    assert r2.status_code == 502, "sigue roto, pero la wallet no está encerrada"


def test_un_blockhash_MALFORMADO_de_verdad_no_deja_fila_y_el_reintento_FUNCIONA(pase_construccion_real):
    # `Hash.from_string` levanta `ParseHashError` dentro de `build_token_transfer`. A diferencia
    # de los dos de arriba, esto sí se arregla solo en cuanto el RPC vuelve a dar un blockhash
    # bueno — y entonces la compra sale entera: construir, firmar y leer la firma, todo de verdad.
    c = pase_construccion_real
    c.mando["blockhash"] = "no-es-un-blockhash"
    r = c.post("/gacha/tracker-pass", json={"days": 7}, headers=c.hdrs)
    assert r.status_code == 502
    with c.session_factory() as s:
        assert s.scalars(select(TrackerPass)).all() == [], "ni una fila que haga de candado"

    c.mando["blockhash"] = "11111111111111111111111111111111"
    r2 = c.post("/gacha/tracker-pass", json={"days": 7}, headers=c.hdrs)
    assert r2.status_code == 200, r2.text
    with c.session_factory() as s:
        p = s.scalars(select(TrackerPass)).one()
        assert p.status == "active"
        # La firma es la que salió de firmar de verdad, leída de los bytes de la transacción: 64
        # bytes en base58, nunca la de ceros (`1111…`) de una transacción sin firmar.
        assert p.tx_signature and set(p.tx_signature) != {"1"}
