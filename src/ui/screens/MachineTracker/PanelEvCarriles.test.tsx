import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const mocks = vi.hoisted(() => ({
  fetchWinners: vi.fn().mockResolvedValue([]),
  fetchGaps: vi.fn().mockResolvedValue({ gaps: {}, sampled: 0 }),
  fetchEv: vi.fn(),
  fetchEvLive: vi.fn(),
  fetchAcceso: vi.fn().mockResolvedValue({ allowed: true, wagered_usd: 500, required_usd: 100, missing_usd: 0, window_days: 7 }),
}))
vi.mock('../../../onchain/gachaClient', () => ({
  fetchGachaWinners: mocks.fetchWinners,
  fetchRarityGaps: mocks.fetchGaps,
  fetchEvRows: mocks.fetchEv,
  fetchEvLive: mocks.fetchEvLive,
  fetchTrackerAccess: mocks.fetchAcceso,
}))
vi.mock('@privy-io/react-auth', () => ({ useIdentityToken: () => ({ identityToken: 'tok' }) }))
vi.mock('../../useMachines', () => ({ useMachineList: () => ({ machines: [] }) }))
vi.mock('../../useAliases', () => ({ useAliases: () => ({}) }))

import { MachineTrackerPage } from './MachineTrackerPage'

const tier = (current: number) => ({
  tier: 'Epic', current, average: 165.7, seen: 12, sample: 2000, days_since: 0.1, cold: false,
})

const fila = {
  machine: 'pokemon_50', name: 'Elite Pokémon', pack_price: 50, buyback_pct: 0.85,
  realized_n_pulls: 3068, realized_window_hours: 48, window_complete: true, hours_covered: 48,
  gaps: [], realized_edge_pct: 6.65, realized_ci_lo_pct: 3.29, realized_ci_hi_pct: 10.14,
  realized_verdict: 'CONFIDENT +EV', pulls_to_conclude: null, tiers: [tier(80)],
}

/** Deja correr los temporizadores y las promesas que disparan.
 *
 *  Aquí NO se usa `waitFor` ni `findBy*`: con temporizadores falsos se quedan esperando un
 *  `setTimeout` que ya está intervenido y el test se cuelga hasta agotar el tiempo. Avanzando el
 *  reloj a mano se vacían las promesas pendientes y ya se puede mirar la pantalla. */
const avanzar = async (ms: number) => {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

const ocultarPestaña = (oculta: boolean) => {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true, get: () => (oculta ? 'hidden' : 'visible'),
  })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  localStorage.clear()
  vi.useFakeTimers()
  mocks.fetchEv.mockResolvedValue({ rows: [fila], updated_at: 0 })
  mocks.fetchEvLive.mockResolvedValue({ rows: [], updated_at: 0 })
})

afterEach(() => {
  ocultarPestaña(false)
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('PanelEv · los dos carriles del refresco', () => {
  it('lo barato se pide seis veces por cada vez que lo caro', async () => {
    // Es la razón de separarlos: el bootstrap son ~9 s de CPU las 48 máquinas y no se mueve; las
    // rachas cuestan ~370 ms y cambian con cada tirada.
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(mocks.fetchEv).toHaveBeenCalledTimes(1)
    await avanzar(60_000)
    expect(mocks.fetchEv).toHaveBeenCalledTimes(2)
    expect(mocks.fetchEvLive).toHaveBeenCalledTimes(6)
  })

  it('la racha nueva llega a la pantalla sin esperar al carril lento', async () => {
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(screen.getByText('80')).toBeTruthy()
    mocks.fetchEvLive.mockResolvedValue({
      rows: [{ machine: 'pokemon_50', tiers: [tier(81)] }], updated_at: 0,
    })
    await avanzar(10_000)
    expect(screen.getByText('81')).toBeTruthy()
    expect(mocks.fetchEv).toHaveBeenCalledTimes(1)      // sin recalcular el bootstrap
  })

  it('en segundo plano no se pide nada', async () => {
    // Una pestaña olvidada estaría sondeando toda la noche para que no la mire nadie.
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(mocks.fetchEv).toHaveBeenCalledTimes(1)
    ocultarPestaña(true)
    await avanzar(120_000)
    expect(mocks.fetchEv).toHaveBeenCalledTimes(1)
    expect(mocks.fetchEvLive).not.toHaveBeenCalled()
  })

  it('al volver a la pestaña se refresca ya, sin esperar al siguiente tic', async () => {
    // Si no, se vería hasta un minuto de datos viejos justo cuando alguien acaba de mirar.
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(mocks.fetchEv).toHaveBeenCalledTimes(1)
    ocultarPestaña(true)
    await avanzar(120_000)
    ocultarPestaña(false)
    await avanzar(0)
    expect(mocks.fetchEv).toHaveBeenCalledTimes(2)
    expect(mocks.fetchEvLive).toHaveBeenCalledTimes(1)
  })

  it('si falla el carril rápido, la tarjeta sigue con lo que tenía', async () => {
    // Es un extra: no puede tumbar lo que sí se ha medido.
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(screen.getByText('80')).toBeTruthy()
    mocks.fetchEvLive.mockRejectedValue(new Error('sin red'))
    await avanzar(10_000)
    expect(screen.getByText('80')).toBeTruthy()
  })

  it('si falla un sondeo del carril lento, no se borra la pantalla', async () => {
    // Lo de antes sigue siendo cierto; vaciarla por un fallo de red pasajero es peor que
    // enseñarla un minuto más vieja.
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(screen.getByText('Elite Pokémon')).toBeTruthy()
    mocks.fetchEv.mockRejectedValue(new Error('sin red'))
    await avanzar(60_000)
    expect(screen.getByText('Elite Pokémon')).toBeTruthy()
  })
})

describe('PanelEv · el orden propio y el refresco', () => {
  const otra = { ...fila, machine: 'anime_75', name: 'Anime Pop', realized_edge_pct: -6 }
  const enPantalla = () =>
    screen.getAllByRole('article').map((a) => a.querySelector('span:nth-of-type(2)')?.textContent)

  it('el orden del usuario SOBREVIVE al refresco, aunque cambie el edge', async () => {
    // El servidor ordena por edge, así que sin orden propio la rejilla se recoloca sola cada 10 s
    // y las tarjetas bailan mientras las estás leyendo. Con orden propio, se queda quieta.
    localStorage.setItem('ba.evTracker.orden', '["anime_75","pokemon_50"]')
    mocks.fetchEv.mockResolvedValue({ rows: [fila, otra], updated_at: 0 })
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(enPantalla()).toEqual(['Anime Pop', 'Elite Pokémon'])

    // El servidor cambia de opinión y las manda al revés.
    mocks.fetchEv.mockResolvedValue({ rows: [otra, fila], updated_at: 0 })
    await avanzar(60_000)
    expect(enPantalla()).toEqual(['Anime Pop', 'Elite Pokémon'])
  })

  it('sin orden propio se sigue respetando al servidor', async () => {
    mocks.fetchEv.mockResolvedValue({ rows: [fila, otra], updated_at: 0 })
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(enPantalla()).toEqual(['Elite Pokémon', 'Anime Pop'])

    mocks.fetchEv.mockResolvedValue({ rows: [otra, fila], updated_at: 0 })
    await avanzar(60_000)
    expect(enPantalla()).toEqual(['Anime Pop', 'Elite Pokémon'])
  })
})
