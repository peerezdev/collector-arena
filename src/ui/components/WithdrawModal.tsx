/**
 * WithdrawModal — send USDC or $CARDS out of the embedded wallet.
 *
 * Props: { open, onClose }
 * Asks for a destination Solana wallet and an amount. The amount must be > 0 and
 * never exceed the user's available balance (USDC minus reserved; CARDS is the raw on-chain
 * balance, nothing reserved against it — see the `token` branch below).
 *
 * Submits to POST /users/me/withdraw, which moves the chosen token from the player's (delegated)
 * wallet to the destination with the operator as fee-payer. Gated by the delegation flow (same
 * as battles).
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useIdentityToken } from '@privy-io/react-auth'
import { COLORS, GRADIENT, FONTS, SHADOW, Z } from '../theme'
import { useReducedMotion } from '../useReducedMotion'
import { useUsdcBalance, useCardsBalance } from '../../wallet/useUsdcBalance'
import { useReservedBalance, availableUsd } from '../../wallet/useReservedBalance'
import { useProfile } from '../../hooks/useProfile'
import { useDelegationGate } from './useDelegationGate'
import { DelegationGate } from './DelegationGate'
import { config } from '../../onchain/config'
import { formatUsd } from '../theme'
import { showToast } from '../toastBus'

interface WithdrawModalProps {
  open: boolean
  onClose: () => void
}

type WithdrawToken = 'usdc' | 'cards'

// Base58, 32–44 chars — a light sanity check, not full on-chain validation.
const SOL_ADDRESS = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/

// $CARDS no es dinero de la plataforma (es el airdrop de Collector Crypt), así que se pinta como
// cantidad de token, no como dólares — mismo criterio que ya usa la pantalla del claim.
function formatCards(v: number): string {
  return `${(Math.round(v * 100) / 100).toLocaleString('en-US', { maximumFractionDigits: 2 })} CARDS`
}

const inputStyle: React.CSSProperties = {
  width: '100%', background: '#0a0e16', border: `1px solid ${COLORS.border}`, borderRadius: 10,
  padding: '11px 13px', color: COLORS.text, fontSize: 14, fontFamily: FONTS.body, outline: 'none',
}
const labelStyle: React.CSSProperties = {
  fontFamily: FONTS.mono, fontSize: 9.5, fontWeight: 700, letterSpacing: '.16em', color: COLORS.muted,
}

export function WithdrawModal({ open, onClose }: WithdrawModalProps) {
  const reducedMotion = useReducedMotion()
  const { identityToken } = useIdentityToken()
  const gate = useDelegationGate()
  const { usdc } = useUsdcBalance()
  const { cards } = useCardsBalance()
  const { reserved } = useReservedBalance()
  const { withdrawAddress } = useProfile()

  const [token, setToken] = useState<WithdrawToken>('usdc')
  const [dest, setDest] = useState('')
  const [amount, setAmount] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // El disponible de USDC descuenta lo reservado por pack battles; el de CARDS es el saldo
  // on-chain tal cual — nada lo reserva porque CARDS no se apuesta en ninguna batalla.
  const available = token === 'cards' ? cards : availableUsd(usdc, reserved)
  const formatAmount = token === 'cards' ? formatCards : formatUsd

  // Prefill the destination from the saved withdrawal address when the modal opens.
  useEffect(() => {
    if (open && withdrawAddress) setDest((d) => (d === '' ? withdrawAddress : d))
  }, [open, withdrawAddress])

  if (!open) return null

  function selectToken(next: WithdrawToken) {
    setToken(next)
    setAmount('')
    setError(null)
  }

  const amountNum = Number(amount)
  const amountValid = amount !== '' && Number.isFinite(amountNum) && amountNum > 0 && available != null && amountNum <= available
  const destValid = SOL_ADDRESS.test(dest.trim())
  const canSubmit = destValid && amountValid && !busy

  function submit() {
    if (available == null) { setError('Balance unavailable. Try again.'); return }
    if (!destValid) { setError('Enter a valid Solana wallet address.'); return }
    if (amount === '' || !Number.isFinite(amountNum) || amountNum <= 0) { setError('Enter an amount greater than 0.'); return }
    if (amountNum > available) { setError(`Amount exceeds your available balance (${formatAmount(available)}).`); return }
    if (!identityToken) { setError('Log in to withdraw.'); return }
    setError(null)
    // Needs the wallet delegated so the server can sign the transfer (same as battles).
    gate.requireDelegation(async () => {
      setBusy(true)
      try {
        const resp = await fetch(`${config.backendUrl}/users/me/withdraw`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${identityToken}`, 'ngrok-skip-browser-warning': 'true' },
          body: JSON.stringify({ address: dest.trim(), amount: amountNum, token }),
        })
        // Cada motivo dice qué pasa Y qué hacer. Antes todo lo que no fuera 402 o 503 caía en
        // "Withdrawal failed. Please try again.", que además de no explicar nada era un mal
        // consejo: reintentar no arregla ni una partida en curso ni un importe por debajo del
        // mínimo. Los textos se escriben AQUÍ y no se reenvía el `detail` del backend: el suyo
        // describe la regla para quien lee un log ("you have an unfinished battle"), y al jugador
        // hay que decirle además cuándo se le desbloquea.
        if (resp.status === 402) { setError('Insufficient available balance.'); return }
        if (resp.status === 409) { setError("You're in a battle right now. USDC withdrawals unlock when it ends."); return }
        if (resp.status === 422) { setError('That amount is below the minimum withdrawal.'); return }
        if (resp.status === 429) { setError('Too many withdrawals. Wait a moment and try again.'); return }
        if (resp.status === 503) { setError('Withdrawals are temporarily unavailable.'); return }
        if (!resp.ok) { setError('Withdrawal failed. Please try again.'); return }
        showToast(`Withdrew ${formatAmount(amountNum)} to ${dest.slice(0, 4)}…${dest.slice(-4)} ✓`, 'success')
        onClose()
      } catch {
        setError('Network error.')
      } finally {
        setBusy(false)
      }
    })
  }

  return (
    <>
      {/* VA EN UN PORTAL A document.body, Y NO ES OPCIONAL. Este modal lo monta AuthButtons,
          que vive dentro de la barra superior, y esa barra lleva `backdrop-filter: blur(14px)`.
          Un ancestro con backdrop-filter (o transform, o perspective, o contain) se convierte en
          el marco de referencia de TODO `position:fixed` que tenga dentro, así que el overlay no
          se posicionaba respecto a la pantalla sino respecto a la barra: aparecía pegado arriba
          y recortado, no centrado. El portal lo saca de ahí y también de su stacking context.
          Dentro ya sí: el overlay centra y scrollea. `margin:auto` en un contenedor con
          `overflow-y:auto` centra cuando sobra sitio y deja subir cuando falta, cosa que el
          `top:50% + translate(-50%,-50%)` de antes no hacía: con el contenido más alto que la
          pantalla, su mitad superior quedaba por encima de top:0 e inalcanzable. */}
      {createPortal(
        <div
          onClick={onClose}
          style={{
            position: 'fixed', inset: 0, zIndex: Z.overlay, background: 'rgba(0,0,0,0.65)',
          display: 'flex', overflowY: 'auto', padding: 16, overscrollBehavior: 'contain',
        }}
      >
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            margin: 'auto',
            background: COLORS.panel, border: `1px solid ${COLORS.border}`, borderRadius: 18,
            padding: '26px 26px 22px', width: 'min(420px, 100%)', boxShadow: SHADOW.panel,
            display: 'flex', flexDirection: 'column', gap: 18,
          }}
        >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontFamily: FONTS.display, fontWeight: 800, fontSize: 18, color: COLORS.text, letterSpacing: '-0.01em' }}>
            Withdraw {token === 'cards' ? 'CARDS' : 'USDC'}
          </span>
          <button onClick={onClose} aria-label="Close"
            style={{ background: 'transparent', border: `1px solid ${COLORS.border}`, color: COLORS.muted, borderRadius: 8, width: 30, height: 30, cursor: 'pointer', fontSize: 15, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: FONTS.body }}>
            ✕
          </button>
        </div>

        {/* Token selector */}
        <div style={{ display: 'flex', gap: 8 }}>
          {(['usdc', 'cards'] as const).map((t) => (
            <button
              key={t}
              onClick={() => selectToken(t)}
              aria-label={`Select ${t.toUpperCase()}`}
              aria-pressed={token === t}
              style={{
                flex: 1, background: token === t ? GRADIENT : 'transparent',
                border: `1px solid ${token === t ? 'transparent' : COLORS.border}`, borderRadius: 9,
                padding: '8px 0', color: token === t ? '#06120c' : COLORS.muted,
                fontWeight: 800, fontSize: 12.5, fontFamily: FONTS.display, letterSpacing: '0.02em',
                cursor: 'pointer', transition: reducedMotion ? 'none' : 'background 0.15s, color 0.15s',
              }}
            >
              {t.toUpperCase()}
            </button>
          ))}
        </div>

        {/* Available */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#11161f', border: `1px solid ${COLORS.border}`, borderRadius: 11, padding: '10px 14px' }}>
          <span style={labelStyle}>AVAILABLE</span>
          <span style={{ fontFamily: FONTS.display, fontWeight: 800, fontSize: 16, color: COLORS.text }}>
            {available != null ? formatAmount(available) : '—'}
          </span>
        </div>

        {/* Destination wallet */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={labelStyle}>DESTINATION WALLET</span>
          <input
            value={dest}
            onChange={(e) => { setDest(e.target.value); setError(null) }}
            placeholder="Solana wallet address"
            spellCheck={false}
            style={{ ...inputStyle, fontFamily: FONTS.mono, fontSize: 13 }}
          />
        </div>

        {/* Amount */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={labelStyle}>AMOUNT ({token === 'cards' ? 'CARDS' : 'USDC'})</span>
          <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
            <input
              value={amount}
              onChange={(e) => { setAmount(e.target.value.replace(/[^0-9.]/g, '')); setError(null) }}
              inputMode="decimal"
              placeholder="0.00"
              style={{ ...inputStyle, paddingRight: 64 }}
            />
            <button
              onClick={() => { if (available != null) { setAmount(String(available)); setError(null) } }}
              disabled={available == null}
              style={{ position: 'absolute', right: 8, background: 'transparent', border: `1px solid ${COLORS.border}`, color: COLORS.green, borderRadius: 7, padding: '5px 9px', fontSize: 11, fontWeight: 700, fontFamily: FONTS.body, cursor: available == null ? 'default' : 'pointer' }}
            >
              MAX
            </button>
          </div>
        </div>

        {error && <div style={{ fontSize: 12.5, color: COLORS.red }}>{error}</div>}

        <button
          onClick={submit}
          disabled={!canSubmit}
          style={{
            background: canSubmit ? GRADIENT : '#1a2230', border: 'none', borderRadius: 10, padding: '12px 0',
            color: canSubmit ? '#06120c' : COLORS.muted, fontWeight: 800, fontSize: 14, fontFamily: FONTS.display,
            cursor: !canSubmit ? 'default' : 'pointer', width: '100%', letterSpacing: '0.01em',
            transition: reducedMotion ? 'none' : 'background 0.15s',
          }}
        >
          {busy ? 'Withdrawing…' : 'Withdraw'}
        </button>
        </div>
        </div>,
        document.body,
      )}
      <DelegationGate gate={gate} />
    </>
  )
}
