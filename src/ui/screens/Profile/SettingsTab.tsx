import { useEffect, useState } from 'react'
import { useIdentityToken } from '@privy-io/react-auth'
import { COLORS, FONTS, GRADIENT } from '../../theme'
import { useProfile } from '../../../hooks/useProfile'
import { validateUsername } from '../../../profile/username'
import { config } from '../../../onchain/config'
import { ExportKeyPanel } from './ExportKeyPanel'

const SOL_ADDRESS = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/

const sectionStyle: React.CSSProperties = {
  borderRadius: 18, padding: 22,
  background: 'linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.012))',
  border: `1px solid ${COLORS.border}`,
}
const inputStyle: React.CSSProperties = {
  flex: 1, minWidth: 200, background: '#0a0e16', border: `1px solid ${COLORS.border}`, borderRadius: 12,
  padding: '12px 15px', color: COLORS.text, fontSize: 14, fontFamily: FONTS.body, outline: 'none',
}
const saveBtn: React.CSSProperties = {
  padding: '12px 22px', borderRadius: 12, border: 'none', color: '#06120c', fontWeight: 800,
  fontSize: 14, fontFamily: FONTS.display, background: GRADIENT,
}

export function SettingsTab() {
  const { username, withdrawAddress, refresh } = useProfile()
  const { identityToken } = useIdentityToken()
  const [value, setValue] = useState('')
  const [status, setStatus] = useState<{ kind: 'idle' | 'ok' | 'error'; msg?: string }>({ kind: 'idle' })
  const [saving, setSaving] = useState(false)

  const [addr, setAddr] = useState('')
  const [addrStatus, setAddrStatus] = useState<{ kind: 'idle' | 'ok' | 'error'; msg?: string }>({ kind: 'idle' })
  const [savingAddr, setSavingAddr] = useState(false)

  useEffect(() => { if (username) setValue(username) }, [username])
  useEffect(() => { setAddr(withdrawAddress ?? '') }, [withdrawAddress])

  async function save() {
    if (saving) return
    const err = validateUsername(value)
    if (err) { setStatus({ kind: 'error', msg: err }); return }
    if (!identityToken) { setStatus({ kind: 'error', msg: 'Log in to set a username.' }); return }
    setSaving(true); setStatus({ kind: 'idle' })
    try {
      const resp = await fetch(`${config.backendUrl}/users/me/alias`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${identityToken}`, 'ngrok-skip-browser-warning': 'true' },
        body: JSON.stringify({ alias: value }),
      })
      if (resp.status === 409) setStatus({ kind: 'error', msg: 'That username is already taken.' })
      else if (!resp.ok) setStatus({ kind: 'error', msg: 'Could not save. Try again.' })
      else { setStatus({ kind: 'ok', msg: 'Saved ✓' }); refresh() }
    } catch { setStatus({ kind: 'error', msg: 'Network error.' }) }
    finally { setSaving(false) }
  }

  async function saveAddr() {
    if (savingAddr) return
    const trimmed = addr.trim()
    if (trimmed !== '' && !SOL_ADDRESS.test(trimmed)) { setAddrStatus({ kind: 'error', msg: 'Enter a valid Solana wallet address.' }); return }
    if (!identityToken) { setAddrStatus({ kind: 'error', msg: 'Log in to save.' }); return }
    setSavingAddr(true); setAddrStatus({ kind: 'idle' })
    try {
      const resp = await fetch(`${config.backendUrl}/users/me/withdraw-address`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${identityToken}`, 'ngrok-skip-browser-warning': 'true' },
        body: JSON.stringify({ address: trimmed }),
      })
      if (!resp.ok) setAddrStatus({ kind: 'error', msg: 'Could not save. Try again.' })
      else { setAddrStatus({ kind: 'ok', msg: 'Saved ✓' }); refresh() }
    } catch { setAddrStatus({ kind: 'error', msg: 'Network error.' }) }
    finally { setSavingAddr(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 560 }}>
      {/* username */}
      <section style={sectionStyle}>
        <div style={{ fontFamily: FONTS.mono, fontSize: 11, letterSpacing: '.2em', color: COLORS.muted, marginBottom: 14 }}>PUBLIC NAME</div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <input value={value} onChange={(e) => { setValue(e.target.value); setStatus({ kind: 'idle' }) }}
            placeholder="3–20 chars, letters/numbers/_" style={inputStyle} />
          <button onClick={save} disabled={saving} style={{ ...saveBtn, cursor: saving ? 'wait' : 'pointer', opacity: saving ? 0.6 : 1 }}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
        <div style={{ fontSize: 12, marginTop: 8, minHeight: 16, color: status.kind === 'error' ? COLORS.red : COLORS.green }}>{status.msg ?? ''}</div>
      </section>

      {/* withdrawal address */}
      <section style={sectionStyle}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginBottom: 14 }}>
          <div style={{ fontFamily: FONTS.mono, fontSize: 11, letterSpacing: '.2em', color: COLORS.green }}>WITHDRAWAL ADDRESS</div>
          <div style={{ fontFamily: FONTS.mono, fontSize: 10, color: COLORS.muted }}>USDC PAYOUT DESTINATION</div>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <input value={addr} onChange={(e) => { setAddr(e.target.value.trim()); setAddrStatus({ kind: 'idle' }) }}
            spellCheck={false} placeholder="Solana wallet address" style={{ ...inputStyle, fontFamily: FONTS.mono, fontSize: 13, color: addr ? COLORS.green : COLORS.text }} />
          <button onClick={saveAddr} disabled={savingAddr} style={{ ...saveBtn, cursor: savingAddr ? 'wait' : 'pointer', opacity: savingAddr ? 0.6 : 1 }}>
            {savingAddr ? 'Saving…' : 'Save'}
          </button>
        </div>
        <div style={{ fontSize: 12, marginTop: 8, minHeight: 16, color: addrStatus.kind === 'error' ? COLORS.red : COLORS.green }}>{addrStatus.msg ?? ''}</div>
      </section>

      {/* private key export — Privy renders the key on its own domain, we only open the dialog */}
      <ExportKeyPanel />
    </div>
  )
}
