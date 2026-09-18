import { useEffect, useState } from 'react'
import { COLORS, FONTS } from '../../theme'
import { buyTrackerPass } from '../../../onchain/gachaClient'
import { useUsdcBalance } from '../../../wallet/useUsdcBalance'

const DURACIONES = [7, 30] as const
const DIA_MS = 86_400_000

/** What to tell the player depending on how the charge failed. The different codes call for
 *  OPPOSITE reactions: with no balance they need to deposit; if the charge failed, the money is
 *  there and asking them to deposit would be useless as well as insulting. And within the 409
 *  itself there are two different reasons: one is fixed by waiting a few seconds, the other needs
 *  a person looking at it. */
function mensajeDeError(e: unknown): string {
  const status = (e as { status?: number } | undefined)?.status
  const detalle = (e as { message?: string } | undefined)?.message

  switch (status) {
    case 402:
      return 'Not enough USDC. Deposit and try again.'
    case 502:
      // They have the money: asking them to deposit would be insulting and would fix nothing.
      return "We couldn't take the payment. Try again."
    case 409:
      // `tracker_pass_pending_stuck` is an old purchase that did not resolve on its own: retrying
      // will not change anything, it needs someone to look at it by hand.
      return detalle === 'tracker_pass_pending_stuck'
        ? 'A previous purchase is stuck. Contact support.'
        : 'A purchase is already in progress. Wait a few seconds and try again.'
    case 429:
      return 'Too many attempts. Wait a moment and try again.'
    default:
      // Covers the 503 (purchase disabled or misconfigured) and anything else. With `pass_prices`
      // empty this block does not even render, so a 503 here can only be a race right when it
      // gets disabled: it does not deserve its own message, a generic one is enough.
      return "Purchases aren't available right now. Try again later."
  }
}

/**
 * Buy the pass, inside the gate.
 *
 * It goes here and not in a separate settings screen because this is the moment when someone
 * feels they are missing the tool: they just read how much they have left before they can play.
 *
 * WITH NO PRICES, NOTHING RENDERS. Zero means the purchase does not exist yet, not that it is
 * broken, so a disabled button would promise something that is not there.
 */
export function PassOffer({ prices, token, onComprado }: {
  prices: Record<string, number>
  token: string | null
  onComprado: () => void
}) {
  // Which duration is waiting for confirmation. Money used to leave the wallet on a single
  // click, with nothing in between and no way back: the duration button now only ASKS, and the
  // charge needs a second, deliberate act.
  const [confirmando, setConfirmando] = useState<7 | 30 | null>(null)
  const [cobrando, setCobrando] = useState<number | null>(null)
  // Once charged successfully, the whole block stays disabled FOREVER, without depending on
  // `onComprado()` (async, not awaited) finishing. Before, the `finally` re-enabled the buttons
  // as soon as `buyTrackerPass` resolved, and during `onComprado()`'s trip the gate stayed
  // mounted with the buttons alive: someone who saw no reaction clicked again and bought a
  // second pass. The backend does not stop it, because the first one is already `active`, not
  // `pending`.
  const [comprado, setComprado] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // `prices?.` and not `prices.`: some old caller (from before `TrackerAccess` carried
  // `pass_prices`) may still be passing an access without that field, and with no prices that is
  // exactly the "offer nothing" case already covered by the filter below.
  const disponibles = DURACIONES.filter((d) => (prices?.[String(d)] ?? 0) > 0)
  if (disponibles.length === 0 || !token) return null

  async function comprar(dias: 7 | 30) {
    if (cobrando !== null || comprado) return   // a double click on a charge is two charges
    setCobrando(dias)
    setError(null)
    try {
      await buyTrackerPass(dias, token!)
      // There is no `finally` that re-enables the buttons: success leaves `cobrando` set and
      // `comprado` permanently true, so there is no window left between this point and the
      // parent unmounting the gate (which is what `onComprado()` does, not awaited here).
      setComprado(true)
      onComprado()
    } catch (e) {
      setError(mensajeDeError(e))
      setCobrando(null)
    }
  }

  return (
    <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid #ffffff14' }}>
      <div style={{
        fontFamily: FONTS.mono, fontSize: 9, letterSpacing: '.22em', color: COLORS.muted,
        marginBottom: 10,
      }}>
        OR UNLOCK NOW
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {disponibles.map((dias) => {
          const precio = prices[String(dias)]
          return (
            <button
              key={dias}
              type="button"
              onClick={() => { setError(null); setConfirmando(dias) }}
              disabled={cobrando !== null || comprado}
              style={{
                flex: '1 1 140px', minHeight: 58, borderRadius: 12, cursor: 'pointer',
                border: `1px solid ${COLORS.border}`, background: '#ffffff0a',
                color: COLORS.text, fontFamily: FONTS.display, fontWeight: 700,
                display: 'flex', flexDirection: 'column', gap: 3, padding: '9px 12px',
                opacity: (cobrando !== null || comprado) && cobrando !== dias ? 0.5 : 1,
              }}
            >
              <span style={{ fontSize: 14 }}>
                {cobrando === dias ? 'Paying…' : `${dias} days · $${precio}`}
              </span>
              {/* The price per day is the ONLY thing that lets you compare 7 with 30 at a glance. */}
              <span style={{ fontFamily: FONTS.mono, fontSize: 9.5, color: COLORS.muted }}>
                ${(precio / dias).toFixed(2)} per day
              </span>
            </button>
          )
        })}
      </div>

      {error && (
        <div style={{ marginTop: 9, fontFamily: FONTS.mono, fontSize: 10, color: '#ff6ba4' }}>
          {error}
        </div>
      )}

      {/* Visible and BEFORE paying, not in fine print: if you buy 30 days and tomorrow wager
          100 USDC, you paid for something you already had. */}
      <div style={{ marginTop: 9, fontFamily: FONTS.mono, fontSize: 9.5, color: COLORS.muted }}>
        No refunds. Wagering still unlocks it for free.
      </div>

      {confirmando !== null && (
        <Confirmacion
          dias={confirmando}
          precio={prices[String(confirmando)]}
          cobrando={cobrando !== null}
          onCancelar={() => setConfirmando(null)}
          onConfirmar={() => comprar(confirmando)}
        />
      )}
    </div>
  )
}

/**
 * The step between picking a duration and paying for it.
 *
 * It exists because the charge is irreversible and instant: USDC leaves the player's embedded
 * wallet the moment they confirm, and there is no refund path. Everything needed to decide is
 * in here, so nobody has to leave the dialog to check: what is bought, what it costs, what the
 * balance is, and the end date, which is the one thing that cannot be worked out in your head.
 *
 * While the charge is in flight the dialog does NOT close, not with Escape and not by clicking
 * outside. Closing it mid-charge would leave the player without the one screen that can tell
 * them whether they paid.
 */
function Confirmacion({ dias, precio, cobrando, onCancelar, onConfirmar }: {
  dias: 7 | 30
  precio: number
  cobrando: boolean
  onCancelar: () => void
  onConfirmar: () => void
}) {
  const { usdc } = useUsdcBalance()
  // `usdc` is null while it is not known (no session, or a failed read). Unknown is NOT treated
  // as "not enough": the backend is the one that decides with a 402, and blocking the button over
  // a balance that could not be read would be inventing a reason to refuse.
  const sinSaldo = usdc !== null && usdc < precio
  const hasta = new Date(Date.now() + dias * DIA_MS)

  useEffect(() => {
    function alPulsar(e: KeyboardEvent) {
      if (e.key === 'Escape' && !cobrando) onCancelar()
    }
    document.addEventListener('keydown', alPulsar)
    return () => document.removeEventListener('keydown', alPulsar)
  }, [cobrando, onCancelar])

  return (
    <div
      onClick={() => { if (!cobrando) onCancelar() }}
      style={{
        position: 'fixed', inset: 0, zIndex: 60, display: 'grid', placeItems: 'center',
        padding: 16, background: 'rgba(6,8,11,.72)', backdropFilter: 'blur(4px)',
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Confirm your ${dias} day pass`}
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 380, maxWidth: '100%', borderRadius: 16, padding: 20,
          background: COLORS.panel, border: `1px solid ${COLORS.border}`,
          boxShadow: '0 30px 80px rgba(0,0,0,.7)',
        }}
      >
        <div style={{ fontFamily: FONTS.mono, fontSize: 9, letterSpacing: '.22em', color: COLORS.muted }}>
          CONFIRM YOUR PASS
        </div>
        <h3 style={{ margin: '8px 0 0', fontFamily: FONTS.display, fontSize: 20, color: COLORS.text }}>
          {dias} days · ${precio}
        </h3>

        <dl style={{
          margin: '16px 0 0', display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '7px 12px',
          fontFamily: FONTS.mono, fontSize: 11, color: COLORS.muted,
        }}>
          <dt>Charged now</dt>
          <dd style={{ margin: 0, textAlign: 'right', color: COLORS.text }}>${precio} USDC</dd>
          <dt>Your balance</dt>
          <dd style={{ margin: 0, textAlign: 'right', color: sinSaldo ? '#ff6ba4' : COLORS.text }}>
            {usdc === null ? '—' : `$${usdc}`}
          </dd>
          <dt>Access until</dt>
          <dd style={{ margin: 0, textAlign: 'right', color: COLORS.text }}>
            {hasta.toLocaleDateString()}
          </dd>
        </dl>

        <p style={{ margin: '14px 0 0', fontFamily: FONTS.mono, fontSize: 9.5, lineHeight: 1.6, color: COLORS.muted }}>
          Paid from your wallet balance. No refunds, and wagering {' '}
          {/* Same warning as outside, because this is the last screen before the money moves. */}
          still unlocks it for free.
        </p>

        {sinSaldo && (
          <p style={{ margin: '10px 0 0', fontFamily: FONTS.mono, fontSize: 10, color: '#ff6ba4' }}>
            Not enough USDC. Deposit and come back.
          </p>
        )}

        <div style={{ display: 'flex', gap: 9, marginTop: 18 }}>
          <button
            type="button"
            onClick={onCancelar}
            disabled={cobrando}
            style={{
              flex: '0 0 auto', minHeight: 42, padding: '0 16px', borderRadius: 11,
              border: `1px solid ${COLORS.border}`, background: 'transparent', color: COLORS.muted,
              fontFamily: FONTS.display, fontWeight: 700, fontSize: 13,
              cursor: cobrando ? 'default' : 'pointer', opacity: cobrando ? 0.5 : 1,
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirmar}
            disabled={cobrando || sinSaldo}
            style={{
              flex: 1, minHeight: 42, borderRadius: 11, border: 'none',
              background: 'linear-gradient(135deg,#ff2e7e,#3ce8a8)', color: '#06120c',
              fontFamily: FONTS.display, fontWeight: 800, fontSize: 13.5,
              cursor: cobrando || sinSaldo ? 'default' : 'pointer',
              opacity: cobrando || sinSaldo ? 0.6 : 1,
            }}
          >
            {cobrando ? 'Paying…' : 'Confirm and pay'}
          </button>
        </div>
      </div>
    </div>
  )
}
