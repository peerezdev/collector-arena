// Cliente fino del proxy /gacha/* del backend. La x-api-key vive en el
// backend; aquí solo viajan el token de sesión y datos públicos.
import { config } from './config'

export interface GachaMachine {
  code: string
  name: string
  price: number
  odds: Record<string, number>
  tierRanges?: Record<string, { start: number; end: number }> | null
  stock: Record<string, number>
  ev: number | null
  image: string | null
  shortName?: string | null
  thumbnailUrl?: string | null
  instantBuyback?: number | null
  contains?: number | null
  videoSrc?: string | null
  videoHevc?: string | null
  available?: boolean | null
  turboMode?: boolean | null
  /** Si esta máquina admite tiradas gratis AHORA. No todas las ofrecen, y CC además puede
   *  cerrarlas globalmente; el backend ya combina las dos cosas en esta bandera. */
  freeSpins?: boolean | null
  /** La máquina SÍ las ofrece, pero CC las tiene cerradas ahora mismo. Existe porque la bandera de
   *  arriba, al combinar las dos condiciones, hacía que un cierre temporal se viera exactamente
   *  igual que una máquina que no las da nunca: sin nada en pantalla. */
  freeSpinsClosed?: boolean | null
}

export interface GeneratePackResponse {
  memo: string
  transaction: string // base64, parcialmente firmada (50 USDC)
}

export interface SubmitTxResponse {
  signature: string
  confirmation_status: string
}

export type OpenPackResult =
  | { pending: true }
  | {
      pending: false
      nft_address: string
      rarity: string
      name: string | null
      image: string | null
      year: string | null
      grade: string | null
      images: string[]
      insured_value: number | null
      grading_company: string | null
      grading_id: string | null
      authenticated: boolean | null
      auto_sold: boolean
      buyback_amount: number | null
    }

export class GachaDisabledError extends Error {
  constructor() { super('gacha_disabled') }
}

/** Error del backend que CONSERVA el código HTTP.
 *
 *  Sin él, quien llama solo tiene un texto, y "no has iniciado sesión" acaba tratándose igual que
 *  "Collector Crypt está caído". Eso fue justo lo que dejó la pantalla del gacha sin puntos y sin
 *  botón de tirada gratis, sin decir por qué: ver `useFreeSpins`. */
export class GachaHttpError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function gachaFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${config.backendUrl}${path}`, {
    ...options,
    headers: { ...(options?.headers as Record<string, string> | undefined), 'ngrok-skip-browser-warning': 'true' },
  })
  if (resp.status === 503) throw new GachaDisabledError()
  if (!resp.ok) {
    let detail: string | undefined
    try { detail = (await resp.json())?.detail } catch { /* ignore */ }
    throw new GachaHttpError(resp.status, detail || `Gacha error ${resp.status}`)
  }
  return resp.json() as Promise<T>
}

function authHeaders(token: string): Record<string, string> {
  return { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
}

export function fetchMachines(): Promise<GachaMachine[]> {
  return gachaFetch<GachaMachine[]>('/gacha/machines')
}

export interface MachineCard {
  nft_address: string | null
  name: string | null
  image: string | null
  rarity: string | null
  insured_value: number | null
  grade: string | null
  images: string[]
  grading_company: string | null
  grading_id: string | null
  the_grade: string | null
  generic_grade: string | null
  authenticated: boolean | null
  year: string | null
}

export function fetchMachineCards(
  code: string,
  opts?: { rarity?: string; page?: number; limit?: number },
): Promise<MachineCard[]> {
  const p = new URLSearchParams()
  if (opts?.rarity) p.set('rarity', opts.rarity)
  if (opts?.page != null) p.set('page', String(opts.page))
  p.set('limit', String(opts?.limit ?? 24))
  return gachaFetch<MachineCard[]>(
    `/gacha/machines/${encodeURIComponent(code)}/cards?${p.toString()}`,
  )
}

export interface NftMetadata {
  nft_address: string
  name: string | null
  image: string | null
  rarity: string | null
  insured_value: number | null
  grade: string | null
  grading_company: string | null
  grading_id: string | null
  year: string | null
  authenticated: boolean | null
}

/** Per-mint card metadata from Collector Crypt, proxied by our backend (browser→backend→CC,
 *  so no CORS and the host switches by cluster). DAS metadata is null on devnet, so this is the
 *  reliable source for insuredValue + grading in the inventory.
 *  Memoised by mint (metadata is effectively immutable per card) so the grid and the modal
 *  share one fetch and revisiting is instant. Failures are not cached (they retry). */
const _cardMetaCache = new Map<string, NftMetadata>()
export function fetchCardMetadata(mint: string): Promise<NftMetadata> {
  const cached = _cardMetaCache.get(mint)
  if (cached) return Promise.resolve(cached)
  return gachaFetch<NftMetadata>(`/gacha/nft/${encodeURIComponent(mint)}`).then((m) => {
    _cardMetaCache.set(mint, m)
    return m
  })
}

export function generatePack(token: string, packType: string): Promise<GeneratePackResponse> {
  return gachaFetch<GeneratePackResponse>('/gacha/generate-pack', {
    method: 'POST', headers: authHeaders(token),
    body: JSON.stringify({ pack_type: packType }),
  })
}

/** `memo` solo cuando la tx es la compra de un sobre: permite al backend marcarlo como pagado,
 *  que es lo que distingue un pendiente real de una tirada abandonada sin comprar. */
export function submitTx(token: string, signedTransaction: string, memo?: string): Promise<SubmitTxResponse> {
  return gachaFetch<SubmitTxResponse>('/gacha/submit-tx', {
    method: 'POST', headers: authHeaders(token),
    body: JSON.stringify(memo ? { signed_transaction: signedTransaction, memo } : { signed_transaction: signedTransaction }),
  })
}

export interface BuybackAvailable {
  available: boolean
  amount: number | null // USDC base units (6 decimals)
}

export interface BuybackResponse {
  serialized_transaction: string
  refund_amount: number | null
  memo: string | null
}

export function fetchBuybackAvailable(wallet: string, nft: string): Promise<BuybackAvailable> {
  const p = new URLSearchParams({ wallet, nft })
  return gachaFetch<BuybackAvailable>(`/gacha/buyback/available?${p.toString()}`)
}

export function requestBuyback(token: string, nftAddress: string): Promise<BuybackResponse> {
  return gachaFetch<BuybackResponse>('/gacha/buyback', {
    method: 'POST', headers: authHeaders(token),
    body: JSON.stringify({ nft_address: nftAddress }),
  })
}

export interface NftWithdrawResponse {
  signature: string
  nft_address: string
  address: string
}

/** Send an NFT owned by the user's embedded wallet to an external Solana address. The backend
 *  verifies ownership and signs the transfer with the (delegated) embedded wallet — mirrors the
 *  USDC withdraw. `destAddress` is the destination the user typed. */
export function withdrawNft(token: string, nftAddress: string, destAddress: string): Promise<NftWithdrawResponse> {
  return gachaFetch<NftWithdrawResponse>('/users/me/nft/withdraw', {
    method: 'POST', headers: authHeaders(token),
    body: JSON.stringify({ nft_address: nftAddress, address: destAddress }),
  })
}

/**
 * Una máquina medida por el EV tracker: cuánto ha pagado de verdad en la ventana.
 *
 * Los campos del intervalo y el veredicto pueden venir a null cuando todavía no hay con qué
 * medir. `realized_verdict` es lo que manda: si dice BUILDING o GAP IN WINDOW, el número existe
 * pero NO se sostiene, y la pantalla no debe presentarlo como una conclusión.
 */
export interface EvRow {
  machine: string
  name: string
  pack_price: number
  buyback_pct: number | null
  realized_n_pulls: number
  realized_window_hours: number
  window_complete: boolean
  hours_covered: number
  gaps: string[][]
  realized_edge_pct: number | null
  realized_ci_lo_pct: number | null
  realized_ci_hi_pct: number | null
  realized_verdict: string | null
  pulls_to_conclude: number | null
  tiers: EvTier[]
  /** Lo que la máquina DEBERÍA pagar según sus cartas y las odds que publica CC.
   *
   *  Viene EN VALOR DE CARTA, la misma base que `realized_edge_pct`, para que las dos mitades sean
   *  comparables tal y como llegan y el interruptor de valoración las convierta por igual.
   *
   *  `null` mientras no se haya barrido el pool de esa máquina. No es un cero: un ratio de 0
   *  pintaría la aguja al fondo de la escala como si fuera un robo. */
  model_ev: number | null
  model_ratio: number | null
  model_edge_pct: number | null
}

/** Racha de una rareza: cuántas tiradas lleva sin salir y cuánto suele tardar.
 *
 *  Va sobre el HISTÓRICO ENTERO, no sobre la ventana del EV: una racha se cuenta en tiradas, no en
 *  tiempo, y recortarla a 48 h no la hace más actual, la deja ciega en las máquinas lentas.
 *
 *  NO es una predicción. El gacha usa VRF y cada tirada es independiente, así que una rareza que
 *  lleva 60 sin salir tiene la misma probabilidad en la 61. `cold` solo dice que va por encima de
 *  su propio ritmo. */
export interface EvTier {
  tier: string
  current: number | null      // null = no salió en la muestra, que NO es lo mismo que "n"
  average: number | null
  seen: number
  sample: number
  days_since: number | null   // cuánto tiempo es esa racha; sin esto el número no se puede leer
  cold: boolean
  // ── lo esperado, del pool de cartas. Ausente mientras no se haya barrido esa máquina. ──
  probability?: number | null   // la odd que publica CC
  n_cards?: number              // cuántas cartas de esa rareza quedan en el bote
  value?: number | null         // lo que valen de media
  gross?: number | null         // probability × value: lo que esa rareza aporta al EV
  min_value?: number | null
  max_value?: number | null
}

/** Lo que cambia tirada a tirada. Complementa a `/gacha/ev`, no lo sustituye: el edge, el intervalo
 *  y el veredicto siguen viniendo de allí, porque son caros y no se mueven. */
export interface EvLive {
  machine: string
  tiers: EvTier[]
}

/** Si quien pregunta puede ver el Machine Tracker, y cuánto le falta si no. */
export interface TrackerAccess {
  allowed: boolean
  wagered_usd: number
  required_usd: number
  /** Ya redondeado HACIA ARRIBA al céntimo por el backend: decir "te faltan 0.00" cuando faltan
   *  0.004 haría que el jugador apostara creyendo que ya está y siguiera fuera. */
  missing_usd: number
  window_days: number
  /** Por dónde tiene acceso ahora mismo: apostando, con un pase comprado, o de casa. `null` si no
   *  tiene acceso por ninguna vía. Todavía sin usar en esta pantalla: lo trae ya la tarea que pinta
   *  cuándo caduca el pase junto al UPDATED/STALE de la cabecera. */
  via: 'wager' | 'pass' | 'house' | null
  /** Epoch en segundos hasta el que el pase comprado sigue valiendo, o `null` si no tiene uno. */
  pass_until: number | null
  /** Precio en USDC de cada duración de pase, por ejemplo `{ "7": 10, "30": 30 }`. */
  pass_prices: Record<string, number>
}

/** Sin token también responde: quien no ha entrado recibe `allowed: false` y el aviso le explica
 *  qué hace falta, en vez de un error que sugeriría que algo se ha roto. */
export function fetchTrackerAccess(token?: string | null): Promise<TrackerAccess> {
  return gachaFetch('/gacha/tracker-access', token
    ? { headers: { Authorization: `Bearer ${token}` } }
    : undefined)
}

/** El tracker dejó de ser público cuando se empezó a cobrar por él, así que estas dos llamadas
 *  viajan con token. Un 403 (`GachaHttpError.status`) significa "no tienes acceso", NO que algo
 *  se haya roto: quien llama debe distinguirlo de un fallo de red y enseñar la puerta, no un aviso
 *  de error. */
export function fetchEvLive(token?: string | null): Promise<{ rows: EvLive[]; updated_at: number }> {
  return gachaFetch('/gacha/ev/live', token ? { headers: { Authorization: `Bearer ${token}` } } : undefined)
}

/** Conserva la firma existente en cuanto al parámetro `hours`, para no romper a quien ya la llama
 *  sin pensar en el token (por ejemplo, el replay público). */
export function fetchEvRows(hours?: number, token?: string | null): Promise<{ rows: EvRow[]; updated_at: number }> {
  const q = hours ? `?hours=${hours}` : ''
  return gachaFetch(`/gacha/ev${q}`, token ? { headers: { Authorization: `Bearer ${token}` } } : undefined)
}

/** Compra un pase de acceso al tracker sin necesidad de apostar. `days` es 7 o 30 porque son las
 *  dos únicas duraciones que vende el backend (`pass_prices` trae su precio). */
export function buyTrackerPass(days: 7 | 30, token: string):
  Promise<{ pass_until: number; days: number; price_usdc: number }> {
  return gachaFetch('/gacha/tracker-pass', {
    method: 'POST', headers: authHeaders(token), body: JSON.stringify({ days }),
  })
}

/** Tiradas gratis que Collector Crypt le debe a esta wallet por sus puntos. */
/** Puntos de la wallet, NADA por máquina: cuántas tiradas dan depende del precio de cada una, y
 *  eso lo calcula `tiradasGratis()`. */
export interface FreeSpins {
  points_available: number
  spins_left_today: number
}

export function fetchFreeSpins(token: string): Promise<FreeSpins> {
  return gachaFetch<FreeSpins>('/users/me/free-spins', { headers: { Authorization: `Bearer ${token}` } })
}

/** Canjea una tirada gratis. A diferencia de una de pago no hay nada que firmar en el navegador:
 *  la prueba de propiedad la pone el backend con la wallet delegada, así que devuelve ya el memo
 *  del sobre, listo para abrir con `openPack`. */
export function freePack(token: string, packType: string): Promise<{ memo: string; remaining_points: number | null }> {
  return gachaFetch<{ memo: string; remaining_points: number | null }>('/gacha/free-pack', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ pack_type: packType }),
  })
}

export function openPack(token: string, memo: string): Promise<OpenPackResult> {
  return gachaFetch<OpenPackResult>('/gacha/open-pack', {
    method: 'POST', headers: authHeaders(token),
    body: JSON.stringify({ memo }),
  })
}

/**
 * Vuelve a montar una tirada ya hecha, a partir de su memo.
 *
 * SIN TOKEN a propósito: el enlace tiene que verse sin cuenta, que es justo para lo que existe —
 * pegarlo en un vídeo o en X. El backend comprueba que el memo sea nuestro y limita por IP.
 *
 * No vuelve a abrir nada: `openPack` de Collector Crypt es idempotente, así que esto es una
 * lectura aunque al otro lado sea un POST.
 */
export interface ReplayPull extends Exclude<OpenPackResult, { pending: true }> {
  /** De qué máquina salió ESTA tirada. Va con la tirada porque la pantalla no tiene otra forma de
   *  saberlo: antes usaba la máquina que el jugador tuviera abierta, así que un replay de una
   *  máquina al 90% se veía al 85%, o sin recompra si no tenía ninguna abierta. */
  machine: string | null
  machine_name: string | null
  /** 0-100, como el `instantBuyback` de CC. `null` = no se pudo resolver, y entonces la pantalla
   *  NO enseña recompra: mejor no decir nada que decir el número de otra máquina. */
  buyback_pct: number | null
  pack_price: number | null
}

export function replayPull(memo: string): Promise<ReplayPull> {
  return gachaFetch(`/gacha/replay/${encodeURIComponent(memo)}`)
}

/** El enlace que se comparte. Absoluto, porque va a acabar pegado fuera de la app. */
export function replayHref(memo: string, origin?: string): string {
  const base = origin ?? (typeof window !== 'undefined' ? window.location.origin : '')
  return `${base}/play/gacha?replay=${encodeURIComponent(memo)}`
}

// ── Polling (puro, testeable) ───────────────────────────────────────────────

export function defaultDelayMs(attempt: number): number {
  return Math.min(2000 * 2 ** attempt, 30000)
}

export async function pollOpenPack(
  open: () => Promise<OpenPackResult>,
  opts: { maxAttempts?: number; delayMs?: (attempt: number) => number } = {},
): Promise<OpenPackResult> {
  const maxAttempts = opts.maxAttempts ?? 8
  const delayMs = opts.delayMs ?? defaultDelayMs
  let last: OpenPackResult = { pending: true }
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    last = await open()
    if (!last.pending) return last
    if (attempt < maxAttempts - 1) {
      await new Promise((r) => setTimeout(r, delayMs(attempt)))
    }
  }
  return last
}

/** Public CollectorCrypt asset page for a Solana NFT mint. */
export function ccAssetUrl(mint: string): string {
  return `https://collectorcrypt.com/assets/solana/${mint}`
}

// CollectorCrypt serves the card front image by mint (302 → CDN image; placeholder if missing).
// Usable directly as an <img src>. https://docs.collectorcrypt.com/metadata
//
// El host es POR RED y el de devnet estaba fijo aquí: en mainnet devolvía 404 para todos los
// mints, así que ninguna carta mostraba imagen (reveal, inventario, perfil, buyback). El backend
// ya lo tenía configurable (CC_NFT_BASE_URL); esto es su equivalente en cliente.
export function ccCardImageUrl(mint: string): string {
  return `${config.ccNftBase}/front/${mint}`
}

export interface YoloTx { memo: string; transaction: string }
export interface YoloPacksResponse { yolo_id: string | null; count: number; transactions: YoloTx[] }

export function generateYoloPacks(token: string, packType: string, count: number, turbo: boolean): Promise<YoloPacksResponse> {
  return gachaFetch<YoloPacksResponse>('/gacha/yolo', {
    method: 'POST', headers: authHeaders(token),
    body: JSON.stringify({ pack_type: packType, count, turbo }),
  })
}

export function yoloTotalCost(price: number, count: number): number {
  return price * count
}

export function clampCount(n: number): number {
  return Math.max(1, Math.min(10, Math.floor(n)))
}

/** Cartas que quedan en la máquina, sumando el stock por rareza.
 *
 *  Lo obvio —y lo que se hacía antes— era usar la longitud del array de cartas que pinta la
 *  cuadrícula, pero ese array se pide con `limit`, así que el cartel acababa diciendo siempre
 *  "24" en todas las máquinas: el tamaño de página, no el contenido. El campo `contains` tampoco
 *  vale, es cartas por sobre (siempre 1). Devuelve null si no hay stock que sumar, para poder
 *  ocultar el cartel en vez de afirmar un 0 que quizá no sea cierto. */
export function machineCardCount(stock: Record<string, number> | null | undefined): number | null {
  if (!stock) return null
  const vals = Object.values(stock).filter((v): v is number => typeof v === 'number' && isFinite(v))
  if (vals.length === 0) return null
  return vals.reduce((a, b) => a + b, 0)
}

export interface PendingPack {
  memo: string
  pack_type: string
  submitted_at: string | null
  /** No null = CC ya resolvió el sobre; el reveal se reproduce con lo guardado, sin reabrirlo. */
  nft_address: string | null
  name: string | null
  /** Guardada al abrir: /gacha/nft/{mint} no la trae, así que sin esto un reveal reproducido
   *  no podría mostrarla nunca. */
  rarity: string | null
  insured_value: number | null
  /** Guardados al abrir: con turbo, CC recompra la carta en el acto. Sin esto un reveal
   *  reproducido ofrecía "Keep" y "Sell" de un NFT que ya no es del jugador. */
  auto_sold: boolean
  buyback_amount: number | null
}

/** Sobres ya pagados y sin abrir. Sirve para recuperarlos desde otra pestaña o tras cerrar la
 *  página a mitad de una tirada, que hoy los dejaba huérfanos e invisibles. */
export function fetchPendingPacks(token: string): Promise<PendingPack[]> {
  return gachaFetch<PendingPack[]>('/gacha/packs/pending', { headers: authHeaders(token) })
}

/** Marca sobres como ya vistos por el jugador. Se llama al TERMINAR el reveal, no al empezarlo:
 *  si se cierra a mitad, siguen pendientes y se pueden volver a ver — mejor repetir que perder. */
export function markPacksRevealed(token: string, memos: string[]): Promise<{ marked: number }> {
  return gachaFetch<{ marked: number }>('/gacha/packs/revealed', {
    method: 'POST', headers: authHeaders(token),
    body: JSON.stringify({ memos }),
  })
}

/** Un ganador del feed público de Collector Crypt: qué salió y a quién. */
export interface GachaWinner {
  wallet: string
  nft_address: string
  name: string | null
  images: string[]
  insured_value: number | null
  machine: string | null
  rarity: 'Common' | 'Uncommon' | 'Rare' | 'Epic' | null
  at: string | null
  slug: string | null
}

/**
 * Últimos ganadores de toda la plataforma de CC.
 *
 * `count` llega a 200 como mucho: es el techo de su API y el backend lo rechaza por encima en vez
 * de aceptarlo y devolver menos en silencio. Con `rarity` distinta de Epic pueden salir MENOS de
 * `count`, porque CC no filtra esas rarezas y el recorte se hace después.
 */
export function fetchGachaWinners(
  opts: { machine?: string; rarity?: string; count?: number } = {},
): Promise<GachaWinner[]> {
  const q = new URLSearchParams()
  if (opts.machine) q.set('machine', opts.machine)
  if (opts.rarity) q.set('rarity', opts.rarity)
  q.set('count', String(opts.count ?? 10))
  return gachaFetch<GachaWinner[]>(`/gacha/winners?${q}`)
}

export interface RarityGaps {
  machine: string
  /** Cuántos ganadores se han mirado (200 como mucho: es el techo de CC). */
  sampled: number
  /** rareza → tiradas desde la última vez que salió. `null` = no salió en toda la muestra. */
  gaps: Record<string, number | null>
}

/**
 * Cuántas tiradas lleva cada rareza sin salir en una máquina.
 *
 * Es telemetría, no una predicción: el gacha usa VRF y cada tirada es independiente, así que un
 * hueco largo no hace la rareza más probable.
 */
export function fetchRarityGaps(machine: string): Promise<RarityGaps> {
  return gachaFetch<RarityGaps>(`/gacha/winners/gaps?machine=${encodeURIComponent(machine)}`)
}
