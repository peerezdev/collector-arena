/**
 * The six rank emblems: ids, names and colours. Thresholds live in the backend
 * (`backend/app/services/badges.py`), which is the only one that decides a rank.
 */
export type RankId = 'bronze' | 'silver' | 'gold' | 'platinum' | 'diamond' | 'obsidian'

export interface RankInfo {
  id: RankId
  name: string
  base: string
  light: string
  dark: string
  /** Colour for the rank's name as text on the app background. */
  text: string
}

export const RANKS: readonly RankInfo[] = [
  { id: 'bronze', name: 'Bronze', base: '#c47f45', light: '#eab184', dark: '#6e3f1c', text: '#eab184' },
  { id: 'silver', name: 'Silver', base: '#b9c2cc', light: '#f1f4f7', dark: '#5d6773', text: '#f1f4f7' },
  { id: 'gold', name: 'Gold', base: '#f0bd3f', light: '#ffe38c', dark: '#8a6112', text: '#ffe38c' },
  { id: 'platinum', name: 'Platinum', base: '#9fd9d3', light: '#e6fbf8', dark: '#3f7c77', text: '#e6fbf8' },
  { id: 'diamond', name: 'Diamond', base: '#62c6ff', light: '#d6f3ff', dark: '#1d6aa3', text: '#d6f3ff' },
  // Its base is too dark to read on the app background, so its text uses the violet edge.
  { id: 'obsidian', name: 'Obsidian', base: '#2b2140', light: '#b99bff', dark: '#b99bff', text: '#b99bff' },
]

const BY_ID = new Map(RANKS.map((r) => [r.id, r]))

export function isRankId(x: unknown): x is RankId {
  return typeof x === 'string' && BY_ID.has(x as RankId)
}

export function rankInfo(id: RankId): RankInfo {
  return BY_ID.get(id)!
}

/** Five-point star: ten points alternating r1 and r2, the first one straight up. */
export function starPoints(cx: number, cy: number, r1: number, r2: number): string {
  const pts: string[] = []
  for (let i = 0; i < 10; i++) {
    const r = i % 2 === 0 ? r1 : r2
    const a = -Math.PI / 2 + (i * Math.PI) / 5
    pts.push(`${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`)
  }
  return pts.join(' ')
}

// Computed once, not on every render.
export const SILVER_STAR_OUTER = starPoints(16, 16.5, 14, 6.2)
export const SILVER_STAR_INNER = starPoints(16, 16.5, 6.5, 2.9)
