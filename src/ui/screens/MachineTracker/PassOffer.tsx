import { useState } from 'react'
import { COLORS, FONTS } from '../../theme'
import { buyTrackerPass } from '../../../onchain/gachaClient'

const DURACIONES = [7, 30] as const

/** Qué decirle al jugador según cómo falló el cobro. Los distintos códigos piden reacciones
 *  OPUESTAS: sin saldo hay que depositar; si el cobro falló, el dinero está y pedirle depositar
 *  sería inútil además de insultante. Y dentro del propio 409 hay dos motivos distintos: uno se
 *  arregla esperando unos segundos, el otro necesita a una persona mirando. */
function mensajeDeError(e: unknown): string {
  const status = (e as { status?: number } | undefined)?.status
  const detalle = (e as { message?: string } | undefined)?.message

  switch (status) {
    case 402:
      return 'Not enough USDC. Deposit and try again.'
    case 502:
      // Tiene el dinero: pedirle depositar sería insultante y no arreglaría nada.
      return "We couldn't take the payment. Try again."
    case 409:
      // `tracker_pass_pending_stuck` es una compra vieja que no se resolvió sola: seguir
      // reintentando no va a cambiar nada, hace falta que alguien la mire a mano.
      return detalle === 'tracker_pass_pending_stuck'
        ? 'A previous purchase is stuck. Contact support.'
        : 'A purchase is already in progress. Wait a few seconds and try again.'
    case 429:
      return 'Too many attempts. Wait a moment and try again.'
    default:
      // Cubre el 503 (compra apagada o mal configurada) y cualquier otra cosa. Con `pass_prices`
      // vacío este bloque ni se renderiza, así que un 503 aquí solo puede ser una carrera justo
      // cuando se apaga: no merece un mensaje propio, con uno genérico basta.
      return "Purchases aren't available right now. Try again later."
  }
}

/**
 * Comprar el pase, dentro de la puerta.
 *
 * Va aquí y no en un ajuste aparte porque este es el momento en que alguien siente que le falta
 * la herramienta: acaba de leer cuánto le queda para entrar jugando.
 *
 * SIN PRECIOS NO SE RENDERIZA NADA. Cero significa que la compra no existe todavía, no que esté
 * rota, así que un botón deshabilitado prometería algo que no hay.
 */
export function PassOffer({ prices, token, onComprado }: {
  prices: Record<string, number>
  token: string | null
  onComprado: () => void
}) {
  const [cobrando, setCobrando] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  // `prices?.` y no `prices.`: algún llamador antiguo (de antes de que `TrackerAccess` trajera
  // `pass_prices`) puede seguir pasando un acceso sin ese campo, y sin precios es exactamente el
  // caso de "no ofrecer nada" que ya contempla el filtro de abajo.
  const disponibles = DURACIONES.filter((d) => (prices?.[String(d)] ?? 0) > 0)
  if (disponibles.length === 0 || !token) return null

  async function comprar(dias: 7 | 30) {
    if (cobrando !== null) return          // un doble clic sobre un cobro son dos cobros
    setCobrando(dias)
    setError(null)
    try {
      await buyTrackerPass(dias, token!)
      onComprado()
    } catch (e) {
      setError(mensajeDeError(e))
    } finally {
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
              disabled={cobrando !== null}
              style={{
                flex: '1 1 140px', minHeight: 58, borderRadius: 12, cursor: 'pointer',
                border: `1px solid ${COLORS.border}`, background: '#ffffff0a',
                color: COLORS.text, fontFamily: FONTS.display, fontWeight: 700,
                display: 'flex', flexDirection: 'column', gap: 3, padding: '9px 12px',
                opacity: cobrando !== null && cobrando !== dias ? 0.5 : 1,
              }}
            >
              <span style={{ fontSize: 14 }}>
                {cobrando === dias ? 'Paying…' : `${dias} days · $${precio}`}
              </span>
              {/* El precio por día es lo ÚNICO que deja comparar 7 con 30 de un vistazo. */}
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

      {/* Visible y ANTES de pagar, no en letra pequeña: si compras 30 días y mañana apuestas
          100 USDC, has pagado por algo que ya tenías. */}
      <div style={{ marginTop: 9, fontFamily: FONTS.mono, fontSize: 9.5, color: COLORS.muted }}>
        No refunds. Wagering still unlocks it for free.
      </div>
    </div>
  )
}
