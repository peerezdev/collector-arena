import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

const estado = vi.hoisted(() => ({ badges: {} as Record<string, { rank: string | null; tags: string[] }> }))
vi.mock('./useBadges', () => ({ useBadges: () => estado.badges }))

import { NombreUsuario } from './NombreUsuario'

beforeEach(() => { estado.badges = {} })

describe('NombreUsuario', () => {
  it('shows emblem, name and tags', () => {
    estado.badges = { A: { rank: 'gold', tags: ['TEAM'] } }
    render(<NombreUsuario wallet="A" size={15}><a href="/profile/A">kairo</a></NombreUsuario>)
    expect(screen.getByRole('img', { name: 'Gold' })).toBeTruthy()
    expect(screen.getByText('TEAM')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'kairo' })).toBeTruthy()   // emblem not in the link
  })

  it('shows only the name while loading or after a failure', () => {
    render(<NombreUsuario wallet="A" size={15}><span>kairo</span></NombreUsuario>)
    expect(screen.getByText('kairo')).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('shows tags without a rank', () => {
    estado.badges = { A: { rank: null, tags: ['TEAM'] } }
    render(<NombreUsuario wallet="A" size={15}><span>kairo</span></NombreUsuario>)
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText('TEAM')).toBeTruthy()
  })
})
