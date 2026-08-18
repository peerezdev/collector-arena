import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useIdentityToken } from '@privy-io/react-auth'
import { COLORS, FONTS, GRADIENT } from '../../theme'
import { fmtGimmighouls, fmtGimmighoulsCompacto } from '../../gimmighouls'
import { useIsWide } from '../../useIsWide'
import { useEmbeddedSolanaAddress } from '../../../wallet/embedded'
import {
  fetchLeaderboard, fetchUser, applyReferralCode,
  type LeaderboardRow,
} from '../../../onchain/leaderboardClient'

function shortWallet(w: string): string {
  return w.length > 10 ? `${w.slice(0, 4)}…${w.slice(-4)}` : w
}

const TINTS = ['linear-gradient(135deg,#ff6bb5,#d4127a)', 'linear-gradient(135deg,#4ea8ff,#6a5bff)', 'linear-gradient(135deg,#f5c542,#e8732c)', 'linear-gradient(135deg,#00ffc4,#16a87a)', 'linear-gradient(135deg,#ff6e8a,#d23a5e)']
const tintFor = (w: string) => TINTS[Math.abs([...(w || 'x')].reduce((h, c) => (h * 31 + c.charCodeAt(0)) | 0, 0)) % TINTS.length]

const MEDALS: Record<number, { medal: string; glow: string; accent: string; cardBg: string; cardBorder: string }> = {
  1: { medal: 'linear-gradient(135deg,#ffe08a,#f5b73d)', glow: 'rgba(245,183,61,.6)', accent: '#f5c542', cardBg: 'linear-gradient(180deg,rgba(245,197,66,.14),rgba(255,255,255,.012))', cardBorder: 'rgba(245,197,66,.5)' },
  2: { medal: 'linear-gradient(135deg,#e6edf5,#b6c2d2)', glow: 'rgba(200,212,228,.5)', accent: '#dbe4ee', cardBg: 'linear-gradient(180deg,rgba(220,228,238,.11),rgba(255,255,255,.012))', cardBorder: 'rgba(220,228,238,.45)' },
  3: { medal: 'linear-gradient(135deg,#e6a96a,#c07a3e)', glow: 'rgba(200,130,70,.5)', accent: '#e6a96a', cardBg: 'linear-gradient(180deg,rgba(210,150,90,.13),rgba(255,255,255,.012))', cardBorder: 'rgba(210,150,90,.45)' },
}

// A small Gimmighoul coin (matches the gold token in the mockup).
function Coin({ size = 14 }: { size?: number }) {
  return <span style={{ width: size, height: size, borderRadius: '50%', background: 'radial-gradient(circle at 35% 30%,#ffd96b,#e8a020)', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.4)', flexShrink: 0, display: 'inline-block' }} />
}

const RANGES = [['all', 'Global'], ['month', 'Month'], ['week', 'Week']] as const

// The public board is parked until we publish it: stats keep being tracked in the
// backend, but nothing is shown yet. Flip to true to bring the whole ranking back.
const RANKING_LIVE: boolean = false

export function LeaderboardPage() {
  const navigate = useNavigate()
  const myWallet = useEmbeddedSolanaAddress()
  const { identityToken } = useIdentityToken()
  const wide = useIsWide('(min-width: 760px)')
  const fmtN = (n: number) => (wide ? fmtGimmighouls(n) : fmtGimmighoulsCompacto(n))

  const [rows, setRows] = useState<LeaderboardRow[]>([])
  const [loading, setLoading] = useState(RANKING_LIVE) // nothing to load while the board is parked
  const [myGimmighouls, setMyGimmighouls] = useState<number | null>(null)
  const [myCode, setMyCode] = useState<string | null>(null)
  const [range, setRange] = useState<string>('all') // all-time only for now (no time-series backend)

  const [codeInput, setCodeInput] = useState('')
  const [applying, setApplying] = useState(false)
  const [applyMsg, setApplyMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!RANKING_LIVE) return
    let cancelled = false
    fetchLeaderboard()
      .then((r) => { if (!cancelled) setRows(r) })
      .catch(() => { /* keep empty list */ })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!myWallet) return
    let cancelled = false
    fetchUser(myWallet)
      .then((u) => { if (cancelled) return; setMyGimmighouls(u.gimmighouls); setMyCode(u.referred_by) })
      .catch(() => { /* ignore */ })
    return () => { cancelled = true }
  }, [myWallet])

  async function onApply() {
    if (!myWallet || !identityToken || !codeInput.trim()) return
    setApplying(true)
    setApplyMsg(null)
    try {
      const res = await applyReferralCode(identityToken, myWallet, codeInput.trim())
      setMyCode(res.code)
      setApplyMsg({ ok: true, text: `Applied ${res.code} · +${Math.round(res.boost_pct * 100)}% boost` })
      setCodeInput('')
    } catch (e) {
      setApplyMsg({ ok: false, text: (e as Error).message })
    } finally {
      setApplying(false)
    }
  }

  const myIdx = myWallet ? rows.findIndex((r) => r.wallet === myWallet) : -1
  const myRow = myIdx >= 0 ? rows[myIdx] : null
  const myGh = myGimmighouls ?? myRow?.gimmighouls ?? null
  // Lead = margin over the next player (if #1) or gap to the player above.
  const lead = myIdx === 0 && rows[1] ? (myGh ?? 0) - rows[1].gimmighouls
    : myIdx > 0 ? (myGh ?? 0) - rows[myIdx - 1].gimmighouls : null

  const top3 = rows.slice(0, 3)
  const podiumOrder = top3.length === 3 ? [top3[1], top3[0], top3[2]] : top3 // 2-1-3 stepped

  return (
    <div style={{ padding: '22px clamp(18px,2.4vw,34px) 44px', display: 'flex', flexDirection: 'column', gap: 22 }}>
      {/* title + range tabs */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <span style={{ width: 42, height: 42, borderRadius: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(245,197,66,.12)', border: '1px solid rgba(245,197,66,.35)', color: '#f5c542', boxShadow: '0 0 22px -8px rgba(245,197,66,.8)' }}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6" /><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18" /><path d="M4 22h16" /><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22" /><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22" /><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z" /></svg>
        </span>
        <h1 style={{ margin: 0, fontFamily: FONTS.display, fontSize: 'clamp(26px,3.2vw,34px)', fontWeight: 700, letterSpacing: '-.02em' }}>Ranking</h1>
        <span style={{ fontSize: 14, color: COLORS.muted }}>· by accumulated Gimmighouls</span>
        {RANKING_LIVE && (
          <div style={{ marginLeft: 'auto', display: 'inline-flex', gap: 4, padding: 4, borderRadius: 13, background: '#ffffff08', border: `1px solid ${COLORS.border}` }}>
            {RANGES.map(([k, label]) => (
              <button key={k} onClick={() => setRange(k)} style={{
                position: 'relative', padding: '8px 16px', borderRadius: 9, border: 0, cursor: 'pointer',
                fontFamily: FONTS.body, fontSize: 13.5, fontWeight: 500,
                color: range === k ? COLORS.text : COLORS.muted,
                background: range === k ? 'linear-gradient(180deg,rgba(0,255,196,.16),rgba(255,46,151,.10))' : 'transparent',
                boxShadow: range === k ? 'inset 0 0 0 1px rgba(0,255,196,.32)' : 'none',
              }}>{label}</button>
            ))}
          </div>
        )}
      </div>

      {/* Board parked: only the "we're tracking you" notice + the creator code box. */}
      {!RANKING_LIVE && (
        <section style={{
          position: 'relative', overflow: 'hidden', borderRadius: 22, padding: 'clamp(22px,2.6vw,34px)',
          background: 'linear-gradient(135deg,rgba(0,255,196,.12),rgba(13,17,22,.55) 48%,rgba(255,46,151,.12))',
          border: `1px solid ${COLORS.border}`,
          display: 'flex', gap: 30, flexWrap: 'wrap', alignItems: 'center',
        }}>
          <div style={{ flex: '1 1 340px', minWidth: 0 }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '5px 12px', borderRadius: 20, background: 'rgba(0,255,196,.12)', border: '1px solid rgba(0,255,196,.35)', fontFamily: FONTS.mono, fontSize: 10.5, letterSpacing: '.2em', color: COLORS.green }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: COLORS.green, boxShadow: `0 0 10px ${COLORS.green}` }} />
              TRACKING
            </span>
            <h2 style={{ margin: '14px 0 0', fontFamily: FONTS.display, fontSize: 'clamp(22px,2.8vw,30px)', fontWeight: 700, letterSpacing: '-.02em', lineHeight: 1.15 }}>
              Your stats are being tracked
            </h2>
            <p style={{ margin: '10px 0 0', maxWidth: 520, fontFamily: FONTS.body, fontSize: 14.5, lineHeight: 1.6, color: COLORS.muted }}>
              Every battle, win and Gimmighoul you earn is already counting. We'll publish the ranking soon, so keep playing to land high when it goes live.
            </p>
          </div>

          <CreatorCodeCard
            myWallet={myWallet} identityToken={identityToken} myCode={myCode}
            codeInput={codeInput} setCodeInput={setCodeInput}
            applying={applying} applyMsg={applyMsg} onApply={onApply}
          />
        </section>
      )}

      {RANKING_LIVE && <>
      {/* your standing + creator code */}
      <section style={{
        position: 'relative', overflow: 'hidden', borderRadius: 22, padding: 'clamp(20px,2.4vw,30px)',
        background: 'linear-gradient(135deg,rgba(0,255,196,.12),rgba(13,17,22,.55) 48%,rgba(255,46,151,.12))',
        border: `1px solid ${COLORS.border}`, animation: 'ba-leadglow 6s ease-in-out infinite',
      }}>
        <div style={{ position: 'relative', display: 'flex', gap: 30, flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ flex: '1 1 320px', minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 12px', borderRadius: 20, background: GRADIENT, color: '#06170f', fontFamily: FONTS.display, fontSize: 12, fontWeight: 700, boxShadow: '0 6px 22px -8px rgba(0,255,196,.8)' }}>
                🏆 {myIdx >= 0 ? `RANK #${myIdx + 1}` : 'UNRANKED'}
              </span>
              <span style={{ fontFamily: FONTS.mono, fontSize: 11, letterSpacing: '.2em', color: COLORS.muted }}>YOUR GIMMIGHOULS</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
              <div style={{ fontFamily: FONTS.display, fontSize: 'clamp(38px,6vw,58px)', fontWeight: 700, letterSpacing: '-.03em', lineHeight: .95, color: COLORS.green, textShadow: '0 4px 30px rgba(0,255,196,.35)' }}>
                {myGh != null ? fmtN(myGh) : '—'}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 18 }}>
              {lead != null && <Stat label={myIdx === 0 ? 'LEAD' : 'GAP'} value={`${lead >= 0 ? '+' : ''}${fmtN(lead)}`} accent />}
              <Stat label="PLAYERS" value={String(rows.length)} />
            </div>
          </div>

          <CreatorCodeCard
            myWallet={myWallet} identityToken={identityToken} myCode={myCode}
            codeInput={codeInput} setCodeInput={setCodeInput}
            applying={applying} applyMsg={applyMsg} onApply={onApply}
          />
        </div>
      </section>

      {/* podium top 3 */}
      {top3.length === 3 && (
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 14 }}>
          {podiumOrder.map((d) => {
            const rank = rows.indexOf(d) + 1
            const m = MEDALS[rank]
            const first = rank === 1
            const isMe = d.wallet === myWallet
            return (
              <div key={d.wallet} onClick={() => navigate(`/profile/${d.wallet}`)} style={{
                flex: '1 1 0', minWidth: 0, position: 'relative', overflow: 'hidden', cursor: 'pointer',
                display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, padding: first ? '26px 18px 24px' : '20px 16px',
                borderRadius: 20, border: `1px solid ${m.cardBorder}`,
                background: m.cardBg,
                boxShadow: `0 0 60px -18px ${m.glow}`,
              }}>
                <div style={{ width: first ? 54 : 46, height: first ? 54 : 46, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: FONTS.mono, fontWeight: 700, fontSize: first ? 20 : 17, color: '#06170f', background: m.medal, boxShadow: `0 8px 26px -8px ${m.glow}` }}>{rank}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 28, height: 28, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, color: '#06170f', background: tintFor(d.wallet), border: `2px solid ${isMe ? 'rgba(0,255,196,.7)' : 'rgba(255,255,255,.12)'}` }}>{(d.alias ?? d.wallet).slice(0, 1).toUpperCase()}</span>
                  <span style={{ fontSize: 15, fontWeight: 700, color: isMe ? COLORS.green : COLORS.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 120 }}>{d.alias ?? shortWallet(d.wallet)}</span>
                </div>
                <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: FONTS.display, fontSize: first ? 21 : 18, fontWeight: 700, letterSpacing: '-.02em', color: m.accent }}><Coin size={16} />{fmtN(d.gimmighouls)}</div>
              </div>
            )
          })}
        </div>
      )}

      {/* full ranking */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
          <span style={{ fontFamily: FONTS.mono, fontSize: 11, letterSpacing: '.2em', color: COLORS.muted }}>FULL RANKING</span>
          <span style={{ flex: 1, height: 1, background: 'linear-gradient(90deg,rgba(255,255,255,.10),transparent)' }} />
        </div>
        {loading ? (
          <div style={{ color: COLORS.muted, fontFamily: FONTS.body }}>Loading…</div>
        ) : rows.length === 0 ? (
          <div style={{ color: COLORS.muted, fontFamily: FONTS.body }}>No players yet.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {rows.map((r, i) => {
              const isMe = myWallet != null && r.wallet === myWallet
              return (
                <div key={r.wallet} onClick={() => navigate(`/profile/${r.wallet}`)} style={{
                  position: 'relative', overflow: 'hidden', display: 'flex', alignItems: 'center', gap: 16, padding: '14px 20px', borderRadius: 14, cursor: 'pointer',
                  background: isMe ? 'linear-gradient(90deg,rgba(0,255,196,.10),rgba(255,255,255,.012))' : 'linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.012))',
                  border: `1px solid ${isMe ? 'rgba(0,255,196,.45)' : COLORS.border}`,
                  boxShadow: isMe ? '0 0 40px -16px rgba(0,255,196,.5)' : 'none',
                }}>
                  {isMe && <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 3, background: GRADIENT }} />}
                  <span style={{ flex: 'none', width: 38, fontFamily: FONTS.mono, fontSize: 15, fontWeight: 700, color: i < 3 ? '#f5c542' : '#6c7682' }}>#{i + 1}</span>
                  <span style={{ flex: 'none', width: 34, height: 34, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 700, color: '#06170f', background: tintFor(r.wallet), border: `2px solid ${isMe ? 'rgba(0,255,196,.7)' : 'rgba(255,255,255,.12)'}` }}>{(r.alias ?? r.wallet).slice(0, 1).toUpperCase()}</span>
                  <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 9 }}>
                    <span style={{ fontSize: 15.5, fontWeight: 700, color: isMe ? COLORS.green : COLORS.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.alias ?? shortWallet(r.wallet)}</span>
                    {isMe && <span style={{ flex: 'none', padding: '2px 8px', borderRadius: 6, background: 'rgba(0,255,196,.14)', border: '1px solid rgba(0,255,196,.4)', fontSize: 9.5, fontWeight: 700, color: COLORS.green }}>YOU</span>}
                  </div>
                  <div style={{ flex: 'none', display: 'flex', alignItems: 'center', gap: 7 }}>
                    <Coin />
                    <span style={{ fontFamily: FONTS.mono, fontSize: 15, fontWeight: 700, letterSpacing: '-.01em', color: isMe ? COLORS.green : COLORS.text }}>{fmtN(r.gimmighouls)}</span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
      </>}
    </div>
  )
}

function CreatorCodeCard({ myWallet, identityToken, myCode, codeInput, setCodeInput, applying, applyMsg, onApply }: {
  myWallet: string | null | undefined
  identityToken: string | null | undefined
  myCode: string | null
  codeInput: string
  setCodeInput: (v: string) => void
  applying: boolean
  applyMsg: { ok: boolean; text: string } | null
  onApply: () => void
}) {
  const disabled = !myWallet || !identityToken || applying || !codeInput.trim()
  return (
    <div style={{ flex: '1 1 300px', minWidth: 260, padding: 20, borderRadius: 18, background: 'rgba(8,10,14,.45)', border: `1px solid ${COLORS.border}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 8 }}>
        <span style={{ width: 30, height: 30, borderRadius: 9, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(255,46,151,.14)', border: '1px solid rgba(255,46,151,.4)', color: '#ff6bb5' }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 12 20 22 4 22 4 12" /><rect x="2" y="7" width="20" height="5" /><line x1="12" y1="22" x2="12" y2="7" /><path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z" /><path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z" /></svg>
        </span>
        <span style={{ fontFamily: FONTS.display, fontSize: 15, fontWeight: 700 }}>Have a creator code?</span>
      </div>
      {myCode ? (
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: COLORS.text }}>
          Applied: <strong style={{ color: COLORS.green }}>{myCode}</strong>, boosting your Gimmighouls.
        </p>
      ) : (
        <>
          <p style={{ margin: '0 0 14px', fontSize: 13, lineHeight: 1.5, color: COLORS.muted }}>Apply it to boost your Gimmighouls and climb faster.</p>
          <div style={{ display: 'flex', gap: 10 }}>
            <input
              aria-label="Referral code" placeholder="Referral code" value={codeInput}
              onChange={(e) => setCodeInput(e.target.value)} disabled={!myWallet || applying}
              style={{ flex: 1, minWidth: 0, padding: '12px 15px', borderRadius: 12, border: `1px solid ${COLORS.border}`, background: '#ffffff08', color: COLORS.text, fontFamily: FONTS.mono, fontSize: 13.5, letterSpacing: '.04em', outline: 'none' }}
            />
            <button type="button" onClick={onApply} disabled={disabled}
              style={{ flex: 'none', padding: '12px 22px', borderRadius: 12, border: 0, cursor: disabled ? 'default' : 'pointer', fontFamily: FONTS.display, fontSize: 14, fontWeight: 700, color: '#06170f', background: GRADIENT, opacity: disabled ? 0.5 : 1, boxShadow: '0 0 20px -6px rgba(0,255,196,.7)' }}>
              {applying ? 'Applying…' : 'Apply'}
            </button>
          </div>
        </>
      )}
      {applyMsg && <div style={{ marginTop: 10, fontFamily: FONTS.body, fontSize: 13, color: applyMsg.ok ? COLORS.green : COLORS.red }}>{applyMsg.text}</div>}
    </div>
  )
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div style={{ padding: '10px 16px', borderRadius: 13, background: '#ffffff08', border: `1px solid ${COLORS.border}` }}>
      <div style={{ fontFamily: FONTS.mono, fontSize: 10, letterSpacing: '.16em', color: COLORS.muted }}>{label}</div>
      <div style={{ fontFamily: FONTS.display, fontSize: 18, fontWeight: 700, letterSpacing: '-.01em', marginTop: 2, color: accent ? COLORS.green : COLORS.text }}>{value}</div>
    </div>
  )
}
