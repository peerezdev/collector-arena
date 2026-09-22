import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EmblemaRango } from './EmblemaRango'
import { RANKS } from './ranks'

describe('EmblemaRango', () => {
  it.each(RANKS.map((r) => [r.id, r.name]))('draws %s with its name as label', (id, name) => {
    render(<EmblemaRango rank={id} size={15} />)
    const img = screen.getByRole('img', { name })
    expect(img.getAttribute('width')).toBe('15')
  })

  it('draws nothing without a rank', () => {
    const { container } = render(<EmblemaRango rank={null} size={15} />)
    expect(container.innerHTML).toBe('')
  })

  it('draws nothing for an id it does not know', () => {
    const { container } = render(<EmblemaRango rank="ruby" size={15} />)
    expect(container.innerHTML).toBe('')
  })
})
