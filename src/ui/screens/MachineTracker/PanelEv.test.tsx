import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const mocks = vi.hoisted(() => ({
  fetchWinners: vi.fn().mockResolvedValue([]),
  fetchGaps: vi.fn().mockResolvedValue({ gaps: {}, sampled: 0 }),
  fetchEv: vi.fn(),
  fetchEvLive: vi.fn().mockResolvedValue({ rows: [], updated_at: 0 }),
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

const fila = (machine: string, name: string) => ({
  machine, name, pack_price: 50, buyback_pct: 0.85, realized_n_pulls: 300,
  realized_window_hours: 48, window_complete: true, hours_covered: 48, gaps: [],
  realized_edge_pct: -6, realized_ci_lo_pct: -8, realized_ci_hi_pct: -4,
  realized_verdict: 'CONFIDENT -EV', pulls_to_conclude: null, tiers: [],
})

beforeEach(() => {
  localStorage.clear()
  mocks.fetchEv.mockResolvedValue({
    rows: [fila('pokemon_50', 'Elite Pokémon'), fila('anime_75', 'Anime Pop')],
    updated_at: 0,
  })
})

const abrirSelector = async () => {
  fireEvent.click(await screen.findByRole('button', { name: /machines/i }))
}

describe('PanelEv · elegir qué máquinas ver', () => {
  it('al entrar se ven todas', async () => {
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    expect(await screen.findByRole('button', { name: /2 of 2 machines/i })).toBeTruthy()
  })

  it('ocultar una la quita de la rejilla y lo recuerda', async () => {
    const { unmount } = render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await abrirSelector()
    fireEvent.click(screen.getByRole('checkbox', { name: /Anime Pop/i }))
    await waitFor(() => expect(screen.getByRole('button', { name: /1 of 2 machines/i })).toBeTruthy())

    // Y sobrevive a recargar: es el sentido de guardarla.
    unmount()
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    expect(await screen.findByRole('button', { name: /1 of 2 machines/i })).toBeTruthy()
  })

  it('una máquina NUEVA aparece aunque haya preferencia guardada', async () => {
    // Se guardan las ocultas, no las visibles, justo para esto.
    localStorage.setItem('ba.evTracker.hiddenMachines', '["anime_75"]')
    mocks.fetchEv.mockResolvedValue({
      rows: [fila('pokemon_50', 'Elite Pokémon'), fila('anime_75', 'Anime Pop'),
             fila('nueva_500', 'Recién llegada')],
      updated_at: 0,
    })
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    expect(await screen.findByRole('button', { name: /2 of 3 machines/i })).toBeTruthy()
  })

  it('ocultarlas todas lo dice en vez de dejar el hueco vacío', async () => {
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await abrirSelector()
    fireEvent.click(screen.getByRole('button', { name: /Hide all/i }))
    expect(await screen.findByText(/All machines hidden/i)).toBeTruthy()
  })

  it('"Show all" las devuelve', async () => {
    localStorage.setItem('ba.evTracker.hiddenMachines', '["anime_75","pokemon_50"]')
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await abrirSelector()
    fireEvent.click(screen.getByRole('button', { name: /Show all/i }))
    await waitFor(() => expect(screen.getByRole('button', { name: /2 of 2 machines/i })).toBeTruthy())
  })
})

describe('PanelEv · las dos lecturas de la misma medición', () => {
  const conBuyback = () => ({
    ...fila('pokemon_50', 'Elite Pokémon'),
    buyback_pct: 0.85, realized_edge_pct: 11.5,
    realized_ci_lo_pct: 8, realized_ci_hi_pct: 15, realized_verdict: 'CONFIDENT +EV',
  })

  it('por defecto mide a precio de recompra', async () => {
    mocks.fetchEv.mockResolvedValue({ rows: [conBuyback()], updated_at: 0 })
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    // 0.85 × 1.115 − 1 = −5.2% → ratio 0.948
    expect(await screen.findByText('0.948')).toBeTruthy()
    expect(screen.getByText(/AT BUYBACK/)).toBeTruthy()
  })

  it('cambiar a "me la quedo" enseña el valor de la carta', async () => {
    mocks.fetchEv.mockResolvedValue({ rows: [conBuyback()], updated_at: 0 })
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: /if you keep it/i }))
    await waitFor(() => expect(screen.getByText('1.115')).toBeTruthy())
    expect(screen.getByText(/AT CARD VALUE/)).toBeTruthy()
  })

  it('el veredicto cambia con el modo, no solo el número', async () => {
    // Es lo que hace honesto el interruptor: a valor de carta esta máquina paga, a precio de
    // recompra no. Las dos cosas son ciertas y la conclusión tiene que seguir al número.
    mocks.fetchEv.mockResolvedValue({ rows: [conBuyback()], updated_at: 0 })
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    expect(await screen.findByText('CONFIRMED −EV')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /if you keep it/i }))
    await waitFor(() => expect(screen.getByText('CONFIRMED +EV')).toBeTruthy())
  })

  it('la elección se recuerda', async () => {
    mocks.fetchEv.mockResolvedValue({ rows: [conBuyback()], updated_at: 0 })
    const { unmount } = render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: /if you keep it/i }))
    await waitFor(() => expect(screen.getByText('1.115')).toBeTruthy())
    unmount()
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    expect(await screen.findByText('1.115')).toBeTruthy()
  })
})

describe('PanelEv · the order the user gives it', () => {
  /** The names of the grid's cards, in the order they are painted. */
  const enPantalla = () =>
    screen.getAllByRole('article').map((a) => a.querySelector('span:nth-of-type(2)')?.textContent)

  it('on entry, the server order rules', async () => {
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await screen.findByRole('button', { name: /2 of 2 machines/i })
    expect(enPantalla()).toEqual(['Elite Pokémon', 'Anime Pop'])
  })

  it('re-ticking a machine sends it to THE END', async () => {
    // This is the click ordering that was asked for: untick, tick, and it comes back in last.
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await abrirSelector()
    const casilla = screen.getByRole('checkbox', { name: /Elite Pokémon/i })
    fireEvent.click(casilla)                              // se oculta
    await waitFor(() => expect(enPantalla()).toEqual(['Anime Pop']))
    fireEvent.click(casilla)                              // back on, and it goes last
    await waitFor(() => expect(enPantalla()).toEqual(['Anime Pop', 'Elite Pokémon']))
  })

  it('the order survives reloading the page', async () => {
    const { unmount } = render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await abrirSelector()
    const casilla = screen.getByRole('checkbox', { name: /Elite Pokémon/i })
    fireEvent.click(casilla)
    fireEvent.click(casilla)
    await waitFor(() => expect(enPantalla()).toEqual(['Anime Pop', 'Elite Pokémon']))

    unmount()
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await screen.findByRole('button', { name: /2 of 2 machines/i })
    expect(enPantalla()).toEqual(['Anime Pop', 'Elite Pokémon'])
  })

  it('a new machine from the server goes BEHIND what is already placed, without displacing it', async () => {
    // Same logic as the hidden ones: whatever the user has not decided is left alone.
    localStorage.setItem('ba.evTracker.orden', '["anime_75","pokemon_50"]')
    mocks.fetchEv.mockResolvedValue({
      rows: [fila('pokemon_50', 'Elite Pokémon'), fila('anime_75', 'Anime Pop'),
             fila('nueva_500', 'Recién llegada')],
      updated_at: 0,
    })
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await screen.findByRole('button', { name: /3 of 3 machines/i })
    expect(enPantalla()).toEqual(['Anime Pop', 'Elite Pokémon', 'Recién llegada'])
  })

  it('"Reset order" gives back the server order', async () => {
    localStorage.setItem('ba.evTracker.orden', '["anime_75","pokemon_50"]')
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await abrirSelector()
    fireEvent.click(screen.getByRole('button', { name: /reset order/i }))
    await waitFor(() => expect(enPantalla()).toEqual(['Elite Pokémon', 'Anime Pop']))
    expect(localStorage.getItem('ba.evTracker.orden')).toBe('[]')
  })

  it('without an order of its own, "Reset order" is not offered', async () => {
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await abrirSelector()
    expect(screen.queryByRole('button', { name: /reset order/i })).toBeNull()
  })

  it('each card links to its pulls, with the machine already filtered', async () => {
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    const enlaces = await screen.findAllByRole('link', { name: /recent pulls/i })
    expect(enlaces.map((a) => a.getAttribute('href')))
      .toEqual(['/winners?machine=pokemon_50', '/winners?machine=anime_75'])
  })
})

describe('PanelEv · dragging a card', () => {
  const enPantalla = () =>
    screen.getAllByRole('article').map((a) => a.querySelector('span:nth-of-type(2)')?.textContent)

  const arrastrar = (desde: number, hasta: number) => {
    const tarjetas = screen.getAllByRole('article')
    fireEvent.dragStart(tarjetas[desde])
    // The container is what listens for the drop; the card only starts the drag.
    const destino = tarjetas[hasta].parentElement as HTMLElement
    fireEvent.dragOver(destino)
    fireEvent.drop(destino)
  }

  it('dropping a card onto another places it there', async () => {
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await screen.findByRole('button', { name: /2 of 2 machines/i })
    expect(enPantalla()).toEqual(['Elite Pokémon', 'Anime Pop'])
    arrastrar(0, 1)
    await waitFor(() => expect(enPantalla()).toEqual(['Anime Pop', 'Elite Pokémon']))
  })

  it('the first drag places ALL of them, not just the one moved', async () => {
    // Without materialising, the moved card would be the only one with a position and the rest
    // would trail behind in the server's order, which is exactly what was just undone.
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await screen.findByRole('button', { name: /2 of 2 machines/i })
    arrastrar(0, 1)
    await waitFor(() =>
      expect(localStorage.getItem('ba.evTracker.orden')).toBe('["anime_75","pokemon_50"]'))
  })

  it('dropping a card onto itself changes nothing', async () => {
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await screen.findByRole('button', { name: /2 of 2 machines/i })
    arrastrar(1, 1)
    await waitFor(() => expect(enPantalla()).toEqual(['Elite Pokémon', 'Anime Pop']))
  })

  it('dropping without having dragged anything does not reorder', async () => {
    // A `drop` can arrive from outside the browser (a file, a text selection).
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await screen.findByRole('button', { name: /2 of 2 machines/i })
    const destino = screen.getAllByRole('article')[1].parentElement as HTMLElement
    fireEvent.drop(destino)
    await waitFor(() => expect(enPantalla()).toEqual(['Elite Pokémon', 'Anime Pop']))
    expect(localStorage.getItem('ba.evTracker.orden')).toBeNull()
  })
})
