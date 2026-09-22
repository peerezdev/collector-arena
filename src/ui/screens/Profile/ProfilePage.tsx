import { useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { COLORS, FONTS, GRADIENT, formatUsd } from '../../theme'
import { useEmbeddedSolanaAddress } from '../../../wallet/embedded'
import { useProfile } from '../../../hooks/useProfile'
import { useUserStats } from '../../../hooks/useUserStats'
import { OverviewTab } from './OverviewTab'
import { InventoryTab } from './InventoryTab'
import { HistoryTab } from './HistoryTab'
import { SettingsTab } from './SettingsTab'
import { TipModal } from '../../components/TipModal'
import { TIPS_ENABLED } from '../../../featureFlags'
import { EmblemaRango } from '../../badges/EmblemaRango'
import { TagUsuario } from '../../badges/TagUsuario'
import { RankProgress } from './RankProgress'

type Tab = 'overview' | 'inventory' | 'history' | 'settings'

function shortWallet(w: string): string {
  return w.length > 9 ? `${w.slice(0, 4)}…${w.slice(-4)}` : w
}

function HeroStat({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div>
      <div style={{ fontFamily: FONTS.mono, fontSize: 9.5, letterSpacing: '.14em', color: COLORS.muted }}>{label}</div>
      <div style={{ fontFamily: FONTS.display, fontSize: 22, fontWeight: 800, letterSpacing: '-.01em', color: color ?? COLORS.text, marginTop: 2 }}>{value}</div>
    </div>
  )
}

export function ProfilePage() {
  const { wallet } = useParams<{ wallet?: string }>()
  const own = useEmbeddedSolanaAddress()
  const isSelf = !wallet || wallet === own
  const target = isSelf ? undefined : wallet
  const address = target ?? own

  const { username, rank, tags, rankProgress } = useProfile(target)
  const { stats } = useUserStats(target)

  const tabs: { key: Tab; label: string }[] = isSelf
    ? [{ key: 'overview', label: 'Overview' }, { key: 'inventory', label: 'Inventory' }, { key: 'history', label: 'History' }, { key: 'settings', label: 'Settings' }]
    : [{ key: 'overview', label: 'Overview' }, { key: 'inventory', label: 'Inventory' }, { key: 'history', label: 'History' }]

  const [params] = useSearchParams()
  const wanted = params.get('tab')
  const initialTab: Tab = wanted === 'inventory' || wanted === 'history' || wanted === 'settings' ? wanted : 'overview'
  const [tab, setTab] = useState<Tab>(initialTab)
  const [tipOpen, setTipOpen] = useState(false)

  const handle = username ?? (address ? shortWallet(address) : 'Player')
  const initial = (username ?? address ?? '?').slice(0, 1).toUpperCase()
  const winRate = stats && stats.battles > 0 ? `${Math.round(stats.winRate * 100)}%` : '—'

  return (
    // Uniform width across every tab (no jump when switching). It's a maxWidth inside the shell's
    // `1fr` main column, so it already respects the left rail AND the chat dock (collapsed/expanded)
    // — no viewport-width breakout that would slide under the chat.
    <div style={{ maxWidth: 1320, width: '100%', margin: '0 auto', padding: '28px 22px' }}>
      {/* ── hero ── */}
      <section style={{
        position: 'relative', overflow: 'hidden', borderRadius: 22, padding: 'clamp(22px,2.6vw,32px)', marginBottom: 22,
        background: 'linear-gradient(135deg,rgba(255,46,151,.14),rgba(13,17,22,.55) 46%,rgba(0,255,196,.08))',
        border: `1px solid ${COLORS.border}`,
      }}>
        <div style={{ position: 'absolute', top: '-45%', right: '-4%', width: 320, height: 320, borderRadius: '50%', background: 'radial-gradient(circle,rgba(255,46,151,.26),transparent 65%)', pointerEvents: 'none' }} />
        <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap' }}>
          {/* avatar */}
          <div style={{ flex: 'none', width: 110, height: 110, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(255,255,255,.06)', border: `1px solid ${COLORS.border}` }}>
            <span style={{ width: 86, height: 86, borderRadius: '50%', background: 'linear-gradient(135deg,#f5c542,#e8732c)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: FONTS.display, fontSize: 32, fontWeight: 800, color: '#3a1f06' }}>{initial}</span>
          </div>
          {/* identity */}
          <div style={{ flex: '1 1 320px', minWidth: 0 }}>
            <div style={{ fontFamily: FONTS.mono, fontSize: 11, letterSpacing: '.22em', color: COLORS.violet, marginBottom: 8 }}>COLLECTOR ARENA · PROFILE</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
              <h1 style={{ margin: 0, fontFamily: FONTS.display, fontSize: 'clamp(26px,3.4vw,38px)', fontWeight: 800, letterSpacing: '-.02em' }}>{handle}</h1>
              <EmblemaRango rank={rank} size={28} />
              {(tags ?? []).map((t) => <TagUsuario key={t} tag={t} />)}
              {isSelf && (
                <button onClick={() => setTab('settings')} title="Edit profile"
                  style={{ width: 30, height: 30, borderRadius: 9, border: `1px solid ${COLORS.border}`, background: '#ffffff08', color: COLORS.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>
                </button>
              )}
              {TIPS_ENABLED && !isSelf && wallet && (
                <button onClick={() => setTipOpen(true)}
                  style={{ padding: '7px 16px', borderRadius: 10, border: 'none', background: GRADIENT, color: '#06120c', fontWeight: 800, fontSize: 13, fontFamily: FONTS.display, cursor: 'pointer' }}>
                  Send tip
                </button>
              )}
            </div>
            <RankProgress rank={rank} progress={rankProgress} />
            <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap' }}>
              <HeroStat label="BATTLES" value={stats?.battles ?? 0} />
              <HeroStat label="WINS" value={stats?.wins ?? 0} />
              <HeroStat label="WIN RATE" value={winRate} color={COLORS.green} />
              <HeroStat label="TOTAL WAGERED" value={stats ? formatUsd(stats.totalWageredUsd) : '—'} color={COLORS.violet} />
            </div>
          </div>
        </div>
      </section>

      {/* ── tabs ── */}
      <div style={{ display: 'flex', gap: 6, borderBottom: `1px solid ${COLORS.border}`, marginBottom: 22, overflowX: 'auto' }}>
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            style={{
              background: 'transparent', border: 'none',
              borderBottom: tab === t.key ? `2px solid ${COLORS.green}` : '2px solid transparent',
              color: tab === t.key ? COLORS.text : COLORS.muted,
              fontFamily: FONTS.body, fontWeight: 700, fontSize: 14, padding: '10px 14px', cursor: 'pointer', whiteSpace: 'nowrap',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'overview' && <OverviewTab wallet={target} stats={stats} />}
      {tab === 'inventory' && <InventoryTab wallet={target} />}
      {tab === 'history' && <HistoryTab wallet={target} />}
      {tab === 'settings' && isSelf && <SettingsTab />}

      {TIPS_ENABLED && !isSelf && wallet && (
        <TipModal open={tipOpen} to={{ wallet, alias: username }} source="profile" onClose={() => setTipOpen(false)} />
      )}
    </div>
  )
}
