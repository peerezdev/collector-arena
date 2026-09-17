import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const mocks = vi.hoisted(() => ({ isDevnet: false }))
vi.mock('../../../onchain/config', () => ({
  config: { get isDevnet() { return mocks.isDevnet } },
}))

import { ClaimBanner } from './ClaimBanner'

const pintar = () => render(<MemoryRouter><ClaimBanner /></MemoryRouter>)

beforeEach(() => {
  mocks.isDevnet = false
  localStorage.clear()
})

describe('ClaimBanner', () => {
  it('enlaza a /claim', () => {
    pintar()
    expect(screen.getByRole('link', { name: /claim/i }).getAttribute('href')).toBe('/claim')
  })

  it('en devnet no se pinta: el airdrop solo existe en mainnet', () => {
    mocks.isDevnet = true
    const { container } = pintar()
    expect(container.textContent).toBe('')
  })

  it('NO se puede cerrar: no hay ningún botón', () => {
    // Es permanente a propósito, como el aviso de la demo de Royale. El que lo cierra un martes
    // sin mirar es justo el que se queda sin reclamar sus tokens.
    pintar()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('no toca localStorage, ni para leer ni para escribir', () => {
    // Si no se puede cerrar, tampoco hay nada que recordar. Un banner que lee almacenamiento
    // sería la puerta por la que volvería a colarse un estado oculto.
    const getItem = vi.spyOn(Storage.prototype, 'getItem')
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    pintar()
    expect(getItem).not.toHaveBeenCalled()
    expect(setItem).not.toHaveBeenCalled()
    getItem.mockRestore(); setItem.mockRestore()
  })

  it('sigue ahí al volver a montarlo', () => {
    pintar()
    const segundo = pintar()
    expect(segundo.container.textContent).toContain('$CARDS')
  })
})
