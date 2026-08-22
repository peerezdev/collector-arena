# Machine Tracker: pase de pago junto al wager — design

Date: 2026-08-21
Status: approved-pending-review

## Objetivo

Hoy el Machine Tracker se desbloquea de una sola forma: 100 USDC apostados en Pack Battle o Battle
Royale en los últimos 7 días. Hay usuarios que prefieren **pagar** por la herramienta en lugar de
apostar. Se añade esa segunda vía sin quitar la primera.

Al abrir esa puerta aparece un problema que hoy no importaba: los datos no están protegidos. Se
cierra también.

## Estado de partida (comprobado)

- La puerta vive en `backend/app/services/tracker_access.py`. Es **deliberadamente sin estado**: la
  ventana rodante se recalcula en cada consulta, así que *"no hay nada que caducar ni ningún estado
  que mantener"*. Tiene ya dos motivos de acceso: el wager y una lista blanca fijada por entorno.
- **`/gacha/ev` y `/gacha/ev/live` son públicos y el cliente ni siquiera les manda el token.**
  Medido: `curl http://<host>/gacha/ev` devuelve las filas completas, con `realized_edge_pct`,
  intervalo, veredicto, `gaps`, rachas y modelo del pool. La puerta hoy esconde la pantalla, no los
  datos.
- `/gacha/winners` y `/gacha/winners/gaps` piden a Collector Crypt en directo con el tope de 200 y
  **no sirven nuestro histórico**, así que no son una vía de fuga.
- No cobramos ningún margen en el gacha. El tracker, que sirve para decidir cuándo NO tirar, no
  canibaliza ningún ingreso nuestro.
- La fee de batalla es 0.5% por jugador con tope del 3% (`battle_fee_pct_per_player`,
  `battle_fee_pct_cap`). Una partida de cuatro deja un 2% del bote, así que **apostar 100 USDC nos
  genera del orden de 2 USDC**. La puerta del wager es una palanca de actividad, no de ingreso.
- El raíl de cobro existe y está probado: `collect_buyin` en `royale_funding.py` mueve USDC con dos
  firmas, el jugador autorizando y el operador pagando el gas.
- `MachineTrackerPage` solo monta `PanelEv` cuando `acceso.allowed`, y con la puerta puesta **ni
  llama a `/gacha/ev`**. El fondo de la puerta ya es falso (`trackerFantasmas.ts`).

## Decisiones

| | |
|---|---|
| Vías de acceso | wager **o** pase activo **o** lista blanca de la casa |
| Duraciones | 7 y 30 días |
| Precio | En configuración, decidido en el despliegue. **0 = apagado** |
| Renovación | Manual. **Sin cobro automático** |
| Comprar estando dentro | Suma al final del pase vigente, no desde hoy |
| Devoluciones | No hay, y se dice **antes** de pagar |
| Datos | `/gacha/ev` y `/gacha/ev/live` pasan a exigir acceso |

### Por qué sin cobro automático

Técnicamente podríamos: las wallets están delegadas y el servidor firma por ellas. Precisamente por
eso no. Cobrar en silencio cada mes usando una delegación que el usuario aceptó **para jugar** es la
clase de cosa que cuesta la confianza de golpe y no se recupera. Un pase que caduca y se vuelve a
comprar es menos ingreso previsible y mucho menos riesgo.

### Por qué el pase suma al final y no empieza hoy

Si comprar pronto quitara días, la gente aprendería a esperar a que caduque. Peor para ellos y peor
para nosotros.

### Por qué se cierran los datos

El razonamiento del comentario actual (*"cerrarlo daría una falsa sensación de exclusividad a cambio
de romper los enlaces que se comparten"*) se sostiene mientras el tracker es gratis y la puerta es
una palanca de engagement. **Deja de sostenerse en cuanto alguien paga**: estaríamos cobrando por
algo que un `curl` regala, y el primero que lo descubra lo publica.

Lo que se protege es lo único irreconstruible. El `getAllWinners` de CC tope en 200 tiradas por
máquina y no hay forma de mirar más atrás: ni con `timestamp`, ni con `page`, ni con `before` (todo
medido). Quien quiera esos números tiene que haber estado escuchando el feed desde antes.

**Y no rompe ningún enlace, al contrario de lo que decía ese comentario.** Comprobado: el único
consumidor de `/gacha/ev` en todo el repo es `MachineTrackerPage`, que ya no lo llama con la puerta
puesta. Compartir `/machine-tracker` con alguien sin acceso YA le enseña la puerta hoy. Lo único que
deja de funcionar es el script de un tercero que hubiera encontrado el endpoint abierto, que es
justo lo que se quiere cerrar. El coste que el comentario temía no existe en nuestro producto.

## Configuración

En `backend/app/config.py`:

```python
tracker_pass_7d_usdc:  float = 0.0   # env: TRACKER_PASS_7D_USDC
tracker_pass_30d_usdc: float = 0.0   # env: TRACKER_PASS_30D_USDC
```

**Cero apaga la vía de pago entera**, igual que `gacha_base_url` vacío apaga el gacha. Esto se puede
desplegar sin precio: la opción no aparece en pantalla y el endpoint de compra responde 503. Es lo
que permite mergear sin haber decidido el precio.

Al arrancar, si `tracker_pass_30d_usdc / 30 > tracker_pass_7d_usdc / 7`, se emite un `warning`: el
pase largo saldría más caro por día que el corto. No se bloquea el arranque (es una decisión de
negocio, no un error), pero es un fallo de configuración que si no, solo lo descubre el cliente que
eche la cuenta.

## Modelo de datos

Tabla nueva `tracker_passes`. Es la **única** parte con estado; el cálculo del wager no se toca.

| columna | tipo | |
|---|---|---|
| `id` | String, PK | uuid4 |
| `wallet` | String, indexado | quién compró |
| `days` | Integer | 7 o 30 |
| `price_base_units` | Integer | lo cobrado, en unidades base de USDC |
| `status` | String | `pending`, `active`, `failed` |
| `starts_at` | DateTime(tz) | ver apilado |
| `ends_at` | DateTime(tz) | `starts_at + days` |
| `tx_signature` | String, nullable | la firma del cobro |
| `created_at` | DateTime(tz) | |

Índice compuesto por `(wallet, status, ends_at)`: es la consulta de la puerta.

**Solo `status == 'active'` da acceso.** Un `pending` no, y un `failed` tampoco.

`_ENSURE_COLUMNS` no aplica: es tabla nueva y `create_all` la crea. No hay migración de datos.

## La compra

`POST /gacha/tracker-pass`, cuerpo `{"days": 7}` o `{"days": 30}`.

- Autenticación **obligatoria** (a diferencia de `/gacha/tracker-access`, que es opcional): sin
  sesión no hay a quién cobrar. Sin token → 401.
- `days` distinto de 7 o 30 → 422.
- Precio 0 para esa duración → 503 `tracker_pass_disabled`.
- Freno de peticiones por wallet, con el mismo mecanismo que el resto de endpoints de gacha.

### Orden del cobro, que es lo delicado

> **NOTA (post-implementación, ronda 4): este orden no es el que corre en producción.** Lo que
> sigue es el diseño ORIGINAL. Tres rondas de revisión seguidas encontraron el MISMO defecto con
> la fila `pending` insertada ANTES de cobrar (paso 1 de abajo): una excepción mal clasificada —un
> blockhash raro, una wallet de destino mal escrita, un operador o un mint mal configurados—
> dejaba la fila puesta sin que nada se hubiera cobrado todavía, y el candado de esa `pending`
> encerraba a la wallet; cuando el fallo era de configuración (no de quién compraba), encerraba a
> TODAS. El orden final invierte los pasos 1 y 2: se construye, se firma, se LEE la firma de la
> transacción ya firmada (viaja dentro de ella, no la inventa el RPC) y la fila se inserta YA CON
> esa firma, justo antes de enviar. Todo lo anterior al envío queda así fuera de la fila: no hay
> nada que desbloquear si revienta, así que esa familia entera de fallos deja de existir por
> construcción en vez de por acertar con una lista de `except`. Ver `backend/app/main.py`,
> docstring de `gacha_tracker_pass`, para el orden real y por qué.

Aquí se mueve dinero real de un usuario. El orden es (diseño original, ver nota de arriba):

0. **Se comprueba el saldo DISPONIBLE antes de tocar nada**, y disponible significa el USDC
   on-chain **menos `reserved_total`**. Es la parte que no se puede saltar: si un jugador tiene
   dinero comprometido en una batalla que aún no se ha liquidado, un pase no puede gastárselo, o la
   batalla se queda sin fondos. Si no llega → **402 `not enough available USDC`**, que es la
   respuesta que ya usa el resto de la aplicación para esto, y **no se inserta ninguna fila**.
1. Se calcula `starts_at` (ver apilado) y `ends_at`, y se inserta la fila con `status='pending'`.
   **Todavía no da acceso.**
2. Se cobra con el raíl existente: `collect_buyin`, el jugador autoriza el USDC y el operador paga
   el gas. Destino: `fee_wallet_address` (con el respaldo al operador que ya usa la fee de batalla).
3. Con la firma en la mano, la fila pasa a `active` y guarda `tx_signature`.
4. Si el cobro falla en cadena, la fila pasa a `failed` y se responde **502** con el motivo. Se
   distingue del 402 a propósito: el 402 es "no tienes bastante" y es cosa del jugador; el 502 es
   "no hemos podido cobrarte" y es cosa nuestra o de la red.

Al revés (marcar activo y cobrar después) se regala acceso cuando el cobro falla. El caso feo que
queda es morirse entre el 2 y el 3: hay cobro y la fila sigue en `pending`. Para poder reconciliarlo
**la firma se guarda en cuanto se conoce**, antes de tocar `status`, y un `pending` con
`tx_signature` es exactamente la señal de "esto se cobró y no se activó". Se registra en el log a
nivel `critical`, como ya se hace en la entrega del gacha.

El pase **no reserva** saldo como hace una batalla: el cobro es inmediato y de un solo paso, así que
no hay ventana entre comprometer y gastar.

### Apilado

`starts_at` = el `ends_at` del pase activo más lejano de esa wallet, si lo hay y está en el futuro;
si no, ahora.

El wager **no** afecta al apilado: si compras teniendo acceso por haber apostado, el pase empieza
hoy igualmente. Descontarlo obligaría a adivinar cuánto va a durar tu wager, que es rodante y
cambia solo.

## La puerta, con dos motivos

`tracker_access.acceso()` recibe un argumento nuevo `pase_hasta: Optional[datetime]` (lo resuelve el
endpoint consultando la tabla; el módulo se mantiene puro y sin sesión propia para eso). Tiene un
solo llamante hoy, `main.py:1038`, así que el cambio de firma está contenido.

La respuesta gana tres campos, y **`wagered_usd` se sigue diciendo de verdad** aunque entres por
pase, por lo mismo que ya no se falsea con la lista blanca:

```json
{
  "allowed": true,
  "via": "pass",              // "wager" | "pass" | "house" | null
  "pass_until": 1789999999,   // unix, o null
  "wagered_usd": 12.5,
  "required_usd": 100.0,
  "missing_usd": 87.5,
  "window_days": 7,
  "pass_prices": { "7": 0.0, "30": 0.0 }   // {} si la vía de pago está apagada
}
```

Orden de resolución: lista blanca, luego pase, luego wager. Es de más fuerte a más débil, y así
`via` dice el motivo real.

## Cerrar los datos

`/gacha/ev` y `/gacha/ev/live` pasan a exigir `Authorization: Bearer <token>` y acceso concedido:

- Sin token o sin acceso → **403** con cuerpo `{"detail": "tracker_locked"}`.
- El cliente (`fetchEvRows`, `fetchEvLive` en `src/onchain/gachaClient.ts`) pasa a mandar el token,
  que ya tiene de `useIdentityToken`.
- `MachineTrackerPage` ya solo monta `PanelEv` con acceso concedido, así que el 403 solo puede darse
  en una carrera: el pase caduca con la pantalla abierta. **Un 403 NO se trata como fallo de red**:
  no se enseña "Couldn't load the tracker", se vuelve a pedir el acceso y aparece la puerta. Mezclar
  las dos cosas diría "se ha roto algo" cuando lo que pasa es que se acabó el pase.

Se actualiza el comentario del endpoint, que hoy justifica lo contrario, explicando por qué cambió.

## Pantalla

**La puerta** (`TrackerGate.tsx`) mantiene lo que tiene (cuánto falta, la barra, "Find a match") y
gana debajo un bloque con los dos pases: duración, precio, y el precio por día del de 30 para que se
vea que sale mejor. Debajo, en el mismo bloque y no en letra pequeña: **sin devoluciones**.

Si `pass_prices` viene vacío, el bloque **no se renderiza**. Nada de botones deshabilitados.

**Dentro del tracker**, cuando `via === 'pass'`, la cabecera dice cuándo caduca junto al
`UPDATED`/`STALE`. Sin eso la gente se lleva la sorpresa al volver.

## Casos límite decididos

| caso | qué pasa |
|---|---|
| Compra sin saldo suficiente | 402 antes de insertar nada. No queda ni fila ni cobro |
| Tiene USDC pero reservado para una batalla | Cuenta como NO disponible: 402. Un pase no puede vaciar el dinero de una partida |
| El cobro falla en cadena | La fila queda `failed` y responde 502. No da acceso |
| Compra dos veces seguidas | Dos filas `active`, la segunda empieza donde acaba la primera |
| Pase activo y además cruza el wager | Ambos dan acceso; `via` dice `wager` solo si no hay pase |
| Pase caducado | Deja de dar acceso, la fila se queda como registro histórico |
| Está en la lista blanca y compra | Se le cobra y se le da el pase. No se le impide comprar, pero la pantalla no le ofrece comprar porque ya tiene acceso |
| Precio cambia con pases vendidos | No afecta: `price_base_units` guarda lo cobrado |

## Qué NO cambia

- La ventana rodante de 7 días, tal cual está documentada.
- Que el gacha no cuente para el wager.
- La lista blanca de la casa.
- `/gacha/winners` y `/gacha/winners/gaps` siguen públicos: sirven de CC en directo con tope 200 y
  no exponen nuestro histórico.

## Pruebas

**Puras** (`tracker_access`, y un módulo nuevo para el periodo del pase):
- El apilado: sin pase previo empieza hoy; con pase vigente empieza a su fin; con pase caducado
  empieza hoy.
- El orden lista blanca → pase → wager, y que `via` dice el motivo real.
- Que `wagered_usd` sigue siendo el real entrando por pase.
- Que un `pending` y un `failed` no dan acceso.
- El aviso de configuración cuando el pase de 30 sale peor por día que el de 7.

**De API:**
- Comprar da acceso, y `via` pasa a `pass`.
- **Un cobro que falla no da acceso** y deja la fila en `failed`, respondiendo 502.
- **Saldo reservado para una batalla no se puede gastar en un pase**: 402 y ninguna fila creada.
- Comprar sin token → 401; `days` inválido → 422; precio a 0 → 503.
- `/gacha/ev` sin token → 403; con token sin acceso → 403; con acceso → 200.
- Un pase caducado deja de abrir `/gacha/ev`.

**De pantalla:**
- La puerta enseña los dos pases y el aviso de no devolución; con precios apagados no enseña nada.
- Entrando por pase, la cabecera dice cuándo caduca.
- Un 403 en `/gacha/ev` enseña la puerta, **no** el error de carga.

## Riesgos

1. **Se mueve dinero real de un usuario.** Es la primera vez que cobramos por algo que no es una
   partida. El orden `pending` → cobro → `active` y el `critical` en el hueco son lo que lo hace
   reconciliable a mano; no hay reversión automática de una transferencia on-chain.
2. **El precio se decide fuera del código.** Con 0 por defecto, un despliegue que olvide ponerlo
   simplemente no ofrece la compra, que es el fallo seguro.
3. **Cerrar `/gacha/ev` no tiene el coste que parecía.** Se comprobó que no rompe ningún enlace
   nuestro. Queda como riesgo menor que alguien de fuera tuviera un script contra el endpoint
   abierto y deje de funcionar sin aviso, que es precisamente el efecto buscado.
