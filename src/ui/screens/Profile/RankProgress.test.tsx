import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RankProgress } from './RankProgress'

describe('RankProgress', () => {
  it('shows the rank name and the way to the next rank', () => {
    render(<RankProgress rank="silver" progress={{ wageredUsd: 7340, nextRank: 'gold', nextThresholdUsd: 10000 }} />)
    expect(screen.getByText('Silver')).toBeTruthy()
    expect(screen.getByText(/\$7,340 wagered/)).toBeTruthy()
    expect(screen.getByText(/\$10,000 for Gold/)).toBeTruthy()
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('73')
  })

  it('at Obsidian shows the name and no bar', () => {
    render(<RankProgress rank="obsidian" progress={{ wageredUsd: 150000, nextRank: null, nextThresholdUsd: null }} />)
    expect(screen.getByText('Obsidian')).toBeTruthy()
    expect(screen.queryByRole('progressbar')).toBeNull()
  })

  it('under 500 aims at Bronze with no rank name', () => {
    render(<RankProgress rank={null} progress={{ wageredUsd: 120, nextRank: 'bronze', nextThresholdUsd: 500 }} />)
    expect(screen.getByText(/\$500 for Bronze/)).toBeTruthy()
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('24')
  })

  it('renders nothing without progress data', () => {
    const { container } = render(<RankProgress rank={null} progress={null} />)
    expect(container.innerHTML).toBe('')
  })
})
