import { useState } from 'react'
import { COLORS, FONTS } from '../../theme'
import { buyTrackerPass } from '../../../onchain/gachaClient'

const DURACIONES = [7, 30] as const

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
              onClick={() => comprar(dias)}
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
    </div>
  )
}
