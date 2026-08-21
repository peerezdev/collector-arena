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

describe('PanelEv · un 403 en cualquiera de los dos carriles lleva a la puerta', () => {
  const accesoOk = { allowed: true, wagered_usd: 500, required_usd: 100, missing_usd: 0, window_days: 7 }
  const accesoCerrado = { allowed: false, wagered_usd: 0, required_usd: 100, missing_usd: 100, window_days: 7 }
  const error403 = () => Object.assign(new Error('tracker_locked'), { status: 403 })

  /** El montaje SIEMPRE tiene que dar acceso (si no, no hay `PanelEv` con el que probar nada); solo
   *  una llamada POSTERIOR a `fetchTrackerAccess` — la que dispara `onSinAcceso` — puede cerrar la
   *  puerta. Contar llamadas en vez de encadenar `mockResolvedValueOnce` evita depender de CUÁNTAS
   *  veces se llama antes de la que importa, que es justo lo que el mutante del test de control
   *  pondría en duda. */
  const accesoSegúnLlamada = () => {
    let llamadas = 0
    mocks.fetchAcceso.mockImplementation(() => {
      llamadas += 1
      return Promise.resolve({ ...(llamadas === 1 ? accesoOk : accesoCerrado) })
    })
  }

  afterEach(() => {
    // Este describe sustituye la respuesta por defecto de `fetchAcceso` por una que cuenta
    // llamadas; se restaura al valor fijo del resto del fichero para no colarse en otro test.
    mocks.fetchAcceso.mockReset()
    mocks.fetchAcceso.mockResolvedValue({ allowed: true, wagered_usd: 500, required_usd: 100, missing_usd: 0, window_days: 7 })
  })

  it('el carril lento devuelve 403 → aparece la puerta, no el aviso de fallo', async () => {
    accesoSegúnLlamada()
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(screen.getByText('Elite Pokémon')).toBeTruthy()

    mocks.fetchEv.mockRejectedValueOnce(error403())
    await avanzar(60_000)
    // Segundo tirón de reloj: el 403 dispara `onSinAcceso`, que hace un `fetchTrackerAccess`
    // nuevo y ENCADENADO (no atado a ningún temporizador); con temporizadores falsos, `avanzar`
    // solo garantiza vaciar lo que cuelga del tic que acaba de correr, así que este segundo
    // `avanzar(0)` es el que vacía esa segunda promesa y deja pintada la puerta.
    await avanzar(0)

    expect(screen.getByText(/to go/i)).toBeTruthy()
    expect(screen.queryByText(/Couldn't load the tracker/i)).toBeNull()
  })

  it('el carril rápido devuelve 403 → aparece la puerta, no el aviso de fallo', async () => {
    accesoSegúnLlamada()
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    expect(screen.getByText('Elite Pokémon')).toBeTruthy()

    mocks.fetchEvLive.mockRejectedValueOnce(error403())
    await avanzar(10_000)
    await avanzar(0) // ver el comentario del test del carril lento

    expect(screen.getByText(/to go/i)).toBeTruthy()
    expect(screen.queryByText(/Couldn't load the tracker/i)).toBeNull()
  })

  it('un fallo que NO es 403 en la primera carga enseña el aviso, no la puerta', async () => {
    // Control: sin este test, los dos de arriba podrían pasar con un `catch` que mandara
    // CUALQUIER error a la puerta, no solo el 403. Aquí un error de red de verdad en la
    // PRIMERA carga tiene que dar el aviso de avería, y la puerta debe seguir cerrada... es
    // decir, sin abrirse ni cerrarse: el acceso ya concedido no debe tocarse.
    accesoSegúnLlamada()
    mocks.fetchEv.mockRejectedValueOnce(new Error('sin red'))
    render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)
    await avanzar(0)
    await avanzar(0)

    expect(screen.getByText(/Couldn't load the tracker/i)).toBeTruthy()
    expect(screen.queryByText(/to go/i)).toBeNull()
  })
})
