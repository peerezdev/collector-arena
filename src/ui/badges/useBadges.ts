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
const BATCH_SIZE = 100
/** How long the queue waits to gather the wallets of every component rendering at once. */
const GATHER_MS = 20

const cache = new Map<string, { badges: Badges; at: number }>()
const queue = new Set<string>()
const inFlight = new Set<string>()
const listeners = new Set<() => void>()
let timer: ReturnType<typeof setTimeout> | null = null

function fresh(w: string): Badges | undefined {
  const e = cache.get(w)
  return e && Date.now() - e.at <= TTL_MS ? e.badges : undefined
}

function notify() {
  for (const l of listeners) l()
}

async function fetchBatch(wallets: string[]) {
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
    for (const w of wallets) inFlight.delete(w)
    notify()
  }
}

function flushQueue() {
  timer = null
  const wallets = [...queue]
  queue.clear()
  for (let i = 0; i < wallets.length; i += BATCH_SIZE) void fetchBatch(wallets.slice(i, i + BATCH_SIZE))
}

function enqueue(wallets: string[]) {
  let queued = false
  for (const w of wallets) {
    if (fresh(w) || inFlight.has(w)) continue
    inFlight.add(w)
    queue.add(w)
    queued = true
  }
  if (queued && !timer) timer = setTimeout(flushQueue, GATHER_MS)
}

function read(wallets: string[]): Record<string, Badges> {
  const out: Record<string, Badges> = {}
  for (const w of wallets) {
    const b = fresh(w)
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
  const [view, setView] = useState(() => read(wallets))

  useEffect(() => {
    const refresh = () => setView(read(wallets))
    listeners.add(refresh)
    enqueue(wallets)
    refresh()
    return () => { listeners.delete(refresh) }
    // `key` captures the wallet list; the array identity changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return view
}

export function __resetBadgesForTests() {
  cache.clear()
  queue.clear()
  inFlight.clear()
  if (timer) clearTimeout(timer)
  timer = null
}
