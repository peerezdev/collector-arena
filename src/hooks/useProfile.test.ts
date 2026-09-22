import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

vi.mock('../wallet/embedded', () => ({ useEmbeddedSolanaAddress: () => null }))

import { useProfile } from './useProfile'

afterEach(() => { vi.unstubAllGlobals() })

describe('useProfile', () => {
  it('drops the previous profile when the address changes', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) =>
      url.endsWith('/users/A')
        ? Promise.resolve({ ok: true, json: async () => ({ alias: 'a', rank: 'gold', tags: ['TEAM'] }) })
        : new Promise(() => {})))
    const { result, rerender } = renderHook(({ addr }) => useProfile(addr), { initialProps: { addr: 'A' } })
    await waitFor(() => expect(result.current.rank).toBe('gold'))
    rerender({ addr: 'B' })
    expect(result.current.rank).toBeNull()
    expect(result.current.tags).toEqual([])
    expect(result.current.username).toBeNull()
  })

  it('keeps the profile on screen while a refresh of the same address loads', async () => {
    let calls = 0
    vi.stubGlobal('fetch', vi.fn(() => (++calls === 1
      ? Promise.resolve({ ok: true, json: async () => ({ alias: 'a', rank: 'gold', tags: [] }) })
      : new Promise(() => {}))))
    const { result } = renderHook(() => useProfile('A'))
    await waitFor(() => expect(result.current.rank).toBe('gold'))
    result.current.refresh()
    await waitFor(() => expect(calls).toBe(2))
    expect(result.current.rank).toBe('gold')
  })
})
