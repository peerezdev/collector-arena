import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { TrackerAccess } from '../../../onchain/gachaClient'

const mocks = vi.hoisted(() => ({
  fetchAcceso: vi.fn(),
  fetchEv: vi.fn(),
  fetchEvLive: vi.fn(),
  identityToken: vi.fn(),
}))
vi.mock('../../../onchain/gachaClient', () => ({
  fetchTrackerAccess: mocks.fetchAcceso,
  fetchEvRows: mocks.fetchEv,
  fetchEvLive: mocks.fetchEvLive,
}))
vi.mock('@privy-io/react-auth', () => ({ useIdentityToken: () => ({ identityToken: mocks.identityToken() }) }))

import { MachineTrackerPage } from './MachineTrackerPage'

const accesoOk: TrackerAccess = {
  allowed: true, wagered_usd: 500, required_usd: 100, missing_usd: 0, window_days: 7,
  via: 'wager', pass_until: null, pass_prices: {},
}
const accesoCerrado: TrackerAccess = {
  allowed: false, wagered_usd: 0, required_usd: 100, missing_usd: 100, window_days: 7,
  via: null, pass_until: null, pass_prices: {},
}

beforeEach(() => {
  localStorage.clear()
  mocks.fetchEv.mockResolvedValue({ rows: [], updated_at: 0 })
  mocks.fetchEvLive.mockResolvedValue({ rows: [], updated_at: 0 })
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('MachineTrackerPage · el acceso no se deja pisar por una respuesta vieja', () => {
  it('una respuesta SIN token que llega DESPUÉS de la que sí tiene token no cierra la puerta', async () => {
    // Reproduce lo que hace Privy en la carga real: `identityToken` llega primero `null` y
    // después el token de verdad, así que hay DOS `fetchTrackerAccess` en vuelo a la vez. Si la
    // de `null` (que el backend responde con `allowed: false`) tarda más que la del token real,
    // su `.then` puede llegar el último y pisar el acceso bueno con la puerta cerrada.
    let resolverSinToken!: (v: TrackerAccess) => void
    let resolverConToken!: (v: TrackerAccess) => void
    mocks.identityToken.mockReturnValue(null)
    mocks.fetchAcceso.mockImplementation((token: string | null | undefined) =>
      token
        ? new Promise<TrackerAccess>((r) => { resolverConToken = r })
        : new Promise<TrackerAccess>((r) => { resolverSinToken = r }))

    const { rerender } = render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)

    // Privy resuelve el token: el efecto de acceso se vuelve a disparar (depende de
    // `identityToken`) y lanza una SEGUNDA petición, con token, mientras la primera sigue viva.
    mocks.identityToken.mockReturnValue('tok')
    rerender(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)

    // La MÁS NUEVA (con token) resuelve primero...
    await act(async () => { resolverConToken(accesoOk) })
    // ...y la VIEJA (sin token) resuelve después. Sin guarda, esta pisaría el acceso bueno.
    await act(async () => { resolverSinToken(accesoCerrado) })

    // La puerta ("... to go") NO debe verse: el jugador tiene acceso de verdad.
    expect(screen.queryByText(/to go/i)).toBeNull()
    // Y sí se ve el panel del tracker (sin filas todavía, pero es el aviso del panel, no la puerta).
    expect(await screen.findByText(/No machines measured yet/i)).toBeTruthy()
  })

  it('al revés: si la de con token es la vieja, gana la más nueva (sin acceso)', async () => {
    // Caso simétrico: si el jugador pierde el pase entre medias, la respuesta que debe mandar es
    // la ÚLTIMA pedida, no la que resuelve primero.
    let resolverConToken!: (v: TrackerAccess) => void
    let resolverSinToken!: (v: TrackerAccess) => void
    mocks.identityToken.mockReturnValue('tok')
    mocks.fetchAcceso.mockImplementation((token: string | null | undefined) =>
      token
        ? new Promise<TrackerAccess>((r) => { resolverConToken = r })
        : new Promise<TrackerAccess>((r) => { resolverSinToken = r }))

    const { rerender } = render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)

    mocks.identityToken.mockReturnValue(null)
    rerender(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)

    // La VIEJA (con token) resuelve primero con acceso...
    await act(async () => { resolverConToken(accesoOk) })
    // ...pero la MÁS NUEVA (sin token) es la que manda, y no da acceso.
    await act(async () => { resolverSinToken(accesoCerrado) })

    expect(await screen.findByText(/to go/i)).toBeTruthy()
  })
})
