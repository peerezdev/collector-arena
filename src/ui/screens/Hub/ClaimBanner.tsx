import { Link } from 'react-router-dom'
import { COLORS, FONTS } from '../../theme'

/** El naranja de $CARDS, el mismo de la pantalla /claim y del claim oficial de Collector Crypt.
 *  El camino entero (banner -> pantalla) va del mismo color a propósito: es el token el que se
 *  presenta, no nosotros, y un salto de paleta a mitad de camino lo haría parecer otra cosa. */
const NARANJA = '#f97316'
import { config } from '../../../onchain/config'

/**
 * Aviso del airdrop de $CARDS, arriba del Lobby, con enlace a /claim.
 *
 * SE LE ENSEÑA A TODO EL MUNDO y no solo a quien tiene algo que reclamar, a propósito: saber si
 * una wallet es elegible cuesta una llamada al RPC, y ponerla en el Lobby la convertiría en una
 * llamada por CADA carga de la página. La pantalla de /claim ya sabe decirle a cada uno lo suyo,
 * así que el banner solo tiene que llevarle hasta ella.
 *
 * ES PERMANENTE, igual que RoyaleDemoNotice y por la misma razón. Lo puse descartable primero,
 * con el argumento de que a quien no tiene nada que reclamar se le convierte en ruido fijo. La
 * decisión fue la contraria: mientras la ronda siga abierta, quien tenga tokens sin reclamar
 * tiene que poder tropezarse con esto, y el que lo cierra un martes sin mirar es justo el que
 * los pierde. No hay botón de descartar ni se recuerda nada.
 *
 * En devnet no se pinta: el airdrop solo existe en mainnet y el enlace no llevaría a nada útil.
 */
export function ClaimBanner() {
  if (config.isDevnet) return null

  return (
    <section style={{
      position: 'relative', overflow: 'hidden', borderRadius: 14,
      border: `1px solid rgba(249,115,22,.3)`,
      background: `radial-gradient(420px 140px at 8% 0%,rgba(249,115,22,.14),transparent 65%),linear-gradient(160deg,#1a0f06,#0b0d13)`,
      padding: '13px clamp(14px,1.8vw,20px)',
      display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
    }}>
      <div style={{ flex: '1 1 340px', minWidth: 0 }}>
        <h2 style={{
          margin: 0, fontFamily: FONTS.display, fontWeight: 800,
          fontSize: 16, letterSpacing: '-.01em', lineHeight: 1.2, color: COLORS.text,
        }}>
          The $CARDS airdrop is live
        </h2>
        <p style={{ margin: '4px 0 0', fontSize: 13, lineHeight: 1.5, color: '#aab3bf' }}>
          If your wallet is on the list, you can claim it here. We cover the network fee.
        </p>
      </div>

      <Link
        to="/claim"
        style={{
          flex: 'none', display: 'inline-flex', alignItems: 'center', gap: 8,
          padding: '10px 18px', borderRadius: 11, minHeight: 44, boxSizing: 'border-box',
          textDecoration: 'none',
          fontFamily: FONTS.display, fontSize: 14, fontWeight: 800, color: '#fff',
          background: `linear-gradient(135deg,${NARANJA},#ea580c)`,
          boxShadow: `0 10px 26px -12px ${NARANJA}`,
        }}
      >
        Check my claim
      </Link>

    </section>
  )
}
