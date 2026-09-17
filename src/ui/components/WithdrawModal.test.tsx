import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// El modal tira de media docena de hooks; se sustituyen para probar SOLO lo que hace con la
// respuesta del servidor, que es donde estaba el problema.
vi.mock('@privy-io/react-auth', () => ({ useIdentityToken: () => ({ identityToken: 'tok' }) }))
vi.mock('../useReducedMotion', () => ({ useReducedMotion: () => true }))
vi.mock('../../wallet/useUsdcBalance', () => ({
  useUsdcBalance: () => ({ usdc: 500, loading: false }),
  useCardsBalance: () => ({ cards: 42, loading: false }),
}))
vi.mock('../../wallet/useReservedBalance', () => ({
  useReservedBalance: () => ({ reserved: 0, lockedRoyale: 0 }),
  availableUsd: () => 500,
}))
vi.mock('../../hooks/useProfile', () => ({ useProfile: () => ({ profile: null }) }))
// La puerta de delegación ejecuta la acción directamente: aquí no se prueba ese flujo.
vi.mock('./useDelegationGate', () => ({
  useDelegationGate: () => ({ open: false, requireDelegation: (fn: () => void) => fn() }),
}))
vi.mock('./DelegationGate', () => ({ DelegationGate: () => null }))
vi.mock('../toastBus', () => ({ showToast: vi.fn() }))

import { WithdrawModal } from './WithdrawModal'

const DESTINO = '4Nd1mBQtrMJVYVfKf2PJy9NZUZdTAsp7D4xWLs4gKgBc'

/** Rellena el formulario y pulsa, con el servidor devolviendo `status`. */
async function pedirRetiro(status: number) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300, status, json: async () => ({}),
  }))
  render(<WithdrawModal open onClose={() => {}} />)
  fireEvent.change(screen.getByPlaceholderText(/wallet address|address/i), { target: { value: DESTINO } })
  const importe = screen.getAllByRole('textbox').find((e) => e !== screen.getByPlaceholderText(/wallet address|address/i))
  fireEvent.change(importe ?? screen.getByPlaceholderText('0.00'), { target: { value: '10' } })
  fireEvent.click(screen.getByRole('button', { name: /withdraw/i }))
}

describe('WithdrawModal · qué se le dice al usuario cuando falla', () => {
  beforeEach(() => vi.unstubAllGlobals())

  it('con una partida en curso se explica el motivo Y cuándo se arregla', async () => {
    // Era el caso peor: 409 caía en "Withdrawal failed. Please try again." — no explicaba nada y
    // encima aconsejaba justo lo que no funciona, porque reintentar no termina la partida.
    await pedirRetiro(409)
    const aviso = await screen.findByText(/in a battle right now/i)
    expect(aviso.textContent).toMatch(/unlock when it ends/i)
    expect(screen.queryByText(/please try again/i)).toBeNull()
  })

  it('por debajo del mínimo no dice "reinténtalo"', async () => {
    await pedirRetiro(422)
    expect(await screen.findByText(/below the minimum/i)).toBeTruthy()
    expect(screen.queryByText(/please try again/i)).toBeNull()
  })

  it('con demasiados retiros seguidos sí dice que espere', async () => {
    await pedirRetiro(429)
    expect(await screen.findByText(/too many withdrawals/i)).toBeTruthy()
  })

  it('sin saldo disponible se dice tal cual', async () => {
    await pedirRetiro(402)
    expect(await screen.findByText(/insufficient available balance/i)).toBeTruthy()
  })

  it('un fallo que no sabemos explicar sigue cayendo en el mensaje genérico', async () => {
    await pedirRetiro(500)
    expect(await screen.findByText(/please try again/i)).toBeTruthy()
  })

  it('un retiro correcto no deja ningún aviso de error', async () => {
    await pedirRetiro(200)
    await waitFor(() => expect(screen.queryByText(/failed|below the minimum|in a battle/i)).toBeNull())
  })
})

describe('WithdrawModal · selector USDC / CARDS', () => {
  beforeEach(() => vi.unstubAllGlobals())

  it('por defecto muestra USDC: título, saldo en dólares y token "usdc" en el envío', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) }))
    render(<WithdrawModal open onClose={() => {}} />)

    expect(container_text_has(screen, 'Withdraw USDC')).toBe(true)
    expect(container_text_has(screen, '$500')).toBe(true)

    fireEvent.change(screen.getByPlaceholderText(/wallet address|address/i), { target: { value: DESTINO } })
    fireEvent.change(screen.getByPlaceholderText('0.00'), { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: /^withdraw$/i }))

    await waitFor(() => expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1))
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    const body = JSON.parse(call[1].body)
    expect(body).toEqual({ address: DESTINO, amount: 10, token: 'usdc' })
  })

  it('al elegir CARDS cambia el título, el saldo mostrado y el token enviado al backend', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) }))
    render(<WithdrawModal open onClose={() => {}} />)

    fireEvent.click(screen.getByRole('button', { name: /select cards/i }))

    expect(container_text_has(screen, 'Withdraw CARDS')).toBe(true)
    // El saldo de CARDS (42, del mock) se pinta SIN signo de dólar.
    expect(container_text_has(screen, '42')).toBe(true)
    expect(screen.queryByText('$500')).toBeNull()

    fireEvent.change(screen.getByPlaceholderText(/wallet address|address/i), { target: { value: DESTINO } })
    fireEvent.change(screen.getByPlaceholderText('0.00'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: /^withdraw$/i }))

    await waitFor(() => expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1))
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    const body = JSON.parse(call[1].body)
    expect(body).toEqual({ address: DESTINO, amount: 5, token: 'cards' })
  })

  it('el botón MAX en CARDS usa el saldo de CARDS, no el de USDC', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) }))
    render(<WithdrawModal open onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /select cards/i }))
    fireEvent.click(screen.getByRole('button', { name: /max/i }))
    const amountInput = screen.getByPlaceholderText('0.00') as HTMLInputElement
    expect(amountInput.value).toBe('42')
  })

  it('volver a USDC restaura el título y el saldo en dólares', async () => {
    render(<WithdrawModal open onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /select cards/i }))
    fireEvent.click(screen.getByRole('button', { name: /select usdc/i }))
    expect(container_text_has(screen, 'Withdraw USDC')).toBe(true)
    expect(container_text_has(screen, '$500')).toBe(true)
  })
})

// Helper local: el repo usa container.textContent en vez de toBeInTheDocument (no hay
// jest-dom en este vitest), así que se comprueba el texto sobre el documento entero.
function container_text_has(_scr: typeof screen, text: string): boolean {
  return document.body.textContent?.includes(text) === true
}

describe('WithdrawModal en pantallas bajas', () => {
  // jsdom no hace layout, así que no puede reproducir el recorte. Lo que sí se puede fijar es la
  // propiedad que lo causaba: centrar un `fixed` con `translate(-50%,-50%)` manda la mitad de
  // arriba por encima de top:0 en cuanto el contenido no cabe, y ahí ya no hay forma de llegar a
  // ella. Pasó de verdad en un móvil: al añadir el selector de token, el selector y el título
  // quedaron fuera de la pantalla.
  const panel = () => screen.getByLabelText('Close').closest('div[style]')!.parentElement as HTMLElement

  it('el panel no se centra con el truco del transform', () => {
    render(<WithdrawModal open onClose={() => {}} />)
    expect(panel().style.transform).toBe('')
  })

  it('el contenedor deja scrollear cuando el modal no cabe', () => {
    render(<WithdrawModal open onClose={() => {}} />)
    const overlay = panel().parentElement as HTMLElement
    expect(overlay.style.position).toBe('fixed')
    expect(overlay.style.overflowY).toBe('auto')
    expect(panel().style.margin).toBe('auto')   // centra si cabe, no recorta si no
  })

  it('se monta en document.body y no en el árbol donde lo pintan', () => {
    // Es LA causa del bug, y no una preferencia: AuthButtons monta este modal dentro de la barra
    // superior, que lleva backdrop-filter. Un ancestro con backdrop-filter se vuelve el marco de
    // referencia de todo `position:fixed` que tenga dentro, así que sin el portal el overlay se
    // posiciona respecto a la barra y no respecto a la pantalla.
    const { container } = render(<WithdrawModal open onClose={() => {}} />)
    const overlay = panel().parentElement as HTMLElement
    expect(container.contains(overlay)).toBe(false)
    expect(document.body.contains(overlay)).toBe(true)
  })

  it('el selector de token sigue estando al abrir', () => {
    render(<WithdrawModal open onClose={() => {}} />)
    expect(screen.getByLabelText('Select USDC')).toBeTruthy()
    expect(screen.getByLabelText('Select CARDS')).toBeTruthy()
  })
})
