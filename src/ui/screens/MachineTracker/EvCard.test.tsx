import { describe, it, expect } from 'vitest'
import { render as renderRaw, screen, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// La tarjeta enlaza a Winners con su máquina filtrada, así que necesita router para montarse.
const render = (ui: React.ReactElement) => renderRaw(<MemoryRouter>{ui}</MemoryRouter>)

import { EvCard } from './EvCard'
import type { EvRow } from '../../../onchain/gachaClient'
import { ACENTO } from './evAcento'

const fila = (over: Partial<EvRow> = {}): EvRow => ({
  machine: 'pokemon_50', name: 'Elite Pokémon Gacha Pack', pack_price: 50, buyback_pct: 0.85,
  realized_n_pulls: 16157, realized_window_hours: 48, window_complete: true, hours_covered: 48,
  gaps: [], realized_edge_pct: -6.21, realized_ci_lo_pct: -8.27, realized_ci_hi_pct: -4.24,
  realized_verdict: 'CONFIDENT -EV', pulls_to_conclude: null,
  model_ev: null, model_ratio: null, model_edge_pct: null,
  tiers: [
    { tier: 'Common', current: 0, average: 0.3, seen: 257, sample: 327, days_since: 0, cold: false },
    { tier: 'Uncommon', current: 2, average: 5.4, seen: 51, sample: 327, days_since: 0.1, cold: false },
    { tier: 'Rare', current: 61, average: 19.4, seen: 16, sample: 327, days_since: 2.6, cold: true },
    { tier: 'Epic', current: null, average: null, seen: 0, sample: 327, days_since: null, cold: false },
  ],
  ...over,
})

/** Ahora sí cuenta agujas: la única `line` del dial es la aguja. La marca de escala del 1.00 se
 *  quitó —su etiqueta ya lo dice— justo porque era una rayita idéntica a la del modelo. */
const agujas = (c: HTMLElement) => c.querySelectorAll('svg line').length
const marcasModelo = (c: HTMLElement) => c.querySelectorAll('svg circle').length

describe('EvCard', () => {
  it('enseña el ratio medido, no el edge, como número principal', () => {
    // 1 + (−6.21/100). Es lo que marca la aguja, y el número grande tiene que coincidir con ella.
    render(<EvCard fila={fila()} />)
    expect(screen.getByText('0.938')).toBeTruthy()
  })

  it('lleva el precio y el buyback en la cabecera', () => {
    render(<EvCard fila={fila()} />)
    expect(screen.getByText(/\$50 · bb 85%/)).toBeTruthy()
  })

  it('un veredicto confirmado se puede vestir de conclusión', () => {
    render(<EvCard fila={fila()} />)
    expect(screen.getByText('CONFIRMED −EV')).toBeTruthy()
    expect(screen.getByText(/95% CI −?-?8.27/)).toBeTruthy()
  })

  it('con la ventana a medias el número NO se pinta de rojo', () => {
    // La regla del diseño: un rojo fuerte sobre seis horas de datos afirma algo que los datos no
    // dicen. El número se enseña igual, en gris, y la etiqueta explica por qué.
    const { container } = render(<EvCard fila={fila({
      realized_verdict: 'BUILDING', window_complete: false, hours_covered: 6,
    })} />)
    expect(screen.getByText(/BUILDING · 6h \/ 48h/)).toBeTruthy()
    expect(screen.getByText(/until the window is full/i)).toBeTruthy()
    expect(container.innerHTML).not.toContain(ACENTO.malo)   // la tinta de "malo confirmado"
  })

  it('con un hueco dentro de la ventana tampoco', () => {
    const { container } = render(<EvCard fila={fila({
      realized_verdict: 'GAP IN WINDOW', gaps: [['a', 'b']],
    })} />)
    expect(screen.getByText('GAP IN WINDOW')).toBeTruthy()
    expect(container.innerHTML).not.toContain(ACENTO.malo)
  })

  it('sin medición no dibuja aguja', () => {
    // Una aguja en el centro se leería como "paga justo", que es afirmar algo sin datos.
    const { container } = render(<EvCard fila={fila({
      realized_edge_pct: null, realized_ci_lo_pct: null, realized_ci_hi_pct: null,
      realized_verdict: 'NOT ENOUGH DATA', realized_n_pulls: 12,
    })} />)
    // getAllByText: la tabla de tiers también usa "—" para una media que no se ha medido.
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
    expect(screen.getByText('NOT ENOUGH DATA')).toBeTruthy()
    expect(screen.getByText(/Only 12 pulls/)).toBeTruthy()
    expect(agujas(container)).toBe(0)     // sin aguja: no hay nada medido que apuntar
  })

  it('con medición sí dibuja aguja', () => {
    const { container } = render(<EvCard fila={fila()} />)
    expect(agujas(container)).toBe(1)
  })

  it('una máquina que paga de más se pinta en verde', () => {
    const { container } = render(<EvCard fila={fila({
      realized_edge_pct: 4.2, realized_ci_lo_pct: 2.1, realized_ci_hi_pct: 6.4,
      realized_verdict: 'CONFIDENT +EV',
    })} />)
    expect(screen.getByText('CONFIRMED +EV')).toBeTruthy()
    expect(screen.getByText('1.042')).toBeTruthy()
    expect(container.innerHTML).toContain(ACENTO.bueno)
  })

  it('enseña la racha de cada rareza y su media', () => {
    // Lo que pedía el diseño: no basta con "61", hace falta saber que lo normal son 19.
    render(<EvCard fila={fila()} />)
    expect(screen.getByText('Rare')).toBeTruthy()
    expect(screen.getByText('61')).toBeTruthy()
    expect(screen.getByText('19.4')).toBeTruthy()
  })

  it('el acento viste toda la tarjeta, no solo el número', () => {
    // Un solo color por tarjeta es lo que hace que la rejilla se lea de un vistazo: el punto del
    // título, el borde, el relleno del arco y la pastilla del veredicto van todos a la vez.
    const { container } = render(<EvCard fila={fila()} />)   // CONFIDENT -EV
    const html = container.innerHTML
    // Aparece muchas veces porque lo llevan el punto, el borde, el arco, el número y la pastilla.
    expect(html.split(ACENTO.malo).length - 1).toBeGreaterThan(3)
  })

  it('la pastilla del edge SOLO sale con veredicto confirmado', () => {
    // Sobre un intervalo que cruza el cero, un "-6.2%" en pastilla se lee como una conclusión.
    render(<EvCard fila={fila()} />)
    expect(screen.getByText('-6.2%')).toBeTruthy()
    cleanup()
    render(<EvCard fila={fila({ realized_verdict: 'unclear (CI crosses zero)' })} />)
    expect(screen.queryByText('-6.2%')).toBeNull()
  })

  it('cada fila se tinta con el color de SU rareza', () => {
    // Se comprueba que sean distintas y no un hex concreto: jsdom convierte el hex de 8 dígitos a
    // `rgba(...)`, así que buscar el literal pasaría o fallaría por el entorno y no por el código.
    const { container } = render(<EvCard fila={fila()} />)
    const fondos = [...container.querySelectorAll('tbody tr')]
      .map((tr) => tr.getAttribute('style') ?? '')
    expect(fondos).toHaveLength(4)
    expect(fondos.every((f) => f.includes('linear-gradient'))).toBe(true)
    // Cuatro rarezas, cuatro tintes distintos.
    expect(new Set(fondos).size).toBe(4)
  })

  it('la columna AGO ya no está', () => {
    // Se quitó a petición: la tabla tenía siete columnas y el tiempo de la racha era la menos
    // usada. El dato sigue llegando del backend, simplemente no se pinta.
    render(<EvCard fila={fila()} />)
    expect(screen.queryByText('AGO')).toBeNull()
    expect(screen.queryByText('3d')).toBeNull()
  })

  it('una rareza que no salió dice "327+" y no "327"', () => {
    // La racha es MAYOR que la muestra; escribir el tamaño daría por medido lo que no se ha medido.
    render(<EvCard fila={fila()} />)
    expect(screen.getByText('327+')).toBeTruthy()
  })

  it('las rachas se enseñan aunque no haya veredicto', () => {
    // Se miden sobre las tiradas observadas, así que son útiles desde la primera hora.
    render(<EvCard fila={fila({ realized_verdict: 'BUILDING', window_complete: false, hours_covered: 3 })} />)
    expect(screen.getByText('Rare')).toBeTruthy()
    expect(screen.getByText('61')).toBeTruthy()
  })

  // ── el modelo: lo que la máquina DEBERÍA pagar ──────────────────────────────

  const conModelo = (over = {}) => fila({
    model_ev: 26.998, model_ratio: 1.08, model_edge_pct: 8.0,
    tiers: [
      { tier: 'Common', current: 0, average: 0.3, seen: 257, sample: 327, days_since: 0,
        cold: false, probability: 0.75, n_cards: 12, value: 19.75, gross: 14.81 },
      { tier: 'Epic', current: null, average: null, seen: 0, sample: 327, days_since: null,
        cold: false, probability: 0.01, n_cards: 211, value: 160.22, gross: 1.6 },
    ],
    ...over,
  })

  it('enseña lo esperado junto a lo medido, que es de lo que va la tarjeta', () => {
    // "Debería pagar 1.080 y está pagando 0.938" dice mucho más que cualquiera de los dos solo.
    render(<EvCard fila={conModelo()} />)
    expect(screen.getByText('0.938')).toBeTruthy()          // lo medido
    expect(screen.getByText(/model 1.080/)).toBeTruthy()    // lo esperado
  })

  it('el modelo NO se dibuja como una aguja', () => {
    // Era el fallo: se dibujaba como una rayita igual a la de la escala del 1.00, así que con un
    // modelo cerca de 1.00 las dos se solapaban y se leían como dos agujas.
    const { container } = render(<EvCard fila={conModelo()} />)
    // Una sola línea, la aguja de lo medido.
    expect(agujas(container)).toBe(1)
    // El modelo es un PUNTO sobre el arco: dos círculos, el halo y el punto. Más el eje de la
    // aguja, que también es un círculo.
    expect(marcasModelo(container)).toBe(3)
  })

  it('los dos números van etiquetados, para saber cuál es cuál', () => {
    // Sin las palabras son dos cifras parecidas y nada dice qué es lo medido y qué lo esperado.
    render(<EvCard fila={conModelo()} />)
    expect(screen.getByText('MEASURED')).toBeTruthy()
    expect(screen.getByText(/model 1.080/)).toBeTruthy()
  })

  it('sin modelo no hay punto: solo el eje de la aguja', () => {
    const { container } = render(<EvCard fila={fila()} />)
    expect(marcasModelo(container)).toBe(1)
  })

  it('desglosa cada rareza en probabilidad, valor y lo que aporta', () => {
    render(<EvCard fila={conModelo()} />)
    expect(screen.getByText('75%')).toBeTruthy()        // P
    expect(screen.getByText('$19.75')).toBeTruthy()     // VALUE
    expect(screen.getByText('$14.81')).toBeTruthy()     // GROSS
  })

  it('una probabilidad pequeña no se redondea a 0%', () => {
    // Un Epic al 1% redondeado a "0%" diría que no sale nunca.
    render(<EvCard fila={conModelo()} />)
    expect(screen.getByText('1%')).toBeTruthy()
  })

  it('sin el pool barrido pone guiones, nunca ceros', () => {
    // Un 0% diría que esa rareza no sale nunca, y un $0 que no vale nada. Las dos cosas son
    // afirmaciones, y lo cierto es que todavía no hemos mirado sus cartas.
    const { container } = render(<EvCard fila={fila()} />)     // el fixture base va sin modelo
    expect(screen.queryByText(/model /)).toBeNull()
    expect(agujas(container)).toBe(1)                          // la aguja, sin punto del modelo
    expect(marcasModelo(container)).toBe(1)                    // solo el eje de la aguja
    // Por texto exacto de celda y no buscando en el HTML: "0%" aparece dentro del width:100% de
    // la tabla, así que un `toContain` daba por bueno el test sin mirar una sola celda.
    expect(screen.queryByText('0%')).toBeNull()
    expect(screen.queryByText('$0')).toBeNull()
  })

  it('sin concluir ofrece cuánta muestra faltaría', () => {
    render(<EvCard fila={fila({
      realized_verdict: 'unclear (CI crosses zero)', realized_n_pulls: 543,
      realized_edge_pct: -11.14, realized_ci_lo_pct: -21.63, realized_ci_hi_pct: 0.93,
      pulls_to_conclude: 1400,
    })} />)
    expect(screen.getByText(/UNCLEAR/)).toBeTruthy()
    expect(screen.getByText(/1,400 pulls would settle it/)).toBeTruthy()
  })
})
