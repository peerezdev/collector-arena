# Claim del airdrop $CARDS desde la app - design

Date: 2026-09-08
Status: draft

## Objetivo

Que un jugador cuya wallet embedded esté en el airdrop $CARDS de Collector Crypt pueda
reclamarlo **desde nuestra app y sin tener SOL**, en vez de tener que ir a
`claim.collectorcrypt.com` con una wallet que casi siempre está a cero.

## Estado de partida (comprobado)

Todo lo de esta sección está verificado contra mainnet el 2026-09-08, no deducido.

- El airdrop es un **Metaplex Gumdrop** (programa `gdrpGjVffourzkdDRrQmySw4aTHr8a3xmQzzxSwFD1a`).
- **La ronda se identifica por la dirección del distribuidor, no por un trimestre.** No se le pone
  etiqueta de "Q2" ni parecida: el criterio con el que CC repartió no lo sabemos y la única
  suposición que hicimos al respecto resultó falsa (hay wallets en la lista cuya actividad empieza
  en agosto de 2026, así que no es un corte de abril a junio). Lo verificable es la dirección.
- Distribuidor `H6k7zSjCn2w5Q4em3b3E7iaPQfLrxVsF6u1bK6kD1Bhq`, **creado el 2026-09-07 a las 19:11
  UTC**, o sea que el airdrop se abrió el día antes de escribir esto. Su vault
  `5TBR7KQHbPsf3wHZ11dyL9iifztCnN9Ccr6rzoCvYqW7` bajó de 10.635.067 a 4.938.420 CARDS en unas
  horas del mismo 2026-09-08: de los 14.999.777 repartidos ya va reclamado un 67%. **Esto corre.**
- La autoridad del airdrop es `mUvPrVBWAuk4MBdH8CFwysXvtnrhxpFMQaz44xEbv6y`: es el campo `base`
  del distribuidor y el dueño del vault, no una cuenta de distribuidor (ni siquiera existe en la
  cadena). Posee **una sola** cuenta de token, la del vault, así que no hay otra ronda escondida
  detrás de ella.
- La web de CC **empotra la lista entera en su bundle**: 4.453 entradas con wallet, cantidad,
  índice y proof. Los 4.453 proofs validan contra la root que está en la cuenta del distribuidor.
- El IDL vive en la cadena. La instrucción `claim` es:

  ```
  args:  claimBump u8, index u64, amount u64, claimantSecret pubkey, proof vec<[u8;32]>
  accts: distributor, claimStatus(mut), from(mut), to(mut),
         temporal(signer), payer(signer,mut), systemProgram, tokenProgram
  ```

  **`payer` es un firmante distinto de `temporal`**, y de ahí sale todo el diseño: el operador
  puede pagar mientras el jugador firma.
- La hoja del árbol es `index_le8 || claimant(32) || mint(32) || amount_le8`. El hashing es
  `keccak256(0x00 || hoja)` para hojas y `keccak256(0x01 || min || max)` para nodos, con los dos
  hijos ordenados byte a byte.
- `ClaimStatus` es una PDA de semillas `["ClaimStatus", index_le8, distributor]`. **Que exista es
  la única señal fiable de "ya reclamado"**, porque el saldo de la wallet no vale (se puede vender).
- La transacción cuesta ~0,004 SOL: rent de `ClaimStatus`, rent de la ATA y fee. Lo paga `payer`.
- **Probado por simulación contra mainnet**: una tx con `payer` = operador y `temporal` = jugador
  se ejecuta sin error (`err: None`, 39.491 unidades de cómputo) y deja la ATA del jugador con sus
  1.483 CARDS. O sea que el programa **no exige que pague el reclamante**, que es justo lo que
  sostiene este diseño. Ninguna transacción real lo demostraba, porque la web de CC pone al usuario
  en las dos cuentas.
- En el repo ya existe todo lo que hace falta: `/users/me/nft/withdraw` monta exactamente este
  patrón de dos firmas (dueño autoriza, operador paga), `build_create_ata` ya emite un
  `CreateIdempotent` con `payer` propio, y `privy_signer.sign_solana` firma por wallet delegada.
- Las 4 wallets de la pool de escrow no están en la lista, no tienen CARDS ni cuenta de token
  abierta. No hay nada atrapado ahí.

## Decisiones

| | |
|---|---|
| Quién puede reclamar | Solo la wallet **embedded** de Privy |
| Quién construye la tx | El **backend**, entera |
| Quién paga | El **operador**, siempre |
| Dónde vive la lista | Fichero commiteado en `backend/data/`, cargado en memoria |
| Dónde se ve | Ruta propia `/claim`, fuera de la barra lateral |
| Redes | Solo mainnet |
| Rondas | Una, la abierta el 2026-09-07. Otra ronda = fichero y variables nuevas |

### Por qué la transacción la construye el backend y no el navegador

Porque paga el operador. Si el frontend armara la transacción y el backend se limitara a firmarla,
el backend estaría **firmando con la clave del operador unos bytes que decide el cliente**. Hoy
`/wallet/sign` es seguro justamente porque solo firma con la wallet del propio usuario autenticado:
lo peor que puede hacer alguien es perjudicarse a sí mismo. Meter la clave del operador en ese
camino convierte un endpoint inofensivo en una firma en blanco sobre nuestro dinero, y no se
justifica por un airdrop.

Como efecto secundario, el proof nunca sale del servidor y el navegador no carga 2,9 MB de lista.

### Por qué paga el operador y no el jugador

Una embedded recién creada tiene cero SOL y en el juego no hay ninguna forma cómoda de meterle,
porque todo el dinero se mueve en USDC. Si pagara el jugador, la mayoría se quedaría atascada en
"te falta SOL" delante de tokens que ya son suyos. Son ~0,004 SOL por reclamante, una vez, y con un
techo duro: solo puede reclamar quien está en una lista cerrada de 4.453 wallets, y una sola vez
cada una, porque la segunda vez la PDA ya existe y la cadena lo rechaza.

### Por qué la lista se commitea en vez de descargarla en caliente

Se puede sacar del bundle de CC en cualquier momento, pero entonces el contenido de nuestro claim
dependería de un fichero de un tercero que puede cambiar sin avisar. Commiteado, no puede cambiar
sin que aparezca en un diff. El script que lo genera valida cada hoja contra la root de la cadena
antes de escribir, así que un fichero corrupto o manipulado se detecta al generarlo y no cuando un
jugador le da al botón.

### Por qué solo la embedded

Es la que sale del identity token, así que el cliente **nunca dice qué wallet reclama** y por
construcción nadie puede reclamar lo de otro. Además es la única por la que podemos firmar. Una
wallet externa enlazada exigiría un segundo camino de firma en el navegador y ahí el operador ya no
puede pagar cómodamente. Las externas que hemos visto elegibles ya reclamaron por su cuenta.

## Datos: el fichero de asignaciones

`scripts/fetch_cards_airdrop.py` descarga el bundle de CC, extrae los 4.453 registros, **verifica
cada hoja contra la root on-chain** y aborta sin escribir si una sola falla. Salida:

```
backend/data/cards_airdrop_2026-09.json     # { wallet: { "i": index, "a": amount, "p": [proof…] } }
```

Unos 2,9 MB. El backend lo carga una vez al arrancar en un dict. Si el fichero no está o no se
puede leer, el módulo se queda vacío y los endpoints responden 503, nunca "no elegible".

## Backend

Servicio nuevo `backend/app/services/cards_airdrop.py`, con las piezas puras separadas de la parte
que habla con la red, que es lo que las hace testeables:

```python
def cargar_asignaciones(path: str) -> dict          # wallet -> {index, amount, proof}
def hoja(index: int, claimant: str, mint: str, amount: int) -> bytes
def verificar_proof(hoja: bytes, proof: list[bytes], root: bytes) -> bool
def claim_status_pda(index: int, distributor: str) -> tuple[Pubkey, int]
def build_claim_tx(claimant: str, index: int, amount: int, proof: list[bytes],
                   *, operador: str, blockhash: str, crear_ata: bool) -> str
```

El `data` del `claim` es `sha256("global:claim")[:8]` seguido de, en este orden, `bump u8`,
`index u64 LE`, `amount u64 LE`, `claimantSecret` (32 bytes, que es la propia wallet del jugador)
y el proof como `len u32 LE` más los hashes de 32 bytes seguidos.

`build_claim_tx` monta dos instrucciones: `CreateIdempotent` de la ATA del jugador (reutilizando
`build_create_ata`, con `payer` = operador) y el `claim` de Gumdrop, con `temporal` = jugador y
`payer` = operador.

Dos endpoints, con el prefijo que ya usa el resto:

- `GET /users/me/airdrop/cards` devuelve `{eligible, amount, claimed, signature?}`. Mira el fichero
  por wallet y pregunta a la cadena si la PDA de `ClaimStatus` existe.
- `POST /users/me/airdrop/cards/claim` construye, firma el jugador vía Privy, firma el operador,
  envía y devuelve `{signature, amount}`.

Guardas, en orden: sin fichero o sin operador configurado 503; wallet no delegada 409 con el mismo
mensaje que ya usa el juego; wallet no elegible 403; PDA ya existente 409; y throttle por wallet
igual que el de withdraw.

Configuración nueva en `config.py`, todo por variable de entorno para que una ronda futura no toque
código: `CARDS_AIRDROP_FILE`, `CARDS_AIRDROP_DISTRIBUTOR`, `CARDS_AIRDROP_VAULT`,
`CARDS_AIRDROP_MINT`, `CARDS_AIRDROP_ROUND`. Vacías = función apagada, que es lo que pasa en devnet.

Tabla nueva `airdrop_claims` (`wallet`, `ronda`, `amount`, `signature`, `created_at`), solo para
dejar constancia: reenseñar la firma al jugador que vuelve y saber cuánto SOL nos ha costado. No
manda sobre la elegibilidad, que la decide el fichero, ni sobre si está reclamado, que lo decide la
cadena.

## Frontend

Ruta `/claim` con `src/ui/screens/Claim/ClaimScreen.tsx` y `src/onchain/airdropClient.ts` con las
dos llamadas. Estados: sin sesión (botón de entrar), cargando, no elegible, elegible con el botón,
reclamando, reclamado con la cantidad y el enlace al explorer, y error. Con `config.isDevnet` la
pantalla dice que el airdrop solo existe en mainnet y no llama a nada.

No entra en la barra lateral: es una ruta enlazable desde fuera que deja de tener sentido cuando CC
cierre el vault.

## Errores y casos límite

| Caso | Qué pasa |
|---|---|
| RPC o Privy caídos al comprobar | Error reintentable. **Nunca** "no eres elegible": no saber no es saber que no |
| Wallet sin delegar | 409, el mismo mensaje que ya usa el juego para entrar a una partida |
| Operador sin SOL | 502, log claro, y al jugador "ahora no se puede, prueba luego" |
| Dos pestañas dan al botón a la vez | La segunda revienta en cadena porque la PDA ya existe; se traduce a "ya reclamado" |
| Ya reclamado fuera de la app | La PDA existe, así que sale como reclamado aunque no haya fila en `airdrop_claims` |
| CC cierra el vault | El claim falla en cadena; se enseña como error, no como "no elegible" |
| Fichero ausente o ilegible | 503 en los dos endpoints |
| Wallet no elegible | 403, y la pantalla lo dice sin más |

## Tests

Lo que de verdad importa anclar es el hashing, porque es lo que se rompe en silencio y sin avisar:

- **Test dorado del merkle**: la entrada real del índice 1687 (`8QDBKx…gtm6`, 1.483 CARDS) valida
  contra la root de mainnet, hardcodeada en el test. Y una hoja con la cantidad cambiada **no**
  valida.
- **PDA**: `claim_status_pda(1687, H6k7z…)` da `E1frLrGw1V1mVN6R7KcTwvBYD87ezWZKrspbs5dWJH6g`.
- **`build_claim_tx`**: cuentas en el orden del IDL, `temporal` = jugador y `payer` = operador como
  firmantes, fee payer = operador, y la ATA delante solo cuando toca.
- **Endpoints**, con el chain mock que ya existe: elegible sin reclamar, elegible ya reclamado,
  no elegible, sin operador, sin delegación.
- **Frontend**: los estados de `ClaimScreen` con fetch mockeado, y que en devnet no llama a nada.

Ningún test toca mainnet ni gasta SOL.

## Fuera de alcance

- Wallets externas enlazadas. Las que hemos visto elegibles ya reclamaron por su cuenta.
- Varias rondas a la vez. Una ronda nueva es fichero y variables, no código.
- Que pague el jugador.
- Reclamar en lote por todos los elegibles desde el operador.

## Anexo: valores de la ronda abierta el 2026-09-07

```
programa     gdrpGjVffourzkdDRrQmySw4aTHr8a3xmQzzxSwFD1a
distribuidor H6k7zSjCn2w5Q4em3b3E7iaPQfLrxVsF6u1bK6kD1Bhq
vault (from) 5TBR7KQHbPsf3wHZ11dyL9iifztCnN9Ccr6rzoCvYqW7
mint         CARDSccUMFKoPRZxt5vt3ksUbxEFEcnZ3H2pd3dKxYjp   (6 decimales)
root         edef2b3b6e41e35a7843cd3521490a576651df938ffc40fb3e79c9de146503bd
temporal     el propio reclamante. El campo `temporal` de la cuenta del distribuidor guarda el
             id del programa, no una clave de CC, y el programa acepta al reclamante como
             `temporal` porque coincide con el `claimantSecret` del argumento. Verificado en una
             tx real que funcionó y en la simulación.
```
