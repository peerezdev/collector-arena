import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

vi.mock('@privy-io/react-auth', () => ({ useIdentityToken: () => ({ identityToken: 'tok' }) }))
vi.mock('./balanceHold', () => ({ isBalanceHeld: () => false, useBalanceHeld: () => false }))
vi.mock('../onchain/config', () => ({ config: { backendUrl: 'http://backend' } }))

import { useUsdcBalance } from './useUsdcBalance'

/** Pone la pestaña a la vista o de fondo y lanza el evento, como haría el navegador. */
function verPestana(visible: boolean) {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => (visible ? 'visible' : 'hidden'),
  })
  document.dispatchEvent(new Event('visibilitychange'))
}

const respuesta = (usdc: number) =>
  Promise.resolve({ ok: true, json: () => Promise.resolve({ usdc }) } as unknown as Response)

let fetchMock: ReturnType<typeof vi.fn>

/** Cuántas veces se ha ido al backend. Cada una es una llamada al RPC. */
const consultas = () => fetchMock.mock.calls.length

describe('useUsdcBalance: solo consulta el saldo con la pestaña a la vista', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    fetchMock = vi.fn(() => respuesta(42))
    vi.stubGlobal('fetch', fetchMock)
    verPestana(true)
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('con la pestaña a la vista refresca cada 30 segundos', async () => {
    renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(consultas()).toBe(1)                               // la de entrada

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(consultas()).toBe(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(consultas()).toBe(3)
  })

  it('en segundo plano deja de consultar del todo', async () => {
    renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const antes = consultas()

    await act(async () => { verPestana(false) })
    // Cinco minutos escondida: ni una sola llamada más. Es el ahorro entero del cambio.
    await act(async () => { await vi.advanceTimersByTimeAsync(300_000) })
    expect(consultas()).toBe(antes)
  })

  it('al volver a la pestaña refresca en el acto, sin esperar los 30 segundos', async () => {
    renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    await act(async () => { verPestana(false) })
    await act(async () => { await vi.advanceTimersByTimeAsync(300_000) })
    const escondida = consultas()

    // Sin esto se vería el número congelado de hace horas, que es peor que no haber parado.
    await act(async () => { verPestana(true) })
    expect(consultas()).toBe(escondida + 1)
  })

  it('al volver reanuda el ciclo, y no lo duplica', async () => {
    renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    await act(async () => { verPestana(false) })
    await act(async () => { verPestana(true) })
    const tras_volver = consultas()

    // Un solo intervalo vivo: 30 s son exactamente una consulta más, no dos.
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(consultas()).toBe(tras_volver + 1)
  })

  it('al desmontar no deja nada corriendo, ni el intervalo ni el oyente', async () => {
    const { unmount } = renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    unmount()
    const alDesmontar = consultas()

    await act(async () => { await vi.advanceTimersByTimeAsync(300_000) })
    await act(async () => { verPestana(false); verPestana(true) })
    expect(consultas()).toBe(alDesmontar)
  })

  it('devuelve el saldo que da el backend', async () => {
    const { result } = renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.usdc).toBe(42)
    expect(result.current.loading).toBe(false)
  })
})
