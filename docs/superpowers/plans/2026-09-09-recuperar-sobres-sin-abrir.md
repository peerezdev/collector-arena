# Recuperar sobres de gacha sin abrir — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un sobre pagado y nunca abierto se pueda recuperar desde nuestra app, aunque se haya comprado en la web de Collector Crypt y nosotros no nos enteráramos.

**Architecture:** Un endpoint nuevo le pregunta a CC por los sobres sin abrir de la wallet del token, crea la fila `GachaPack` de los que no tengamos con `submitted_at` puesto, y con eso el sobre entra en la recuperación de pendientes que YA existe. El frontend solo aporta un botón que llama y avisa al bus; AppShell hace el resto.

**Tech Stack:** FastAPI + SQLAlchemy síncrono (backend), React + vitest (frontend), pytest con respx para el upstream.

**Spec:** `docs/superpowers/specs/2026-09-09-recuperar-sobres-sin-abrir-design.md`

## Global Constraints

- **La wallet sale SIEMPRE del token de identidad** (`Depends(current_user)`), nunca de un parámetro ni del cuerpo. Es lo único que impide que alguien nos haga adoptar el sobre de otro y, con ello, abrirlo y ver su carta.
- **Solo se adopta lo recuperable:** pago `confirmed`, sin `refund_transaction_signature`, y con memo.
- **`submitted_at` es obligatorio al crear la fila.** Es el campo que decide si el sobre sale en pendientes; sin él la adopción no se ve y el arreglo es invisible.
- **Nunca se pisa una fila existente.** Pulsar dos veces no duplica ni sobrescribe.
- **`pack_type` de un sobre adoptado es `cc_<coste>`**, literal. No se adivina la máquina.
- **Si CC falla: 502 y CERO filas escritas.**
- Nada de guiones largos (—) en el copy que ve el usuario.
- Comentarios en castellano, explicando el porqué.

---

### Task 1: El servicio pregunta a CC por los sobres sin abrir

**Files:**
- Modify: `backend/app/services/gacha.py` (método nuevo, junto a `free_spins`, línea ~213)
- Test: `backend/tests/test_gacha.py` (al final)

**Interfaces:**
- Consumes: `GachaService._request(method, path, json=None, params=None)`, que ya existe y lanza `GachaUpstreamError` cuando el upstream falla.
- Produces: `async def unopened_packs(self, wallet: str) -> list[dict]`, cada elemento
  `{"memo": str, "cost": float, "paid_at": str | None}`. Lo consume la Task 2.

- [ ] **Step 1: Escribir los tests que fallan**

Al final de `backend/tests/test_gacha.py`:

```python
@respx.mock
@pytest.mark.asyncio
async def test_unopened_packs_pide_la_lista_de_esa_wallet_y_se_queda_con_tres_campos():
    """La respuesta de CC trae una veintena de campos y solo usamos tres.

    Depender del resto sería atarnos a un esquema de un tercero que no controlamos y que ya
    cambió una vez (el nonce de generateFreePack).
    """
    from app.services.gacha import GachaService

    ruta = respx.get(f"{BASE}/api/userStats").mock(return_value=Response(200, json={
        "success": True,
        "data": {"linkedAll": [{
            "id": 8947390,
            "memo": "cc-uno",
            "spin_cost": 25,
            "spin_txn_created_at": "2026-09-09T17:47:48.525Z",
            "spin_status": "confirmed",
            "spin_webhook_received": True,
            "refund_transaction_signature": None,
            "send_nft_address": None,
            "hashlist_metadata": None,
        }], "pagination": {"hasMore": False, "lastId": None}},
    }))

    out = await GachaService(base_url=BASE, api_key="k").unopened_packs("WalletA")

    assert out == [{"memo": "cc-uno", "cost": 25.0, "paid_at": "2026-09-09T17:47:48.525Z"}]
    # La consulta tiene que ir por la wallet pedida y por el filtro de sin abrir: sin `show`
    # devolvería el historial ENTERO y adoptaríamos sobres ya abiertos.
    q = ruta.calls.last.request.url.params
    assert q["wallet"] == "WalletA"
    assert q["show"] == "unopened"
    assert q["data"] == "linkedAll"


@respx.mock
@pytest.mark.asyncio
async def test_unopened_packs_descarta_lo_que_no_se_puede_recuperar():
    """Un reembolsado o uno con el pago sin cuajar no tienen nada que abrir.

    Adoptarlos le prometería al jugador una carta que no va a llegar nunca.
    """
    from app.services.gacha import GachaService

    respx.get(f"{BASE}/api/userStats").mock(return_value=Response(200, json={"data": {"linkedAll": [
        {"memo": "cc-bueno", "spin_cost": 25, "spin_status": "confirmed",
         "refund_transaction_signature": None},
        {"memo": "cc-reembolsado", "spin_cost": 25, "spin_status": "confirmed",
         "refund_transaction_signature": "firma-del-reembolso"},
        {"memo": "cc-sin-cuajar", "spin_cost": 25, "spin_status": "pending",
         "refund_transaction_signature": None},
        {"memo": None, "spin_cost": 25, "spin_status": "confirmed",
         "refund_transaction_signature": None},
    ]}}))

    out = await GachaService(base_url=BASE, api_key="k").unopened_packs("WalletA")

    assert [p["memo"] for p in out] == ["cc-bueno"]


@respx.mock
@pytest.mark.asyncio
async def test_unopened_packs_aguanta_una_respuesta_rara_sin_reventar():
    """Es un endpoint no documentado: si un día contesta otra cosa, esto no puede tumbar la
    pantalla. Sin lista que leer, no hay sobres que adoptar y ya está."""
    from app.services.gacha import GachaService
    svc = GachaService(base_url=BASE, api_key="k")

    respx.get(f"{BASE}/api/userStats").mock(return_value=Response(200, json={"data": None}))
    assert await svc.unopened_packs("WalletA") == []

    respx.reset()
    respx.get(f"{BASE}/api/userStats").mock(return_value=Response(200, json=[1, 2, 3]))
    assert await svc.unopened_packs("WalletA") == []


@respx.mock
@pytest.mark.asyncio
async def test_unopened_packs_propaga_el_fallo_del_upstream():
    """Un CC caído NO puede parecerse a "no tienes sobres": eso le diría al jugador que su sobre
    no existe justo cuando no hemos podido preguntar."""
    from app.services.gacha import GachaService, GachaUpstreamError

    respx.get(f"{BASE}/api/userStats").mock(return_value=Response(500, json={"error": "boom"}))
    with pytest.raises(GachaUpstreamError):
        await GachaService(base_url=BASE, api_key="k").unopened_packs("WalletA")
```

`BASE`, `respx` y `Response` ya están importados arriba en ese fichero. Comprueba antes de añadir imports duplicados.

- [ ] **Step 2: Ejecutarlos y ver que fallan**

Run: `cd backend && ./.venv/bin/pytest tests/test_gacha.py -k unopened -v`
Expected: FAIL con `AttributeError: 'GachaService' object has no attribute 'unopened_packs'`

- [ ] **Step 3: Implementar el método**

En `backend/app/services/gacha.py`, justo después de `free_spins`:

```python
    #: Cuántos sobres sin abrir se piden de una vez. Alguien con más de cien atascados es un
    #: incidente que se mira a mano, no un caso de diseño, así que no se pagina.
    MAX_SIN_ABRIR = 100

    async def unopened_packs(self, wallet: str) -> list[dict]:
        """Sobres que esa wallet PAGÓ y nunca abrió, según CC. Endpoint NO documentado.

        Es la lista que hay detrás de su página `/user/packs/unopened`, y hace falta porque
        nuestra propia tabla solo conoce los sobres que originamos nosotros: uno comprado en la
        web de CC es, para nuestra app, invisible e inabrible.

        Se queda con lo RECUPERABLE y tira el resto: un sobre reembolsado o con el pago sin
        cuajar no tiene nada que abrir, y adoptarlo le prometería al jugador una carta que no va
        a llegar. De los veintitantos campos que devuelve CC solo se leen tres; depender de los
        demás sería atarnos a un esquema que no controlamos.

        Una respuesta con otra forma se trata como lista vacía. Un fallo del upstream SÍ se
        propaga: que CC esté caído no puede parecerse a "no tienes sobres".
        """
        raw = await self._request("GET", "/api/userStats", params={
            "wallet": wallet, "data": "linkedAll", "count": self.MAX_SIN_ABRIR,
            "show": "unopened",
        })
        datos = raw.get("data") if isinstance(raw, dict) else None
        filas = datos.get("linkedAll") if isinstance(datos, dict) else None
        if not isinstance(filas, list):
            return []
        out = []
        for f in filas:
            if not isinstance(f, dict):
                continue
            memo = f.get("memo")
            if not memo or f.get("spin_status") != "confirmed":
                continue
            if f.get("refund_transaction_signature"):
                continue
            try:
                coste = float(f.get("spin_cost") or 0)
            except (TypeError, ValueError):
                coste = 0.0
            out.append({"memo": memo, "cost": coste,
                        "paid_at": f.get("spin_txn_created_at")})
        return out
```

- [ ] **Step 4: Ejecutarlos y ver que pasan**

Run: `cd backend && ./.venv/bin/pytest tests/test_gacha.py -v`
Expected: PASS (los cuatro nuevos y los que ya había)

- [ ] **Step 5: Comprobar que los tests valen (mutación)**

Cada cambio: ejecuta, comprueba que FALLA, y **deshaz**. Usa un editor, NUNCA `git checkout`, que borraría la implementación entera.

1. Quita `"show": "unopened"` de los params. Debe fallar el primer test.
2. Cambia `if f.get("refund_transaction_signature"): continue` por `pass`. Debe fallar el de descartes.
3. Cambia `f.get("spin_status") != "confirmed"` por `f.get("spin_status") == "cancelled"`. Debe fallar el de descartes.

Si alguna mutación NO pone rojo, el test no protege nada: arréglalo antes de seguir.

- [ ] **Step 6: Commit**

```bash
cd /Users/mauro/Desarrollos/BattleArena
git add backend/app/services/gacha.py backend/tests/test_gacha.py
git commit -m "feat(gacha): preguntar a CC por los sobres sin abrir de una wallet"
```

---

### Task 2: El endpoint que adopta

**Files:**
- Modify: `backend/app/main.py` (endpoint nuevo, justo DESPUÉS de `gacha_pending_packs`, línea ~952)
- Test: `backend/tests/test_gacha_api.py` (al final)

**Interfaces:**
- Consumes: `GachaService.unopened_packs(wallet) -> list[dict]` con `{memo, cost, paid_at}` (Task 1);
  `_gacha_or_503()`, `_gacha_throttle(wallet)`, `current_user`, el modelo `GachaPack`.
- Produces: `POST /gacha/packs/reconcile`, que responde **con la misma forma exacta que
  `GET /gacha/packs/pending`**: una lista de
  `{memo, pack_type, submitted_at, nft_address, name, rarity, insured_value, auto_sold, buyback_amount}`.
  Lo consume la Task 3.

Nota: el bloque que arma esa respuesta ya existe dentro de `gacha_pending_packs`. Para no tener
dos copias que se desincronicen, **extrae ese `return [...]` a una función interna**
`_pendientes(s, wallet) -> list[dict]` dentro de `create_app`, y haz que los dos endpoints la
llamen. Es la misma razón por la que `pendingToResult` vive en un solo sitio en el frontend.

- [ ] **Step 1: Escribir los tests que fallan**

Al final de `backend/tests/test_gacha_api.py`:

```python
def _cc_sin_abrir(filas):
    """CC responde con esos sobres sin abrir."""
    return respx.get(f"{BASE}/api/userStats").mock(
        return_value=Response(200, json={"data": {"linkedAll": filas}}))


def _fila_cc(memo, cost=25, paid="2026-09-09T17:47:48.525Z"):
    return {"memo": memo, "spin_cost": cost, "spin_txn_created_at": paid,
            "spin_status": "confirmed", "refund_transaction_signature": None}


@respx.mock
def test_reconcile_adopta_un_sobre_que_nunca_pasó_por_nosotros():
    """El caso real: se compró en la web de CC, así que no tenemos fila y hoy es inabrible."""
    client, priv = _client()
    _cc_sin_abrir([_fila_cc("cc-huerfano")])

    r = client.post("/gacha/packs/reconcile", headers=_hdrs(priv, WALLET_A))

    assert r.status_code == 200, r.text
    assert [p["memo"] for p in r.json()] == ["cc-huerfano"]
    with client.session_factory() as s:
        fila = s.get(GachaPack, "cc-huerfano")
        assert fila.wallet == WALLET_A
        # Sin submitted_at el sobre NO sale en pendientes, así que la adopción sería invisible.
        assert fila.submitted_at is not None
        # No se adivina la máquina: varias cuestan lo mismo (pokemon_25 y comic_25).
        assert fila.pack_type == "cc_25"


@respx.mock
def test_reconcile_es_idempotente():
    """Pulsar dos veces no puede duplicar ni pisar lo que ya había."""
    client, priv = _client()
    _cc_sin_abrir([_fila_cc("cc-uno")])

    client.post("/gacha/packs/reconcile", headers=_hdrs(priv, WALLET_A))
    with client.session_factory() as s:
        s.get(GachaPack, "cc-uno").name = "Carta ya guardada"
        s.commit()
    r = client.post("/gacha/packs/reconcile", headers=_hdrs(priv, WALLET_A))

    assert r.status_code == 200
    with client.session_factory() as s:
        assert s.query(GachaPack).filter_by(memo="cc-uno").count() == 1
        assert s.get(GachaPack, "cc-uno").name == "Carta ya guardada"


@respx.mock
def test_reconcile_IGNORA_la_wallet_que_mande_el_cliente():
    """La propiedad de seguridad de todo esto.

    Si la wallet se pudiera pedir, cualquiera adoptaría los sobres de otro y, al abrirlos, vería
    su carta antes que él: `POST /api/openPack` solo necesita el memo. La wallet sale del token.
    """
    client, priv = _client()
    ruta = _cc_sin_abrir([_fila_cc("cc-de-A")])

    r = client.post("/gacha/packs/reconcile",
                    json={"wallet": WALLET_B, "memo": "cc-de-otro"},
                    headers=_hdrs(priv, WALLET_A))

    assert r.status_code == 200, r.text
    # Se preguntó por la wallet DEL TOKEN, no por la del cuerpo.
    assert ruta.calls.last.request.url.params["wallet"] == WALLET_A
    with client.session_factory() as s:
        assert s.get(GachaPack, "cc-de-otro") is None
        assert s.get(GachaPack, "cc-de-A").wallet == WALLET_A


@respx.mock
def test_reconcile_con_CC_caido_no_escribe_nada():
    """Un fallo a medias sería peor que no hacer nada: filas de sobres que no sabemos si existen."""
    client, priv = _client()
    respx.get(f"{BASE}/api/userStats").mock(return_value=Response(500, json={"error": "boom"}))

    r = client.post("/gacha/packs/reconcile", headers=_hdrs(priv, WALLET_A))

    assert r.status_code == 502
    with client.session_factory() as s:
        assert s.query(GachaPack).count() == 0


@respx.mock
def test_reconcile_devuelve_tambien_los_pendientes_que_ya_teniamos():
    """El cliente pinta UNA lista, así que la respuesta tiene que traerlo todo.

    Si solo devolviera lo recién adoptado, un jugador con un pendiente nuestro y otro de CC vería
    desaparecer el primero al pulsar el botón.
    """
    client, priv = _client()
    with client.session_factory() as s:
        s.add(GachaPack(memo="cc-nuestro", wallet=WALLET_A, pack_type="pokemon_50",
                        submitted_at=datetime.now(timezone.utc)))
        s.commit()
    _cc_sin_abrir([_fila_cc("cc-de-cc")])

    r = client.post("/gacha/packs/reconcile", headers=_hdrs(priv, WALLET_A))

    assert sorted(p["memo"] for p in r.json()) == ["cc-de-cc", "cc-nuestro"]


@respx.mock
def test_un_sobre_que_el_jugador_YA_VIO_no_reaparece():
    """CC lo sigue listando como sin abrir en algunos casos, pero para el jugador ya está visto.

    Sin esto, cada pulsación le devolvería a la cara una carta que ya conoce, y el modal de
    recuperación se abriría solo con algo viejo.
    """
    client, priv = _client()
    ahora = datetime.now(timezone.utc)
    with client.session_factory() as s:
        s.add(GachaPack(memo="cc-ya-visto", wallet=WALLET_A, pack_type="pokemon_50",
                        submitted_at=ahora, opened_at=ahora, revealed_at=ahora,
                        nft_address="MintYaVisto"))
        s.commit()
    _cc_sin_abrir([_fila_cc("cc-ya-visto")])

    r = client.post("/gacha/packs/reconcile", headers=_hdrs(priv, WALLET_A))

    assert r.json() == []
    with client.session_factory() as s:
        # Y su fila sigue intacta: la carta que ya tiene no se pierde.
        assert s.get(GachaPack, "cc-ya-visto").nft_address == "MintYaVisto"


@respx.mock
def test_reconcile_no_toca_los_sobres_de_otra_wallet():
    client, priv = _client()
    with client.session_factory() as s:
        s.add(GachaPack(memo="cc-de-B", wallet=WALLET_B, pack_type="pokemon_50",
                        submitted_at=datetime.now(timezone.utc)))
        s.commit()
    _cc_sin_abrir([])

    r = client.post("/gacha/packs/reconcile", headers=_hdrs(priv, WALLET_A))

    assert r.json() == []
    with client.session_factory() as s:
        assert s.get(GachaPack, "cc-de-B").wallet == WALLET_B
```

`datetime`, `timezone`, `respx`, `Response`, `GachaPack`, `_client`, `_hdrs`, `WALLET_A` y
`WALLET_B` ya están arriba en ese fichero.

- [ ] **Step 2: Ejecutarlos y ver que fallan**

Run: `cd backend && ./.venv/bin/pytest tests/test_gacha_api.py -k reconcile -v`
Expected: FAIL con 404 (la ruta no existe todavía)

- [ ] **Step 3: Extraer la respuesta de pendientes a una función**

En `backend/app/main.py`, dentro de `create_app` y justo ANTES de `gacha_pending_packs`:

```python
    def _pendientes(s: Session, wallet: str) -> list[dict]:
        """Los sobres pagados y todavía no VISTOS por el jugador, como los pinta el cliente.

        Vive aparte porque lo devuelven DOS endpoints (la lista y la reconciliación) y dos copias
        de esta forma se desincronizarían a la primera que añadiera un campo.
        """
        rows = (s.query(GachaPack)
                .filter(GachaPack.wallet == wallet,
                        GachaPack.submitted_at.isnot(None),
                        GachaPack.revealed_at.is_(None))
                .order_by(GachaPack.submitted_at)
                .limit(50)
                .all())
        return [{"memo": p.memo, "pack_type": p.pack_type,
                 "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
                 "nft_address": p.nft_address, "name": p.name,
                 "rarity": p.rarity, "insured_value": p.insured_value,
                 "auto_sold": bool(p.auto_sold), "buyback_amount": p.buyback_amount}
                for p in rows]
```

Y sustituye el cuerpo de `gacha_pending_packs` (desde `rows = (...)` hasta el `return [...]`
completo) por:

```python
        return _pendientes(s, wallet)
```

Deja intacto su docstring, que explica por qué el filtro exige `submitted_at`.

- [ ] **Step 4: Comprobar que no se rompió nada de lo que ya había**

Run: `cd backend && ./.venv/bin/pytest tests/test_gacha_api.py -k pending -v`
Expected: PASS. Es una extracción, así que el comportamiento no cambia.

- [ ] **Step 5: Escribir el endpoint**

En `backend/app/main.py`, justo después de `gacha_pending_packs`:

```python
    @app.post("/gacha/packs/reconcile")
    async def gacha_reconcile_packs(wallet: str = Depends(current_user),
                                    s: Session = Depends(db)):
        """Adopta los sobres que CC dice que esta wallet pagó y nunca abrió.

        Existe porque nuestra tabla solo conoce los sobres que originamos nosotros: uno comprado
        en la web de CC no tiene fila, y sin fila `/gacha/open-pack` responde 403 y
        `/gacha/packs/pending` no lo enseña. O sea, invisible e inabrible.

        LA WALLET SALE DEL TOKEN, y eso no es un detalle de implementación: `POST /api/openPack`
        de CC solo necesita el memo, así que quien consiguiera que adoptáramos el sobre de otro
        podría abrirlo y ver su carta antes que su dueño. Aquí no se lee ninguna wallet ni ningún
        memo del cliente.

        Escribe la fila con `submitted_at`, que es lo que hace que el sobre salga en pendientes y
        entre en la recuperación de siempre: se pulsa una vez y queda adoptado para siempre,
        también desde otro dispositivo.

        `pack_type` queda como `cc_<coste>` porque CC dice el precio y no la máquina, y varias
        cuestan lo mismo. Inventarse cuál era haría que la pantalla enseñara la recompra de otra.
        """
        svc = _gacha_or_503()
        _gacha_throttle(wallet)     # llamada a un tercero disparada por un botón
        try:
            sin_abrir = await svc.unopened_packs(wallet)
        except GachaDisabled:
            raise HTTPException(503, "gacha_disabled")
        except GachaUpstreamError as e:
            # Sin escribir nada: un fallo a medias dejaría filas de sobres que no sabemos si
            # existen, y el jugador puede reintentar sin haber roto nada.
            raise HTTPException(502, str(e) or "gacha upstream unavailable")
        nuevos = 0
        for p in sin_abrir:
            if s.get(GachaPack, p["memo"]) is not None:
                continue        # ya es nuestro: no se pisa lo que pueda tener guardado
            cuando = _fecha_cc(p.get("paid_at"))
            coste = p.get("cost") or 0
            s.add(GachaPack(memo=p["memo"], wallet=wallet,
                            pack_type=f"cc_{int(coste)}", submitted_at=cuando))
            nuevos += 1
        if nuevos:
            s.commit()
            logger.info("reconcile: %s adoptó %d sobre(s) de CC", wallet, nuevos)
        return _pendientes(s, wallet)
```

Y arriba, junto a las demás funciones sueltas del módulo (fuera de `create_app`, cerca de
`_json_safe`):

```python
def _fecha_cc(valor: Optional[str]) -> datetime:
    """La fecha del pago que da CC, o ahora si no se puede leer.

    Se cae a "ahora" a propósito en vez de dejarlo vacío: `submitted_at` es lo que hace visible el
    sobre, así que una fecha rara no puede costarle al jugador la recuperación. Lo único que se
    pierde es el orden en la lista.
    """
    if not valor:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
```

- [ ] **Step 6: Ejecutar y ver que pasan**

Run: `cd backend && ./.venv/bin/pytest tests/test_gacha_api.py -v`
Expected: PASS

- [ ] **Step 7: Comprobar que los tests valen (mutación)**

Cada cambio: ejecuta, comprueba que FALLA, **deshaz** (con el editor, nunca `git checkout`).

1. Cambia la firma a `async def gacha_reconcile_packs(body: dict = Body(default={}), wallet: str = Depends(current_user), ...)` y usa `body.get("wallet") or wallet` en la llamada a `unopened_packs`. Debe fallar `test_reconcile_IGNORA_la_wallet_que_mande_el_cliente`.
2. Quita `submitted_at=cuando` del `GachaPack(...)`. Debe fallar el test de adopción.
3. Quita el `if s.get(GachaPack, p["memo"]) is not None: continue`. Debe fallar el de idempotencia.
4. Cambia el `raise HTTPException(502, ...)` por `sin_abrir = []`. Debe fallar el de CC caído.

- [ ] **Step 8: Commit**

```bash
cd /Users/mauro/Desarrollos/BattleArena
git add backend/app/main.py backend/tests/test_gacha_api.py
git commit -m "feat(gacha): adoptar los sobres sin abrir que CC conoce y nosotros no"
```

---

### Task 3: El botón, en los dos sitios

**Files:**
- Modify: `src/onchain/gachaClient.ts` (junto a `fetchPendingPacks`, línea ~456)
- Create: `src/ui/components/RecoverPacksButton.tsx`
- Create: `src/ui/components/RecoverPacksButton.test.tsx`
- Modify: `src/ui/screens/gacha/GachaVault.tsx` (import arriba; montaje tras el bloque de la máquina, antes del comentario `{/* ── OPEN ERROR BANNER ───` , línea ~708)
- Modify: `src/ui/screens/Profile/InventoryTab.tsx` (import arriba; montaje en la fila de cabecera de `OwnInventory`, dentro del `<div>` con `marginLeft: 'auto'` que hoy contiene el chip "Buyback available", línea ~244)

**Interfaces:**
- Consumes: `POST /gacha/packs/reconcile` (Task 2), que responde una lista con la misma forma que
  `PendingPack`; `notifyPendingPacksChanged()` de `src/ui/screens/gacha/pendingPacksBus`;
  `showToast(msg, kind)` de `src/ui/toastBus`.
- Produces: `reconcilePacks(token): Promise<PendingPack[]>` y el componente `RecoverPacksButton`.

**Por qué el botón no pinta nada de lo que encuentra:** `notifyPendingPacksChanged()` hace que
AppShell recargue los pendientes y, al ver un memo nuevo, abra solo el modal de recuperación con su
ceremonia. Reimplementar ahí ese pintado sería una segunda copia del reveal.

- [ ] **Step 1: Añadir la llamada al cliente**

En `src/onchain/gachaClient.ts`, justo debajo de `fetchPendingPacks`:

```ts
/** Le pregunta al backend por sobres que CC dice que pagaste y nunca abriste, y los adopta.
 *
 *  Hace falta porque un sobre comprado en la web de CC no tiene fila nuestra, y sin fila es a la
 *  vez invisible y no se puede abrir. Devuelve la lista de pendientes ya completa, con lo recién
 *  adoptado y lo que ya teníamos, para que la pantalla pinte UNA sola lista. */
export function reconcilePacks(token: string): Promise<PendingPack[]> {
  return gachaFetch<PendingPack[]>('/gacha/packs/reconcile', {
    method: 'POST', headers: authHeaders(token),
  })
}
```

- [ ] **Step 2: Escribir los tests del botón**

Crear `src/ui/components/RecoverPacksButton.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const reconcilePacks = vi.fn()
// `GachaHttpError` se re-exporta de verdad (no un doble) porque el componente distingue POR
// INSTANCIA: un doble haría que `instanceof` fallara y el test pasaría por el camino equivocado.
vi.mock('../../onchain/gachaClient', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  reconcilePacks: (...a: unknown[]) => reconcilePacks(...a),
}))

const showToast = vi.fn()
vi.mock('../toastBus', () => ({ showToast: (...a: unknown[]) => showToast(...a) }))

const notifyPendingPacksChanged = vi.fn()
vi.mock('../screens/gacha/pendingPacksBus', () => ({
  notifyPendingPacksChanged: () => notifyPendingPacksChanged(),
}))

let token: string | null = 'tok'
vi.mock('@privy-io/react-auth', () => ({ useIdentityToken: () => ({ identityToken: token }) }))

import { RecoverPacksButton } from './RecoverPacksButton'

const pulsar = () => fireEvent.click(screen.getByRole('button', { name: /missing a pack/i }))

describe('RecoverPacksButton', () => {
  beforeEach(() => {
    reconcilePacks.mockReset(); showToast.mockReset(); notifyPendingPacksChanged.mockReset()
    token = 'tok'
  })

  it('al encontrar sobres avisa al bus, que es lo que abre la recuperación', async () => {
    // El botón NO pinta las cartas: recarga los pendientes de AppShell y ese modal hace el resto.
    reconcilePacks.mockResolvedValue([{ memo: 'cc-uno' }])
    render(<RecoverPacksButton />)
    pulsar()
    await waitFor(() => expect(notifyPendingPacksChanged).toHaveBeenCalled())
    expect(reconcilePacks).toHaveBeenCalledWith('tok')
  })

  it('si no hay nada lo DICE, en vez de no hacer nada', async () => {
    // Un botón sin respuesta visible se lee como roto, y este se pulsa justo cuando ya sospechas
    // que algo va mal.
    reconcilePacks.mockResolvedValue([])
    render(<RecoverPacksButton />)
    pulsar()
    await waitFor(() => expect(showToast).toHaveBeenCalled())
    expect(showToast.mock.calls[0][0]).toMatch(/no unopened packs/i)
    expect(notifyPendingPacksChanged).not.toHaveBeenCalled()
  })

  it('un fallo se enseña y se puede reintentar', async () => {
    reconcilePacks.mockRejectedValue(new Error('boom'))
    render(<RecoverPacksButton />)
    pulsar()
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(expect.stringMatching(/could not check/i), 'error'))
    // El botón vuelve a estar disponible: no se queda colgado en "buscando".
    await waitFor(() => expect(screen.getByRole('button', { name: /missing a pack/i })).not.toBeDisabled())
  })

  it('no dispara dos búsquedas si se pulsa dos veces seguidas', async () => {
    let resolver: (v: unknown) => void = () => {}
    reconcilePacks.mockReturnValue(new Promise((r) => { resolver = r }))
    render(<RecoverPacksButton />)
    pulsar()
    pulsar()
    expect(reconcilePacks).toHaveBeenCalledTimes(1)
    resolver([])
  })

  it('sin wallet embebida NO dice "prueba otra vez", que es lo que no lo arregla', async () => {
    // El backend saca el jugador de la wallet embebida del token. Con una sesión de wallet
    // externa el token vale pero no la lleva, así que reintentar repite el 401 para siempre.
    // Mismo mensaje que en las tiradas gratis, que tienen exactamente este problema.
    const { GachaHttpError } = await import('../../onchain/gachaClient')
    reconcilePacks.mockRejectedValue(new GachaHttpError(401, 'no embedded wallet'))
    render(<RecoverPacksButton />)
    pulsar()
    await waitFor(() => expect(showToast).toHaveBeenCalled())
    expect(showToast.mock.calls[0][0]).toMatch(/no in-app wallet/i)
    expect(showToast.mock.calls[0][0]).not.toMatch(/try again/i)
  })

  it('sin sesión no se pinta: no hay wallet a la que preguntar', async () => {
    token = null
    const { container } = render(<RecoverPacksButton />)
    expect(container.firstChild).toBeNull()
  })
})
```

- [ ] **Step 3: Ejecutarlos y ver que fallan**

Run: `npx vitest run src/ui/components/RecoverPacksButton.test.tsx`
Expected: FAIL, no se puede resolver `./RecoverPacksButton`

- [ ] **Step 4: Escribir el componente**

Crear `src/ui/components/RecoverPacksButton.tsx`:

```tsx
import { useState } from 'react'
import { useIdentityToken } from '@privy-io/react-auth'
import { COLORS, FONTS } from '../theme'
import { showToast } from '../toastBus'
import { reconcilePacks, GachaHttpError } from '../../onchain/gachaClient'
import { notifyPendingPacksChanged } from '../screens/gacha/pendingPacksBus'

/**
 * "¿Falta un sobre?": busca en Collector Crypt sobres que el jugador pagó y nunca abrió.
 *
 * Existe porque un sobre comprado en la web de CC no tiene fila nuestra, y sin fila es invisible
 * para la app y no se puede abrir. El backend los adopta; aquí solo se pide y se avisa.
 *
 * NO pinta lo que encuentra: `notifyPendingPacksChanged` hace que AppShell recargue los
 * pendientes y abra su modal de recuperación, que ya tiene la ceremonia entera. Pintarlo aquí
 * sería una segunda copia del reveal.
 *
 * Un mismo componente en dos pantallas (el gacha y el inventario) a propósito: dos copias del
 * mismo botón es como se desincronizan los mensajes.
 */
export function RecoverPacksButton() {
  const { identityToken } = useIdentityToken()
  const [buscando, setBuscando] = useState(false)

  // Sin sesión no hay wallet por la que preguntar, así que el botón no tiene nada que ofrecer.
  if (!identityToken) return null

  async function buscar() {
    if (buscando) return          // dos pulsaciones seguidas no son dos búsquedas
    setBuscando(true)
    try {
      const pendientes = await reconcilePacks(identityToken!)
      if (pendientes.length === 0) {
        // Decirlo es obligatorio: un botón sin respuesta visible se lee como roto, y este se
        // pulsa justo cuando ya sospechas que algo va mal.
        showToast('No unopened packs found', 'info')
      } else {
        notifyPendingPacksChanged()
      }
    } catch (e) {
      // Un 401 aquí NO es una sesión caducada: el backend saca al jugador de la wallet embebida
      // del token, y una sesión de wallet externa no la lleva. Decirle "prueba otra vez" le haría
      // repetir para siempre lo único que no lo arregla.
      const sinWallet = e instanceof GachaHttpError && e.status === 401
      showToast(
        sinWallet
          ? 'This session has no in-app wallet. Log in with email or a social account instead of an external wallet'
          : 'Could not check for unopened packs. Try again in a moment',
        'error',
      )
    } finally {
      setBuscando(false)
    }
  }

  return (
    <button
      type="button"
      onClick={() => void buscar()}
      disabled={buscando}
      style={{
        background: 'transparent', border: 'none', padding: '7px 5px', cursor: buscando ? 'default' : 'pointer',
        fontFamily: FONTS.body, fontSize: 12.5, fontWeight: 600,
        color: COLORS.muted, textDecoration: 'underline', textUnderlineOffset: 3,
      }}
    >
      {buscando ? 'Checking…' : 'Missing a pack?'}
    </button>
  )
}
```

- [ ] **Step 5: Ejecutarlos y ver que pasan**

Run: `npx vitest run src/ui/components/RecoverPacksButton.test.tsx`
Expected: PASS (los cinco)

- [ ] **Step 6: Montarlo en el gacha**

En `src/ui/screens/gacha/GachaVault.tsx`, con los demás imports:

```tsx
import { RecoverPacksButton } from '../../components/RecoverPacksButton'
```

Y justo después del `})()}` que cierra el bloque de la máquina y ANTES del comentario
`{/* ── OPEN ERROR BANNER ─`:

```tsx
      {/* Para el que pagó un sobre y no lo ve: aquí está justo cuando lo echa en falta. */}
      <div style={{ display: 'flex', justifyContent: 'center', marginTop: 10 }}>
        <RecoverPacksButton />
      </div>
```

- [ ] **Step 7: Montarlo en el inventario**

En `src/ui/screens/Profile/InventoryTab.tsx`, con los demás imports:

```tsx
import { RecoverPacksButton } from '../../components/RecoverPacksButton'
```

Y dentro de `OwnInventory`, en el `<div style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 'auto' }}>`
que hoy contiene el chip "Buyback available", justo ANTES de ese `<button>`:

```tsx
          <RecoverPacksButton />
```

- [ ] **Step 8: Comprobar que los tests valen (mutación)**

Cada cambio: ejecuta, comprueba que FALLA, **deshaz**.

1. Quita `if (buscando) return` de `buscar`. Debe fallar el de las dos pulsaciones.
2. Cambia `if (pendientes.length === 0)` por `if (false)`. Debe fallar el de "si no hay nada lo dice".
3. Quita el `setBuscando(false)` del `finally`. Debe fallar el de reintentar.
4. Quita el `if (!identityToken) return null`. Debe fallar el de sin sesión.
5. Cambia `const sinWallet = e instanceof GachaHttpError && e.status === 401` por `const sinWallet = false`. Debe fallar el de sin wallet embebida.

- [ ] **Step 9: Todo verde, tipos y lint**

Run: `npx vitest run --dir src && npx tsc -b && npx eslint src/ui/components/RecoverPacksButton.tsx src/ui/screens/gacha/GachaVault.tsx src/ui/screens/Profile/InventoryTab.tsx`
Expected: todo PASS. Encadena con `&&`, nunca con `;`: un lint que falla no puede colarse en el commit.

- [ ] **Step 10: Commit**

```bash
cd /Users/mauro/Desarrollos/BattleArena
git add src/onchain/gachaClient.ts src/ui/components/RecoverPacksButton.tsx \
        src/ui/components/RecoverPacksButton.test.tsx \
        src/ui/screens/gacha/GachaVault.tsx src/ui/screens/Profile/InventoryTab.tsx
git commit -m "feat(gacha): botón para recuperar un sobre pagado que nunca se abrió"
```

---

### Task 4: Documentación

**Files:**
- Modify: `docs/COLLECTOR-CRYPT-API.md` (sección `## Endpoints NO documentados`, línea ~89, detrás del bloque de `GET /api/freeSpins?wallet=`)
- Modify: `docs/superpowers/specs/2026-09-09-recuperar-sobres-sin-abrir-design.md` (línea 4)

Nota: la tabla de endpoints de `backend/README.md` **no documenta ninguna ruta `/gacha/*`**
(comprobado), así que meter una sola fila de gacha ahí sería incoherente con el alcance de ese
fichero. Toda la integración con CC se documenta en `docs/COLLECTOR-CRYPT-API.md`, que es donde
vive. No toques el README.

- [ ] **Step 1: Documentar el endpoint de CC y nuestra reconciliación**

En `docs/COLLECTOR-CRYPT-API.md`, dentro de `## Endpoints NO documentados` y DETRÁS del bloque
entero de `### GET /api/freeSpins?wallet=` (o sea, antes del siguiente `###`), añade:

```markdown
### `GET /api/userStats?wallet=&data=linkedAll&count=&show=unopened`

La lista que hay detrás de su página `/user/packs/unopened`. **No lleva autenticación**: basta la
wallet, comprobado llamándola sin credenciales. Eso es cómodo para nosotros (no hace falta la
sesión de CC del jugador) y es un problema de privacidad de ellos: cualquiera puede enumerar el
historial de sobres de una wallet.

`show` acepta `all`, `buybacks`, `refunds`, `unopened` y `freepacks`. Pagina con `last_id` y
`pagination.hasMore`. Responde `{success, data: {linkedAll: [...], pagination}}`.

| campo | qué es |
|---|---|
| `memo` | la única llave que hace falta para abrirlo |
| `spin_cost` | lo que costó, en dólares. **No dice la máquina**, y varias cuestan lo mismo |
| `spin_status` | `confirmed` = el pago cuajó |
| `refund_transaction_signature` | si está, se devolvió el dinero y no hay nada que abrir |
| `send_nft_roll`, `send_nft_vrf_proof`, `send_nft_vrf_alpha` | vacíos hasta que se abre |

**La carta se sortea al ABRIR, no al pagar.** Esos campos `send_nft_*` se rellenan con `openPack`.
O sea que un sobre pagado y nunca abierto no está perdiendo ninguna carta ni tiene una esperando:
abrirlo tarde no es peor que abrirlo pronto.

De aquí sale `POST /gacha/packs/reconcile`, que adopta esos sobres para que se puedan abrir desde
nuestra app. Nuestra tabla `gacha_packs` solo conoce los sobres que originamos nosotros, así que
uno comprado en la web de CC es invisible (no sale en pendientes) e inabrible (`/gacha/open-pack`
exige fila propia). La wallet por la que se pregunta sale SIEMPRE del token de identidad: como
`openPack` solo necesita el memo, adoptar el sobre de otro sería poder abrirlo y ver su carta
antes que su dueño.
```

- [ ] **Step 2: Marcar el diseño como implementado**

En `docs/superpowers/specs/2026-09-09-recuperar-sobres-sin-abrir-design.md`, línea 4:

```
Status: implemented
```

- [ ] **Step 3: Pasar las dos suites enteras**

Run: `cd backend && ./.venv/bin/pytest -q && cd .. && npx vitest run --dir src && npx tsc -b`
Expected: todo PASS.

Aviso: si falla algo de `tests/test_referrals.py`, NO lo arregles. Es trabajo en curso ajeno a
esto; dilo en el informe y sigue.

- [ ] **Step 4: Commit**

```bash
cd /Users/mauro/Desarrollos/BattleArena
git add docs/COLLECTOR-CRYPT-API.md \
        docs/superpowers/specs/2026-09-09-recuperar-sobres-sin-abrir-design.md
git commit -m "docs(gacha): la reconciliación de sobres y de dónde sale la lista de CC"
```

---

## Cómo probarlo de verdad cuando esté

Hay un sobre real esperando, pagado el 9 de septiembre desde `8QDBKx8…gtm6` y **deliberadamente sin
abrir**. La prueba de aceptación es: entrar con esa wallet, pulsar "Missing a pack?", y que salga el
reveal. Su webhook ya llegó, así que resolverá al instante y no pasará por `WAITING_FOR_WEBHOOK`.

Se abre UNA vez: en cuanto se abra, deja de servir como caso de prueba.

## Fuera de alcance (está en el spec)

- Barrido automático en el servidor.
- Reconciliar `buybacks`, `refunds` o `freepacks`.
- Adivinar de qué máquina era un sobre adoptado.
- Que `GachaScreen.tsx` pague sin mandar el memo. Agujero real y aparte, de una línea.
