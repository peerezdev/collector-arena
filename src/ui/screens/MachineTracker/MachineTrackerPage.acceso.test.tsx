import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { TrackerAccess } from '../../../onchain/gachaClient'

const mocks = vi.hoisted(() => ({
  fetchAcceso: vi.fn(),
  fetchEv: vi.fn(),
  fetchEvLive: vi.fn(),
  identityToken: vi.fn(),
}))
vi.mock('../../../onchain/gachaClient', () => ({
  fetchTrackerAccess: mocks.fetchAcceso,
  fetchEvRows: mocks.fetchEv,
  fetchEvLive: mocks.fetchEvLive,
}))
vi.mock('@privy-io/react-auth', () => ({ useIdentityToken: () => ({ identityToken: mocks.identityToken() }) }))

import { MachineTrackerPage } from './MachineTrackerPage'

const accesoOk: TrackerAccess = {
  allowed: true, wagered_usd: 500, required_usd: 100, missing_usd: 0, window_days: 7,
  via: 'wager', pass_until: null, pass_prices: {},
}
const accesoCerrado: TrackerAccess = {
  allowed: false, wagered_usd: 0, required_usd: 100, missing_usd: 100, window_days: 7,
  via: null, pass_until: null, pass_prices: {},
}

beforeEach(() => {
  localStorage.clear()
  mocks.fetchEv.mockResolvedValue({ rows: [], updated_at: 0 })
  mocks.fetchEvLive.mockResolvedValue({ rows: [], updated_at: 0 })
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('MachineTrackerPage · access does not let itself be trampled by a stale response', () => {
  it('a response WITHOUT a token that arrives AFTER the one that does have a token does not close the gate', async () => {
    // Reproduces what Privy does on a real load: `identityToken` arrives first as `null` and
    // afterward with the real token, so there are TWO `fetchTrackerAccess` calls in flight at
    // once. If the one for `null` (which the backend answers with `allowed: false`) takes
    // longer than the one for the real token, its `.then` can land last and overwrite the good
    // access with the closed gate.
    let resolverSinToken!: (v: TrackerAccess) => void
    let resolverConToken!: (v: TrackerAccess) => void
    mocks.identityToken.mockReturnValue(null)
    mocks.fetchAcceso.mockImplementation((token: string | null | undefined) =>
      token
        ? new Promise<TrackerAccess>((r) => { resolverConToken = r })
        : new Promise<TrackerAccess>((r) => { resolverSinToken = r }))

    const { rerender } = render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)

    // Privy resolves the token: the access effect fires again (it depends on
    // `identityToken`) and fires a SECOND request, with the token, while the first is still alive.
    mocks.identityToken.mockReturnValue('tok')
    rerender(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)

    // The NEWEST one (with token) resolves first...
    await act(async () => { resolverConToken(accesoOk) })
    // ...and the OLD one (without token) resolves afterward. Without a guard, this would
    // overwrite the good access.
    await act(async () => { resolverSinToken(accesoCerrado) })

    // The gate ("... to go") must NOT be visible: the player really has access.
    expect(screen.queryByText(/to go/i)).toBeNull()
    // And the tracker panel IS visible (no rows yet, but that is the panel's own notice, not the gate).
    expect(await screen.findByText(/No machines measured yet/i)).toBeTruthy()
  })

  it('the other way around: if the one with a token is the stale one, the newest one wins (no access)', async () => {
    // Symmetric case: if the player loses the pass in between, the response that should win is
    // the LAST one requested, not the one that resolves first.
    let resolverConToken!: (v: TrackerAccess) => void
    let resolverSinToken!: (v: TrackerAccess) => void
    mocks.identityToken.mockReturnValue('tok')
    mocks.fetchAcceso.mockImplementation((token: string | null | undefined) =>
      token
        ? new Promise<TrackerAccess>((r) => { resolverConToken = r })
        : new Promise<TrackerAccess>((r) => { resolverSinToken = r }))

    const { rerender } = render(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)

    mocks.identityToken.mockReturnValue(null)
    rerender(<MemoryRouter><MachineTrackerPage /></MemoryRouter>)

    // The OLD one (with token) resolves first with access...
    await act(async () => { resolverConToken(accesoOk) })
    // ...but the NEWEST one (without token) is the one that wins, and gives no access.
    await act(async () => { resolverSinToken(accesoCerrado) })

    expect(await screen.findByText(/to go/i)).toBeTruthy()
  })
})
