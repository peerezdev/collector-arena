# Depósito y retirada multicadena con Privy: lo que sabemos

Investigación del 2026-08-19, **sin escribir una línea de código**. La pregunta de partida era:
¿podemos dejar que la gente deposite USDC desde Base, Arbitrum y otras, y que les llegue solo a su
wallet embebida de Solana sin tener que hacer ningún bridge?

Respuesta corta: **sí, y Privy ya lo trae**. La parte de retirar también existe, pero no es gratis y
su precio no está publicado. Ahí está la decisión que quedó abierta.

Distinción importante en todo el documento: lo que sale de leer **los tipos publicados en
`node_modules`** es un hecho comprobado; lo que sale de la documentación de Privy va marcado como
tal, porque en al menos un punto se contradice a sí misma.

---

## Lo que ya tenemos montado

- Wallet embebida de **Solana** para todos los usuarios, creada en el login
  ([AppPrivyProvider.tsx](../src/wallet/AppPrivyProvider.tsx), `embeddedWallets.solana.createOnLogin: 'all-users'`).
- Firma desde el servidor contra `/v1/wallets/{id}/rpc` con firma de autorización
  ([privy_signer.py](../backend/app/services/privy_signer.py)).
- Depósito actual: [DepositModal.tsx](../src/ui/components/DepositModal.tsx) enseña la dirección de
  Solana y un QR. Nada más.
- Retirada actual: solo dentro de Solana, dos firmantes (el jugador autoriza, el operador paga el
  gas), con comisión opcional en la misma transacción
  ([royale_funding.py:63](../backend/app/services/royale_funding.py#L63)).

O sea que hoy, para jugar, hay que traerse el USDC a Solana por tu cuenta.

---

## Depositar: universal deposit addresses

Privy genera una dirección a la que el jugador manda USDC desde otra cadena, y al llegar se bridgea
y se convierte solo hasta la wallet de destino que le digamos.

**Ya está en el SDK que tenemos instalado** (`@privy-io/react-auth` 3.31.0). Comprobado en
`node_modules`, no en la documentación:

```ts
// @privy-io/react-auth/dist/dts/use-deposit-address-*.d.ts
/** @experimental This interface may change at any time. */
declare const useDepositAddress: () => {
  createDepositAddress: (opts: DepositAddressFlowParams) => Promise<void>
}
```

Ese hook abre **su** modal y no devuelve la dirección. Debajo hay una API completa que sí la
devuelve, en `@privy-io/js-sdk-core`, y también está ya instalada (0.67.2):

```ts
type GenerateDepositAddressInput = {
  sourceChain: string; sourceCurrency: string
  destinationChain: string; destinationCurrency: string; destinationAddress: string
  refundAddress?: string; slippageBps?: number
}

type DepositAddressQuote = {
  id: string
  deposit_address: string          // <- la dirección, para pintarla nosotros
  indicative_rate: string
  time_estimate_seconds: number
  refund_address: string
  created_at: string
  ...
}

generateDepositAddress(...)  → Promise<DepositAddressQuote>
waitForDeposit / waitForCompletion / getDeposit   // seguimiento
getConfig(privy) → { currencies[], chains[] }     // las redes soportadas, en ejecución
resolveRefundAddress({ privy, caip2 }) → { ok, address } | { ok: false, error }
```

### Lo que esto resuelve

- **No rotan.** La documentación de Privy: *"Deposit addresses can be reused. If a user sends funds
  to the same deposit address again, Privy routes the new deposit using the same source token,
  source chain, and destination settings."* Los tipos lo respaldan: la dirección se deriva de una
  tupla fija (cadena y moneda de origen, cadena y moneda de destino, dirección de destino). Es
  **una dirección estable por red de origen**, igual que un exchange: una para Base, otra para
  Arbitrum. Sirve para configurar una retirada recurrente desde un exchange.
- **Lo experimental no nos afecta si no queremos.** El hook de React sigue marcado `@experimental`
  incluso en la 3.37.3, la última publicada (comprobado descargando el paquete; nosotros vamos por
  la 3.31.0). Pero `generateDepositAddress` en el core **no lleva esa marca** ni en la instalada ni
  en la 0.71.0, y en ese mismo fichero hay otras 6 cosas que sí la llevan, así que la ausencia
  significa algo. Construyendo sobre el core y con interfaz propia, no dependemos de lo experimental.

  Aviso sobre la documentación: su changelog dice que el hook se estabilizó en la 3.28 y que se
  introdujo en la 3.29, lo cual es imposible. No es fiable; los tipos publicados sí.
- **No hay que adivinar las cadenas.** `getConfig()` devuelve la lista real en ejecución.
- **Gratis y sin KYC** en la ruta de depósito, según Privy. No hay comisión ni mínimo mensual.
- Orígenes citados por Privy: Ethereum, Base, Arbitrum, Polygon, Optimism, Bitcoin, Solana y más.
  Destino: Solana mainnet con el mint de USDC está soportado.

### Por qué la wallet EVM embebida sigue haciendo falta

No para el bridge, sino **para las devoluciones**. El `refund_address` es obligatorio en la
cotización, los pedidos pueden acabar en `status: 'refunded'`, y existe `resolveRefundAddress()` con
un error propio `REFUND_WALLET_CREATION_FAILED` y un callback `onWalletCreated`. Si un depósito
desde Base falla, el dinero vuelve a una dirección **en Base**, y para eso el jugador necesita una
wallet EVM. Privy la crea sola dentro de ese flujo.

Privy soporta wallets EVM y Solana para el mismo usuario sin problema.

### Cómo puede fallar

Los códigos de error del propio SDK dicen dónde está el riesgo:

```
ROUTE_UNAVAILABLE · AMOUNT_TOO_LOW · INSUFFICIENT_LIQUIDITY · UNSUPPORTED_ROUTE
DEPOSIT_FAILED · DEPOSIT_REFUNDED · SANCTIONED_WALLET_ADDRESS
TIMEOUT_WAITING_FOR_NEXT_ORDER · TIMEOUT_ORDER_COMPLETION
```

---

## Retirar: transfer intent

```
POST /v1/intents/wallets/{wallet_id}/transfer
  source:      { asset: "usdc", chain: "solana" }
  destination: { address: "0x…", chain: "base" }
  amount_type: "exact_input"
```

Se llama desde el servidor con `privy-app-id` y Basic Auth, que es el mismo mecanismo que ya
implementa nuestro firmante. Devuelve un `intent_id` y estados: `pending`, `processing`, `executed`,
`failed`, `expired`, `rejected`, `dismissed`.

Tres cosas de la documentación:

1. **Desde Solana solo vale `exact_input`.** *"The `amount_type: 'exact_output'` parameter is not yet
   supported when the source chain is Solana."* Especificamos lo que sale, no lo que llega, así que
   al jugador solo se le puede prometer una cantidad aproximada.
2. **Comisiones desglosadas**: `relayer fee`, `privy fee` y `developer fee`, más un
   `fee_configuration.total_fee_bps`. Nuestro `withdraw_fee_pct` actual podría ir por ahí.
3. **El precio no está publicado.** Lo busqué en la referencia de la API, en el overview de funding
   y fuera de la documentación. No aparece.

---

## La comparación que decide la retirada

Depositar es gratis; retirar no. Y hay una diferencia de fondo entre los dos caminos posibles:

| | Privy (Relay) | CCTP de Circle |
|---|---|---|
| Mecanismo | Red de liquidez | Quemar y acuñar |
| Lo que recibe el jugador | Aproximado, con `slippage_bps` | Exactamente 1:1 |
| Puede fallar por liquidez | Sí (`INSUFFICIENT_LIQUIDITY`) | No, no hay pool |
| Coste | Sin publicar | 1 punto básico en Fast Transfer |
| Cobertura | Casi cualquier cadena y activo | Solo USDC, y solo cadenas CCTP |
| Quién lo opera | Ellos | Nosotros |

En un juego donde se retiran ganancias, "recibes exactamente lo que pediste" pesa mucho más que en
un depósito: que lleguen 97,80 de los 100 que se pidieron, con una cifra que además no se puede
prometer de antemano, es una conversación de soporte por cada retirada.

CCTP v2 soporta Solana desde octubre de 2025, con Fast Transfers de 8 a 20 segundos, y cubre
Ethereum, Base, Arbitrum, Polygon y Optimism, que es justo lo que se pedía. El precio de usarlo es
operarlo: sondeo del servicio de atestación, transferencias atascadas, gas en las dos puntas y una
wallet EVM de operador con fondos. Aviso de calendario: **CCTP v1 se retira el 31 de julio de 2026**,
así que cualquier integración nace en v2.

---

## Recomendación con la que se cerró

No es elegir un solo camino, porque las dos direcciones no tienen por qué usar el mismo:

- **Depósito: Privy.** Gratis, sin KYC, muchas cadenas y muchos activos. No hay nada que ganar
  construyéndolo.
- **Retirada: detrás de una interfaz pequeña.** "Saca X USDC a la dirección Y en la cadena Z" es lo
  único que el resto de la app necesita conocer. Se empieza con el intent de Privy, que son días y
  reutiliza el firmante; se mide la comisión real en la primera retirada de mainnet; y si el número
  es malo, se cambia solo esa pieza por CCTP sin tocar nada más.

Construir CCTP ahora, sin saber qué cobra Privy, es pagar semanas de trabajo y asumir la operación
por un ahorro que todavía no sabemos si existe.

---

## Lo que NO se puede probar en devnet

El bridge es mainnet contra mainnet. En testnet solo existe la ruta `base_sepolia` ↔
`ethereum_sepolia`, y Solana devnet no entra. Como todo nuestro desarrollo es devnet, **la primera
prueba real tiene que ser en mainnet con dinero de verdad**.

---

## Preguntas abiertas

Las dos se contestan con un depósito de 5 o 10 dólares desde Base y una retirada de vuelta, en
mainnet. Es la única forma.

1. **¿Cuánto cobra Privy por retirar?** Es el número que decide entre usar su intent o construir
   CCTP. No está publicado en ningún sitio.
2. **¿Quién paga el gas de Solana en el intent?** Nuestra retirada actual usa el operador como
   pagador con dos firmas. Si el intent exige que la wallet del jugador tenga SOL, es un problema:
   muchas no tienen.
3. **¿La dirección de depósito es realmente permanente en el tiempo, o caduca?** La reutilización
   está confirmada por documentación, pero `waitForDeposit` recibe un `quoteCreatedAt` y no encontré
   nada sobre caducidad de la cotización.

---

## Dónde se dejó

Investigación cerrada, **sin diseño aprobado y sin código**. Lo siguiente es una de estas dos:

- Preparar la prueba en mainnet (depósito desde Base y retirada de vuelta) y medir los números
  reales, o
- Pasar a diseñar el sistema completo asumiendo Privy en ambas direcciones, con CCTP previsto como
  reemplazo de la retirada.

### Fuentes

- <https://privy.io/blog/introducing-universal-deposit-addresses>
- <https://docs.privy.io/wallets/funding/crypto-deposit-addresses>
- <https://docs.privy.io/recipes/relay-deposit-addresses>
- <https://docs.privy.io/wallets/actions/transfer/bridging>
- <https://docs.privy.io/api-reference/intents/transfer>
- <https://docs.privy.io/wallets/overview/embedded>
- <https://www.circle.com/cross-chain-transfer-protocol>
- <https://www.circle.com/paymaster>
