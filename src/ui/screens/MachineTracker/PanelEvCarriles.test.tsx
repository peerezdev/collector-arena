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

describe('PanelEv · the user\'s own order and the refresh', () => {
  const otra = { ...fila, machine: 'anime_75', name: 'Anime Pop', realized_edge_pct: -6 }
  const enPantalla = () =>
    screen.getAllByRole('article').map((a) => a.querySelector('span:nth-of-type(2)')?.textContent)

  it('the user\'s order SURVIVES the refresh, even if the edge changes', async () => {
    // The server sorts by edge, so without an order of your own the grid rearranges itself every
    // 10 s and the cards dance while you are reading them. With your own order, it stays put.
    localStorage.setItem('ba.evTracker.orden', '["anime_75","pokemon_50"]')
    mocks.fetchEv.mockResolvedValue({ rows: [fila, otra], updated_at: 0 })
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(enPantalla()).toEqual(['Anime Pop', 'Elite Pokémon'])

    // The server changes its mind and sends them the other way round.
    mocks.fetchEv.mockResolvedValue({ rows: [otra, fila], updated_at: 0 })
    await avanzar(60_000)
    expect(enPantalla()).toEqual(['Anime Pop', 'Elite Pokémon'])
  })

  it('without an order of its own, the server is still respected', async () => {
    mocks.fetchEv.mockResolvedValue({ rows: [fila, otra], updated_at: 0 })
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(enPantalla()).toEqual(['Elite Pokémon', 'Anime Pop'])

    mocks.fetchEv.mockResolvedValue({ rows: [otra, fila], updated_at: 0 })
    await avanzar(60_000)
    expect(enPantalla()).toEqual(['Anime Pop', 'Elite Pokémon'])
  })
})

describe('PanelEv · a 403 on either lane leads to the gate', () => {
  const accesoOk = { allowed: true, wagered_usd: 500, required_usd: 100, missing_usd: 0, window_days: 7 }
  const accesoCerrado = { allowed: false, wagered_usd: 0, required_usd: 100, missing_usd: 100, window_days: 7 }
  const error403 = () => Object.assign(new Error('tracker_locked'), { status: 403 })

  /** Mounting must ALWAYS grant access (otherwise there is no `PanelEv` to test anything with);
   *  only a LATER call to `fetchTrackerAccess`, the one `onSinAcceso` fires, can close the gate.
   *  Counting calls instead of chaining `mockResolvedValueOnce` avoids depending on HOW MANY
   *  times it is called before the one that matters, which is exactly what the control test's
   *  mutant would call into question. */
  const accesoSegúnLlamada = () => {
    let llamadas = 0
    mocks.fetchAcceso.mockImplementation(() => {
      llamadas += 1
      return Promise.resolve({ ...(llamadas === 1 ? accesoOk : accesoCerrado) })
    })
  }

  afterEach(() => {
    // This describe swaps `fetchAcceso`'s default response for one that counts calls; it is
    // restored to the fixed value the rest of the file uses so it does not leak into another
    // test.
    mocks.fetchAcceso.mockReset()
    mocks.fetchAcceso.mockResolvedValue({ allowed: true, wagered_usd: 500, required_usd: 100, missing_usd: 0, window_days: 7 })
  })

  it('the slow lane returns 403 → the gate appears, not the failure notice', async () => {
    accesoSegúnLlamada()
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(screen.getByText('Elite Pokémon')).toBeTruthy()

    mocks.fetchEv.mockRejectedValueOnce(error403())
    await avanzar(60_000)
    // Second turn of the clock: the 403 fires `onSinAcceso`, which makes a new and CHAINED
    // `fetchTrackerAccess` (not tied to any timer). With fake timers, `avanzar` only guarantees
    // draining whatever hangs off the tick that just ran, so this second `avanzar(0)` is the one
    // that drains that second promise and leaves the gate painted.
    await avanzar(0)

    expect(screen.getByText(/to go/i)).toBeTruthy()
    expect(screen.queryByText(/Couldn't load the tracker/i)).toBeNull()
  })

  it('the fast lane returns 403 → the gate appears, not the failure notice', async () => {
    accesoSegúnLlamada()
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(screen.getByText('Elite Pokémon')).toBeTruthy()

    mocks.fetchEvLive.mockRejectedValueOnce(error403())
    await avanzar(10_000)
    await avanzar(0) // see the comment in the slow lane test

    expect(screen.getByText(/to go/i)).toBeTruthy()
    expect(screen.queryByText(/Couldn't load the tracker/i)).toBeNull()
  })

  it('a failure that is NOT a 403 on the first load shows the notice, not the gate', async () => {
    // Control: without this test, the two above could pass with a `catch` that sent ANY error
    // to the gate, not just the 403. Here a real network error on the FIRST load has to produce
    // the breakage notice, and the gate must stay as it was, that is, neither opening nor
    // closing: access already granted must not be touched.
    accesoSegúnLlamada()
    mocks.fetchEv.mockRejectedValueOnce(new Error('sin red'))
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    await avanzar(0)

    expect(screen.getByText(/Couldn't load the tracker/i)).toBeTruthy()
    expect(screen.queryByText(/to go/i)).toBeNull()
  })
})

describe('the pass says how long is left, not just a date', () => {
  // This file runs on fake timers: `findBy*` would wait on a clock that is already intervened,
  // so the screen is read after `avanzar`, like every other test here.
  const conPase = (hasta: number) => {
    mocks.fetchAcceso.mockResolvedValue({
      allowed: true, wagered_usd: 0, required_usd: 100, missing_usd: 100, window_days: 7,
      via: 'pass', pass_until: hasta, pass_prices: {},
    })
  }

  it('shows the days left and the date it ends', async () => {
    // A date on its own makes you count on your fingers. What you want to know is whether it is
    // running out, and only then which day.
    const dentroDe6Dias = Math.floor(Date.now() / 1000) + 6 * 86_400
    conPase(dentroDe6Dias)
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)

    const etiqueta = screen.getByTitle(/pass/i)
    expect(etiqueta.textContent).toMatch(/6 days left/i)
    expect(etiqueta.textContent).toContain(new Date(dentroDe6Dias * 1000).toLocaleDateString())
  })

  it('on the last day it says hours, because days would round to zero', async () => {
    conPase(Math.floor(Date.now() / 1000) + 5 * 3600)
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)

    expect(screen.getByTitle(/pass/i).textContent).toMatch(/5 hours left/i)
  })

  it('whoever got in by wagering is shown no date, because there is none to show', async () => {
    // The wager window reopens the gate on its own when it stops being met: there is no date
    // anybody can plan around, and inventing one would be a lie.
    mocks.fetchAcceso.mockResolvedValue({
      allowed: true, wagered_usd: 500, required_usd: 100, missing_usd: 0, window_days: 7,
      via: 'wager', pass_until: null, pass_prices: {},
    })
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)

    expect(screen.getByText('Elite Pokémon')).toBeTruthy()
    expect(screen.queryByTitle(/pass/i)).toBeNull()
  })
})
