import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { TrackerAccess } from '../../../onchain/gachaClient'

const { TrackerGate } = await import('./TrackerGate')

const pintar = (a: TrackerAccess) =>
  render(<MemoryRouter><TrackerGate acceso={a} /></MemoryRouter>)

const acceso = (over: Partial<TrackerAccess> = {}): TrackerAccess => ({
  allowed: false, wagered_usd: 60, required_usd: 100, missing_usd: 40, window_days: 7,
  via: null, pass_until: null, pass_prices: {}, ...over,
})

describe('el aviso del Machine Tracker', () => {
  it('detrás del cristal NO hay datos reales', () => {
    // Es lo que sostiene la puerta: difuminar las tarjetas de verdad sería una puerta de mentira,
    // porque el blur es CSS y se quita desde el navegador, y los números viajarían igual.
    const { container } = pintar(acceso())
    // Los valores del fondo son inventados y están fijados en el módulo, no medidos.
    expect(container.textContent).toContain('1.194')
    // Y el fondo es inerte: ni se puede pulsar ni se puede seleccionar.
    const fondo = container.querySelector('[aria-hidden][style*="blur"]') as HTMLElement
    expect(fondo).toBeTruthy()
    expect(fondo.getAttribute('style')).toContain('pointer-events: none')
    expect(fondo.getAttribute('style')).toContain('user-select: none')
  })

  it('NO enseña lo que hay detrás del candado', () => {
    // Se pidió expresamente que no saliera: enseñar "ahora mismo esta máquina paga 1.19" es
    // regalar justo el dato por el que se pide el wager.
    const { container } = pintar(acceso())
    expect(container.textContent).not.toContain('Right now behind')
  })

  it('lo primero que dice es cuánto falta', () => {
    // Es el número con el que el jugador decide. El motivo de la puerta va después: nadie lee la
    // justificación antes de saber si le afecta.
    pintar(acceso())
    expect(screen.getByText('$40 to go')).toBeTruthy()
  })

  it('dice sobre cuánto y en cuántos días', () => {
    // Por textContent: la cifra va en un <strong>, así que el texto está partido en varios nodos.
    const { container } = pintar(acceso())
    expect(container.textContent).toContain('$100')
    expect(container.textContent).toMatch(/in\s+Pack Battles or Battle Royale over the last 7 days/)
  })

  it('dice explícitamente que el gacha NO cuenta', () => {
    // Sin esto, alguien abriría sobres esperando avanzar y no avanzaría.
    const { container } = pintar(acceso())
    expect(container.textContent).toContain("Gacha pulls don't count")
  })

  it('explica que la ventana es RODANTE', () => {
    // La alternativa —creer que es un acceso que se gana una vez— lleva a perderlo sin haber hecho
    // nada y no entender por qué.
    pintar(acceso())
    expect(screen.getByText(/Rolling 7-day window/)).toBeTruthy()
    expect(screen.getByText(/anything older drops off/)).toBeTruthy()
  })

  it('enseña lo que ya lleva apostado, no solo lo que falta', () => {
    // "Voy por 60 de 100" motiva; "te faltan 40" a secas no dice si estás cerca de empezar o de
    // terminar.
    pintar(acceso())
    expect(screen.getByText('$60 wagered')).toBeTruthy()
  })

  it('con cero apostado no revienta ni pinta una barra rara', () => {
    const { container } = pintar(acceso({ wagered_usd: 0, missing_usd: 100 }))
    expect(screen.getByText('$100 to go')).toBeTruthy()
    expect(container.innerHTML).toContain('width: 0%')
  })

  it('la barra no se pasa del 100% si se apostó de más', () => {
    const { container } = pintar(acceso({ wagered_usd: 250, missing_usd: 0 }))
    expect(container.innerHTML).toContain('width: 100%')
  })

  it('usa los valores que trae el backend, no constantes propias', () => {
    // Si el mínimo o la ventana cambian en el servidor, el aviso tiene que seguirlos sin tocar
    // esta pantalla; dos sitios con el mismo número se desincronizan.
    const { container } = pintar(acceso({ required_usd: 250, window_days: 14, missing_usd: 190, wagered_usd: 60 }))
    expect(container.textContent).toMatch(/\$250\s*in Pack Battles/)
    expect(screen.getByText(/Rolling 14-day window/)).toBeTruthy()
    expect(screen.getByText('$190 to go')).toBeTruthy()
    // Las marcas de la barra también salen del mínimo del backend, no de constantes propias.
    expect(screen.getByText('$125')).toBeTruthy()
  })
})

// ── por dónde seguir ─────────────────────────────────────────────────────────

describe('el botón de la puerta', () => {
  it('es UNO solo y lleva al Lobby', () => {
    // Antes eran dos, uno por modo, y los dos llevaban al mismo sitio: dos caminos para una
    // decisión que se toma igual, mirando qué hay abierto. En el Lobby están las dos listas.
    pintar(acceso())
    const botones = screen.getAllByRole('link')
    expect(botones).toHaveLength(1)
    expect(botones[0].getAttribute('href')).toBe('/play/lobby')
    expect(botones[0].textContent).toMatch(/Find a match/)
  })

  it('no lleva a un modo concreto', () => {
    // Filtrar por él desde aquí decidiría por el jugador algo que se decide viendo las partidas.
    pintar(acceso())
    expect(screen.getByRole('link').getAttribute('href')).not.toContain('mode=')
  })

  it('sale también con cero apostado', () => {
    // Es cuando más falta hace: el que no ha jugado nada es justo el que necesita saber por dónde.
    pintar(acceso({ wagered_usd: 0, missing_usd: 100 }))
    expect(screen.getByRole('link', { name: /Find a match/ })).toBeTruthy()
  })
})

// ── la página: qué se enseña y cuándo ────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  fetchAcceso: vi.fn(),
  fetchEv: vi.fn().mockResolvedValue({ rows: [], updated_at: 0 }),
  fetchEvLive: vi.fn().mockResolvedValue({ rows: [], updated_at: 0 }),
}))
vi.mock('../../../onchain/gachaClient', () => ({
  fetchTrackerAccess: mocks.fetchAcceso,
  fetchEvRows: mocks.fetchEv,
  fetchEvLive: mocks.fetchEvLive,
}))
vi.mock('@privy-io/react-auth', () => ({ useIdentityToken: () => ({ identityToken: 'tok' }) }))

const { MachineTrackerPage } = await import('./MachineTrackerPage')

/** Dos vueltas: una para el acceso y otra para lo que el panel pide DESPUÉS de montarse. Con una
 *  sola, el panel existe pero todavía no ha pintado sus datos. */
const FILA = {
  machine: 'pokemon_50', name: 'Elite Pokémon', pack_price: 50, buyback_pct: 0.85,
  realized_n_pulls: 3068, realized_window_hours: 48, window_complete: true, hours_covered: 48,
  gaps: [], realized_edge_pct: 6.65, realized_ci_lo_pct: 3.29, realized_ci_hi_pct: 10.14,
  realized_verdict: 'CONFIDENT +EV', pulls_to_conclude: null, tiers: [],
  model_ev: null, model_ratio: null, model_edge_pct: null,
}

const dejarResolver = async () => {
  for (let i = 0; i < 3; i++) await act(async () => { await Promise.resolve() })
}

beforeEach(() => { localStorage.clear(); vi.clearAllMocks() })

describe('MachineTrackerPage', () => {
  it('con acceso enseña el panel y NO el aviso', async () => {
    mocks.fetchAcceso.mockResolvedValue(acceso({ allowed: true, missing_usd: 0 }))
    mocks.fetchEv.mockResolvedValue({ rows: [FILA], updated_at: 0 })
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await dejarResolver()
    expect(screen.queryByText(/to go/)).toBeNull()
    // Y el panel está de verdad ahí: se comprueba por su interruptor de valoración, no solo por
    // la ausencia del aviso. Sin esto el test pasaría con la pantalla en blanco.
    expect(mocks.fetchEv).toHaveBeenCalled()
    expect(screen.getByText('if you sell back')).toBeTruthy()
  })

  it('sin acceso enseña el aviso y NO pide los datos del tracker', async () => {
    // Pedirlos daría igual para la seguridad —el endpoint es público— pero sería trabajo tirado:
    // el bootstrap de 48 máquinas para no enseñar nada.
    mocks.fetchAcceso.mockResolvedValue(acceso())
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await dejarResolver()
    expect(screen.getByText('$40 to go')).toBeTruthy()
    expect(mocks.fetchEv).not.toHaveBeenCalled()
  })

  it('mientras no se sabe, no se enseña ninguna de las dos cosas', async () => {
    // Enseñar el panel y quitarlo medio segundo después es peor que esperar; y enseñar el aviso a
    // quien sí tiene acceso es acusarle de algo que no es verdad.
    mocks.fetchAcceso.mockReturnValue(new Promise(() => {}))
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    expect(screen.queryByText(/to go/)).toBeNull()
    expect(mocks.fetchEv).not.toHaveBeenCalled()
  })

  it('el explicador está CON acceso y SIN él', async () => {
    // Quien todavía no puede entrar merece saber qué es lo que le estamos pidiendo que se gane.
    mocks.fetchAcceso.mockResolvedValue(acceso())
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await dejarResolver()
    expect(screen.getByRole('button', { name: /How to read a card/i })).toBeTruthy()
    cleanup()
    mocks.fetchAcceso.mockResolvedValue(acceso({ allowed: true, missing_usd: 0 }))
    mocks.fetchEv.mockResolvedValue({ rows: [FILA], updated_at: 0 })
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await dejarResolver()
    expect(screen.getByRole('button', { name: /How to read a card/i })).toBeTruthy()
  })

  it('si no se puede preguntar, la puerta se queda CERRADA', async () => {
    // Una puerta que se cae abierta ante un fallo de red no es una puerta.
    mocks.fetchAcceso.mockRejectedValue(new Error('sin red'))
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await dejarResolver()
    expect(screen.getByText('$100 to go')).toBeTruthy()
    expect(mocks.fetchEv).not.toHaveBeenCalled()
  })
})
