import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { buyTrackerPass, fetchEvRows, fetchEvLive } from './gachaClient'

const fetchMock = vi.fn()
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset() })
afterEach(() => vi.unstubAllGlobals())

const ok = (body: unknown) =>
  fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => body })

describe('el cliente del pase del tracker', () => {
  it('las filas del tracker viajan CON token, que antes no lo llevaban', async () => {
    ok({ rows: [], updated_at: 0 })
    await fetchEvRows(undefined, 'tok')
    const [, init] = fetchMock.mock.calls[0]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok')
  })

  it('el carril rápido también', async () => {
    ok({ rows: [], updated_at: 0 })
    await fetchEvLive('tok')
    const [, init] = fetchMock.mock.calls[0]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok')
  })

  it('comprar manda la duración y el token', async () => {
    ok({ pass_until: 123, days: 30, price_usdc: 30 })
    const r = await buyTrackerPass(30, 'tok')
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/gacha/tracker-pass')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ days: 30 })
    expect(r.pass_until).toBe(123)
  })

  it('un 403 se distingue de un fallo, para poder enseñar la puerta', async () => {
    // Si se confundiera con un error de red, la pantalla diría "se ha roto algo" cuando lo que
    // pasa es que se acabó el pase.
    fetchMock.mockResolvedValue({ ok: false, status: 403, json: async () => ({ detail: 'tracker_locked' }) })
    await expect(fetchEvRows(undefined, 'tok')).rejects.toMatchObject({ status: 403 })
  })
})
