import { describe, it, expect } from 'vitest'
import { RANKS, isRankId, rankInfo, starPoints } from './ranks'

describe('ranks', () => {
  it('has the six ranks in order', () => {
    expect(RANKS.map((r) => r.id)).toEqual(['bronze', 'silver', 'gold', 'platinum', 'diamond', 'obsidian'])
    expect(RANKS.map((r) => r.name)).toEqual(['Bronze', 'Silver', 'Gold', 'Platinum', 'Diamond', 'Obsidian'])
  })

  it('recognises only known ids', () => {
    expect(isRankId('gold')).toBe(true)
    expect(isRankId('ruby')).toBe(false)
    expect(isRankId(null)).toBe(false)
  })

  it('uses the light colour for text, violet for obsidian', () => {
    expect(rankInfo('gold').text).toBe('#ffe38c')
    expect(rankInfo('obsidian').text).toBe('#b99bff')
  })

  it('starts the star at the top and alternates radii', () => {
    const pts = starPoints(16, 16.5, 14, 6.2).split(' ')
    expect(pts).toHaveLength(10)
    expect(pts[0]).toBe('16.00,2.50')
  })
})
