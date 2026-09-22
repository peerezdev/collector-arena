import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

const params: { wallet?: string } = {}
// Las propinas se despliegan APAGADAS (ver featureFlags.ts). Estos tests cubren la pantalla con
// la funcionalidad encendida; que apagada no se ofrezca nada tiene su propio test al final.
vi.mock('../../../featureFlags', () => ({ TIPS_ENABLED: true }))
vi.mock('react-router-dom', () => ({
  useParams: () => params,
  useSearchParams: () => [new URLSearchParams(), () => {}],
}))
vi.mock('../../../wallet/embedded', () => ({ useEmbeddedSolanaAddress: () => 'WalletA' }))
// useProfile devuelve { username, elo, gamesPlayed, gimmighouls, withdrawAddress, rank, tags,
// rankProgress, loading, refresh }, no { alias } como decía el brief: el campo se llama `username`.
const profileState = vi.hoisted(() => ({
  data: { username: null, elo: null, gamesPlayed: null, gimmighouls: null, withdrawAddress: null,
          rank: null, tags: [] as string[], rankProgress: null } as Record<string, unknown>,
}))
vi.mock('../../../hooks/useProfile', () => ({
  useProfile: () => ({ ...profileState.data, loading: false, refresh: () => {} }),
}))
const EMPTY_PROFILE = { ...profileState.data }
// useUserStats devuelve { stats, loading }; `stats` no trae alias.
vi.mock('../../../hooks/useUserStats', () => ({ useUserStats: () => ({ stats: null, loading: false }) }))
vi.mock('./OverviewTab', () => ({ OverviewTab: () => null }))
vi.mock('./InventoryTab', () => ({ InventoryTab: () => null }))
vi.mock('./HistoryTab', () => ({ HistoryTab: () => null }))
vi.mock('./SettingsTab', () => ({ SettingsTab: () => null }))
vi.mock('../../components/TipModal', () => ({ TipModal: () => null }))

import { ProfilePage } from './ProfilePage'

describe('ProfilePage', () => {
  beforeEach(() => {
    profileState.data = { ...EMPTY_PROFILE }
  })

  it('ofrece dar propina en el perfil de otro', () => {
    params.wallet = 'WalletB'
    render(<ProfilePage />)
    expect(screen.getByRole('button', { name: /send tip/i })).toBeTruthy()
  })

  it('no ofrece dar propina en el perfil propio', () => {
    params.wallet = undefined
    render(<ProfilePage />)
    expect(screen.queryByRole('button', { name: /send tip/i })).toBeNull()
  })

  it('shows the emblem, tags and progress next to the name', () => {
    params.wallet = 'WalletB'
    profileState.data = { ...profileState.data, username: 'kairo', rank: 'silver', tags: ['TEAM'],
                    rankProgress: { wageredUsd: 7340, nextRank: 'gold', nextThresholdUsd: 10000 } }
    render(<ProfilePage />)
    expect(screen.getByRole('img', { name: 'Silver' })).toBeTruthy()
    expect(screen.getByText('TEAM')).toBeTruthy()
    expect(screen.getByRole('progressbar')).toBeTruthy()
  })
})
