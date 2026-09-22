import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useBadges, __resetBadgesForTests } from './useBadges'

const ok = (body: unknown) => ({ ok: true, json: async () => body })

beforeEach(() => __resetBadgesForTests())
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

describe('useBadges', () => {
  it('resolves rank and tags', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok({ A: { rank: 'gold', tags: ['TEAM'] } })))
    const { result } = renderHook(() => useBadges(['A']))
    await waitFor(() => expect(result.current.A).toEqual({ rank: 'gold', tags: ['TEAM'] }))
  })

  it('batches the wallets of many callers into one request', async () => {
    const f = vi.fn().mockResolvedValue(ok({ A: { rank: null, tags: [] }, B: { rank: null, tags: [] } }))
    vi.stubGlobal('fetch', f)
    const a = renderHook(() => useBadges(['A']))
    const b = renderHook(() => useBadges(['B']))
    await waitFor(() => expect(a.result.current.A).toBeDefined())
    await waitFor(() => expect(b.result.current.B).toBeDefined())
    expect(f).toHaveBeenCalledTimes(1)
    expect(String(f.mock.calls[0][0])).toContain('wallets=A%2CB')
  })

  it('splits more than 100 wallets into several requests', async () => {
    const f = vi.fn(async (url: string) => {
      const ws = decodeURIComponent(url.split('wallets=')[1]).split(',')
      return ok(Object.fromEntries(ws.map((w) => [w, { rank: null, tags: [] }])))
    })
    vi.stubGlobal('fetch', f)
    const wallets = Array.from({ length: 150 }, (_, i) => `W${i}`)
    const { result } = renderHook(() => useBadges(wallets))
    await waitFor(() => expect(Object.keys(result.current)).toHaveLength(150))
    expect(f).toHaveBeenCalledTimes(2)
  })

  it('does not ask again for a cached wallet', async () => {
    const f = vi.fn().mockResolvedValue(ok({ A: { rank: 'bronze', tags: [] } }))
    vi.stubGlobal('fetch', f)
    const first = renderHook(() => useBadges(['A']))
    await waitFor(() => expect(first.result.current.A).toBeDefined())
    const second = renderHook(() => useBadges(['A']))
    expect(second.result.current.A).toEqual({ rank: 'bronze', tags: [] })
    expect(f).toHaveBeenCalledTimes(1)
  })

  it('asks again once the cache entry is 5 minutes old', async () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    const f = vi.fn().mockResolvedValue(ok({ A: { rank: 'bronze', tags: [] } }))
    vi.stubGlobal('fetch', f)
    const first = renderHook(() => useBadges(['A']))
    await waitFor(() => expect(first.result.current.A).toBeDefined())
    now.mockReturnValue(1_000_000 + 5 * 60_000 + 1)
    renderHook(() => useBadges(['A']))
    await waitFor(() => expect(f).toHaveBeenCalledTimes(2))
  })

  it('shows nothing and caches nothing when the request fails', async () => {
    const f = vi.fn().mockRejectedValue(new Error('network'))
    vi.stubGlobal('fetch', f)
    const { result } = renderHook(() => useBadges(['A']))
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1))
    expect(result.current.A).toBeUndefined()
    f.mockResolvedValue(ok({ A: { rank: 'gold', tags: [] } }))
    const again = renderHook(() => useBadges(['A']))
    await waitFor(() => expect(again.result.current.A).toEqual({ rank: 'gold', tags: [] }))
  })

  it('treats an unknown rank id as no rank', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok({ A: { rank: 'ruby', tags: ['TEAM'] } })))
    const { result } = renderHook(() => useBadges(['A']))
    await waitFor(() => expect(result.current.A).toEqual({ rank: null, tags: ['TEAM'] }))
  })
})
