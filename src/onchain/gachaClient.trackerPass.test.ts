import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { buyTrackerPass, fetchEvRows, fetchEvLive } from './gachaClient'

const fetchMock = vi.fn()
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset() })
afterEach(() => vi.unstubAllGlobals())

const ok = (body: unknown) =>
  fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => body })

describe('the tracker pass client', () => {
  it('tracker rows travel WITH a token, which they did not before', async () => {
    ok({ rows: [], updated_at: 0 })
    await fetchEvRows(undefined, 'tok')
    const [, init] = fetchMock.mock.calls[0]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok')
  })

  it('the fast lane too', async () => {
    ok({ rows: [], updated_at: 0 })
    await fetchEvLive('tok')
    const [, init] = fetchMock.mock.calls[0]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok')
  })

  it('buying sends the duration and the token', async () => {
    ok({ pass_until: 123, days: 30, price_usdc: 30 })
    const r = await buyTrackerPass(30, 'tok')
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/gacha/tracker-pass')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ days: 30 })
    expect(r.pass_until).toBe(123)
  })

  it('a 403 is told apart from a failure, so the gate can be shown', async () => {
    // If this were confused with a network error, the screen would say "something broke" when
    // what actually happened is that the pass ran out.
    fetchMock.mockResolvedValue({ ok: false, status: 403, json: async () => ({ detail: 'tracker_locked' }) })
    await expect(fetchEvRows(undefined, 'tok')).rejects.toMatchObject({ status: 403 })
  })
})
