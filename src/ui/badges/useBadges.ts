import { useEffect, useState } from 'react'
import { config } from '../../onchain/config'
import { isRankId, type RankId } from './ranks'

export interface Badges {
  rank: RankId | null
  tags: string[]
}

/** A rank or tag change shows up within this long without a reload. */
const TTL_MS = 5 * 60_000
/** The backend's limit per request (GET /users/badges answers 422 above it). */
const LOTE = 100
/** How long the queue waits to gather the wallets of every component rendering at once. */
const ESPERA_MS = 20

const cache = new Map<string, { badges: Badges; at: number }>()
const cola = new Set<string>()
const enVuelo = new Set<string>()
const oyentes = new Set<() => void>()
let temporizador: ReturnType<typeof setTimeout> | null = null

function fresco(w: string): Badges | undefined {
  const e = cache.get(w)
  return e && Date.now() - e.at <= TTL_MS ? e.badges : undefined
}

function avisar() {
  for (const o of oyentes) o()
}

async function pedirLote(wallets: string[]) {
  try {
    const url = `${config.backendUrl}/users/badges?wallets=${encodeURIComponent(wallets.join(','))}`
    const r = await fetch(url, { headers: { 'ngrok-skip-browser-warning': 'true' } })
    if (!r.ok) return
    const body = (await r.json()) as Record<string, { rank?: unknown; tags?: unknown }>
    const at = Date.now()
    for (const w of wallets) {
      const d = body[w]
      if (!d) continue
      cache.set(w, {
        at,
        badges: {
          rank: isRankId(d.rank) ? d.rank : null,
          tags: Array.isArray(d.tags) ? d.tags.filter((t): t is string => typeof t === 'string') : [],
        },
      })
    }
  } catch {
    // Nothing cached: the names render without emblems, and the next mount asks again.
  } finally {
    for (const w of wallets) enVuelo.delete(w)
    avisar()
  }
}

function vaciarCola() {
  temporizador = null
  const wallets = [...cola]
  cola.clear()
  for (let i = 0; i < wallets.length; i += LOTE) void pedirLote(wallets.slice(i, i + LOTE))
}

function encolar(wallets: string[]) {
  let alguna = false
  for (const w of wallets) {
    if (fresco(w) || enVuelo.has(w)) continue
    enVuelo.add(w)
    cola.add(w)
    alguna = true
  }
  if (alguna && !temporizador) temporizador = setTimeout(vaciarCola, ESPERA_MS)
}

function leer(wallets: string[]): Record<string, Badges> {
  const out: Record<string, Badges> = {}
  for (const w of wallets) {
    const b = fresco(w)
    if (b) out[w] = b
  }
  return out
}

/**
 * Rank and tags per wallet. Only wallets already resolved are in the result; a missing wallet
 * means "nothing to show yet", and callers render the bare name.
 */
export function useBadges(wallets: string[]): Record<string, Badges> {
  const key = wallets.join(',')
  const [vista, setVista] = useState(() => leer(wallets))

  useEffect(() => {
    const refrescar = () => setVista(leer(wallets))
    oyentes.add(refrescar)
    encolar(wallets)
    refrescar()
    return () => { oyentes.delete(refrescar) }
    // `key` captures the wallet list; the array identity changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return vista
}

export function __resetBadgesForTests() {
  cache.clear()
  cola.clear()
  enVuelo.clear()
  if (temporizador) clearTimeout(temporizador)
  temporizador = null
}
