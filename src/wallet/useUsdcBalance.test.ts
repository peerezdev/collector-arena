import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

let token: string | null = 'tok'
vi.mock('@privy-io/react-auth', () => ({ useIdentityToken: () => ({ identityToken: token }) }))
vi.mock('./balanceHold', () => ({ isBalanceHeld: () => false, useBalanceHeld: () => false }))
vi.mock('../onchain/config', () => ({ config: { backendUrl: 'http://backend' } }))

import { useUsdcBalance, useCardsBalance } from './useUsdcBalance'

/** Puts the tab on screen or in the background and fires the event, as the browser would. */
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

/** How many times it went to the backend. Each one is an RPC call. */
const consultas = () => fetchMock.mock.calls.length

describe('useUsdcBalance: only queries the balance with the tab in view', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    token = 'tok'
    fetchMock = vi.fn(() => respuesta(42))
    vi.stubGlobal('fetch', fetchMock)
    verPestana(true)
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('with the tab in view, it refreshes every 30 seconds', async () => {
    renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(consultas()).toBe(1)                               // la de entrada

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(consultas()).toBe(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(consultas()).toBe(3)
  })

  it('in the background, it stops querying altogether', async () => {
    renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const antes = consultas()

    await act(async () => { verPestana(false) })
    // Five minutes hidden: not one more call. That is the entire saving of this change.
    await act(async () => { await vi.advanceTimersByTimeAsync(300_000) })
    expect(consultas()).toBe(antes)
  })

  it('coming back to the tab refreshes right away, without waiting the 30 seconds', async () => {
    renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    await act(async () => { verPestana(false) })
    await act(async () => { await vi.advanceTimersByTimeAsync(300_000) })
    const escondida = consultas()

    // Without this you would see the number frozen hours ago, worse than never having stopped.
    await act(async () => { verPestana(true) })
    expect(consultas()).toBe(escondida + 1)
  })

  it('coming back resumes the cycle, and does not duplicate it', async () => {
    renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    await act(async () => { verPestana(false) })
    await act(async () => { verPestana(true) })
    const tras_volver = consultas()

    // A single live interval: 30 s is exactly one more query, not two.
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(consultas()).toBe(tras_volver + 1)
  })

  it('unmounting leaves nothing running, neither the interval nor the listener', async () => {
    const { unmount } = renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    unmount()
    const alDesmontar = consultas()

    await act(async () => { await vi.advanceTimersByTimeAsync(300_000) })
    await act(async () => { verPestana(false); verPestana(true) })
    expect(consultas()).toBe(alDesmontar)
  })

  it('returns the balance the backend gives', async () => {
    const { result } = renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.usdc).toBe(42)
    expect(result.current.loading).toBe(false)
  })
})

describe('useUsdcBalance / useCardsBalance', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    token = 'tok'
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('useUsdcBalance lee /users/me/usdc y el campo "usdc"', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ usdc: 12.5 }) })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useUsdcBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    expect(result.current.usdc).toBe(12.5)
    expect(fetchMock.mock.calls[0][0]).toContain('/users/me/usdc')
  })

  it('useCardsBalance lee /users/me/cards y el campo "cards", no /usdc', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ cards: 7 }) })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useCardsBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    expect(result.current.cards).toBe(7)
    expect(fetchMock.mock.calls[0][0]).toContain('/users/me/cards')
    expect(fetchMock.mock.calls[0][0]).not.toContain('/users/me/usdc')
  })

  it('useCardsBalance es null sin sesión, y no llama a fetch', async () => {
    token = null
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useCardsBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    expect(result.current.cards).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('useCardsBalance es null cuando el backend responde 503 (mint sin configurar)', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useCardsBalance())
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    expect(result.current.cards).toBeNull()
  })
})
