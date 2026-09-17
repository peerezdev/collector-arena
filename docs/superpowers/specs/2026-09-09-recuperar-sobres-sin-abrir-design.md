# Recuperar sobres de gacha sin abrir — design

Date: 2026-09-09
Status: approved-pending-review

## Objetivo

Que un sobre pagado y nunca abierto se pueda recuperar desde nuestra app, incluso si se compró en
la web de Collector Crypt y nosotros no llegamos a enterarnos.

## El caso que lo motiva (comprobado, no hipotético)

Un sobre de 25 USDC pagado el 2026-09-09 a las 17:47 UTC desde la wallet `8QDBKx8…gtm6`. En CC
consta con el pago `confirmed`, su webhook recibido, sin reembolso, y todos los campos `send_nft_*`
vacíos: **la carta nunca se sorteó**. Sigue sin abrir a propósito, para poder probar con él.

El memo va truncado aquí (`cc-b9c035e3…`) porque `POST /api/openPack` solo necesita el memo:
cualquiera que lo tenga puede disparar el sorteo y ver la carta. Recuperarlo entero es una consulta
al endpoint de abajo.

Tres hechos que salieron de investigarlo y que sostienen todo el diseño:

- **Ese memo no está en ninguna de nuestras bases.** El último sobre que registramos en mainnet es
  del 25 de agosto. La compra no pasó por nosotros.
- **La carta se sortea al ABRIR, no al pagar.** Los campos `send_nft_roll` y `send_nft_vrf_proof`
  se rellenan en ese momento. No hay ninguna carta asignada esperando ni perdiéndose, así que
  esperar no cuesta nada y abrir tarde no es peor que abrir pronto.
- **Nuestra app no puede abrirlo hoy**, y no por falta de permiso sino por construcción:
  `/gacha/open-pack` exige una fila `GachaPack` de esa wallet (`main.py`, "this memo does not
  belong to this wallet"), y `/gacha/packs/pending` solo lee nuestra propia tabla. Un sobre que no
  originamos es a la vez invisible e inabrible.

## Lo que ya existe y NO se toca

La recuperación de pendientes está entera y funciona: `GachaPack` con `submitted_at` / `opened_at` /
`revealed_at`, `GET /gacha/packs/pending`, el reveal, el "marcar visto" y el bus
`pendingPacksBus`. Este diseño **no construye una recuperación nueva**: solo le da de comer.

## El contrato de CC (sacado de su propio JS, `1_elf0d7kxxik.js`)

| | |
|---|---|
| Listar | `GET /api/userStats?wallet=<addr>&data=linkedAll&count=100&show=unopened` |
| Abrir | `POST /api/openPack {memo}`, reintentando mientras responda `WAITING_FOR_WEBHOOK` |

El listado **no lleva autenticación**: basta la wallet, comprobado llamándolo sin credenciales.
Filtros posibles: `all`, `buybacks`, `refunds`, `unopened`, `freepacks`. Pagina con `last_id` y
`pagination.hasMore`. Responde `{success, data: {linkedAll: [...], pagination}}`.

Que sea público es cómodo para nosotros —no hace falta la sesión de CC del jugador— y es un
problema de privacidad de ellos, no nuestro: cualquiera puede enumerar el historial de sobres de
una wallet. No lo empeoramos, pero conviene saberlo antes de apoyar nada más encima.

## Decisiones

| | |
|---|---|
| Qué se adopta | Todo lo que CC diga de esa wallet, incluidas las compras hechas en su web |
| Cómo cuenta en el perfil | Como cualquier otro sobre: historial y mejor carta, sin distinguir origen |
| Cuándo se pregunta | Solo al pulsar un botón. Ni al arrancar ni en barrido |
| Dónde está el botón | En el gacha y en el inventario del perfil, un componente usado dos veces |
| Qué pasa al encontrarlo | Se adopta y lo recoge la recuperación de siempre |

### Por qué adoptar (escribir la fila) y no solo enseñarlo

Es la diferencia que decide si esto arregla el problema. Al escribir la fila, el sobre entra en la
maquinaria normal: **se pulsa una vez y ya está recuperado para siempre**, también en otro
dispositivo, porque la lista de pendientes que se pide al arrancar ya lo incluye.

Sin escribir, cada dispositivo tendría que volver a pulsar, y el problema que estamos arreglando es
justo "pagué y desapareció". Una solución que depende de que te acuerdes de pulsar otra vez repite
el fallo con otra cara.

### Por qué botón y no automático

Decisión del producto, con su precio asumido: solo lo encuentra quien lo busque. Se compensa en
parte con lo de arriba (basta pulsar una vez en la vida) y poniéndolo en los dos sitios donde el
jugador nota la ausencia.

## Backend

### `POST /gacha/packs/reconcile`

Con sesión. Cuatro pasos:

1. **La wallet sale del token de identidad**, nunca de un parámetro ni del cuerpo. Es lo único que
   impide que alguien nos haga adoptar el sobre de otro y, con ello, abrirlo y ver su carta.
2. Pregunta a CC por esa wallet (`show=unopened`).
3. Se queda con lo recuperable: pago `confirmed`, sin firma de reembolso, con memo. Un sobre
   reembolsado o con el pago sin cuajar no se adopta porque no hay nada que abrir.
4. Crea la fila de cada memo que no tengamos, con `submitted_at` a la fecha del pago de CC. Ese
   campo es el que decide si sale en pendientes: sin él la adopción no se vería y el arreglo sería
   invisible. Las filas existentes no se tocan, así que pulsar dos veces no duplica ni pisa nada.

Devuelve la lista de pendientes ya refrescada, **con la misma forma exacta que
`GET /gacha/packs/pending`**, para que el cliente no tenga que encadenar dos llamadas ni conocer
un formato nuevo.

Lleva el throttle por wallet del gacha: es una llamada a un tercero disparada por un botón.

### `pack_type` de un sobre adoptado

CC dice el coste, no la máquina, y varias máquinas comparten precio (`pokemon_25` y `comic_25`).
Así que se guarda `cc_<coste>`, sin inventarse cuál era. Comprobado que degrada bien:

- `packTitle("cc_25")` → `CC 25`
- `priceFromCode("cc_25")` → 25, así que la paleta sale la correcta
- `recompraDe("cc_25", …)` → `null`, así que **no se enseña la recompra de otra máquina**, que es
  justo el fallo que ese `null` existe para evitar

### En el service

Un método que traduce la fila de CC a los tres campos que usamos —memo, coste y fecha— y tira el
resto. Su respuesta trae una veintena de campos; depender de ellos sería atarnos a un esquema de
un tercero que no controlamos y que ya cambió una vez (el nonce de `generateFreePack`).

## Frontend

Un solo componente, usado en el gacha (bajo las máquinas) y en el inventario del perfil. Dos copias
del mismo botón es como se desincronizan los mensajes.

Su trabajo entero: llamar a reconciliar, llamar a `notifyPendingPacksChanged()`, y ya. AppShell
recarga los pendientes, ve un memo nuevo y abre solo el modal de recuperación con su ceremonia.

Dos añadidos que no son opcionales:

- **Si no hay nada, se dice.** Un botón que no hace nada visible se lee como roto, y este se pulsa
  justo cuando ya sospechas que algo va mal.
- **Si CC falla, se dice y se puede reintentar.** No hay estado a medias que limpiar, porque el
  backend no escribió nada.

## Errores y casos límite

| Caso | Qué pasa |
|---|---|
| CC no responde | 502, cero filas escritas; la lista de pendientes de siempre sigue funcionando |
| Sesión sin wallet embebida | Mismo mensaje que las tiradas gratis: volver a entrar NO lo arregla |
| Sobre ya adoptado | Idempotente: no duplica ni pisa la fila |
| CC lo lista sin abrir pero ya lo abrimos | No se toca la fila; no reaparece |
| `WAITING_FOR_WEBHOOK` al abrirlo | Ya lo maneja `pollOpenPack`; sigue pendiente y se reintenta |
| Más de 100 sin abrir | Se coge una página. Más de cien atascados es un incidente, no un caso de diseño |

## Tests

- **Seguridad, el que de verdad importa**: mandar una wallet o un memo en la petición NO cambia
  nada, porque la wallet sale del token. Sin este test, el día que alguien "mejore" el endpoint
  aceptando una wallet, nadie se entera.
- **Adopción**: crea la fila de un memo nuevo; es idempotente al repetir; pone `submitted_at`, sin
  el cual el sobre no aparecería en pendientes.
- **Filtrado**: un sobre reembolsado y uno con el pago sin confirmar NO se adoptan.
- **Fallo de CC**: 502 y **cero filas escritas**.
- **Service**: traduce los tres campos y no revienta si CC añade o quita otros.
- **Frontend**: el botón reconcilia y avisa al bus; con cero resultados lo dice; un fallo se enseña.

## Lo que este diseño acepta a sabiendas

- **Adoptamos compras que no pasaron por nosotros**, y cuentan en el perfil como propias. Decisión
  tomada: si la carta es tuya y salió de tu wallet, es tuya.
- **Solo lo encuentra quien pulse.** El precio de haber elegido botón.
- **Nos fiamos de CC para crear filas.** Es su fuente de verdad, así que es correcto, pero deja de
  ser cierto que toda fila de `gacha_packs` la originamos nosotros. Cualquier cosa que dependa de
  eso en el futuro tiene que saberlo.

## Fuera de alcance

- Barrido automático en el servidor.
- Reconciliar `buybacks`, `refunds` o `freepacks`, que el mismo endpoint de CC sabe listar.
- Adivinar de qué máquina era un sobre adoptado.
- Arreglar que `GachaScreen.tsx` pague sin mandar el memo (agujero real y aparte: esos sobres nunca
  reciben `submitted_at`, así que tampoco salen en pendientes). Va por su cuenta, es una línea.
