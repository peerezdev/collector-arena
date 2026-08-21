import { Link } from 'react-router-dom'
import { COLORS, FONTS, formatUsd } from '../../theme'
import type { TrackerAccess } from '../../../onchain/gachaClient'
import { FANTASMAS } from './trackerFantasmas'
import { PassOffer } from './PassOffer'

const VERDE = '#3ce8a8'
const ROSA = '#ff2e7e'
const AMBAR = '#ffd166'

/**
 * La puerta del Machine Tracker: qué ve quien todavía no lleva los 100 USDC apostados.
 *
 * DETRÁS DEL CRISTAL NO HAY DATOS REALES. Ver `trackerFantasmas`: difuminar las tarjetas de verdad
 * sería una puerta de mentira, porque un `blur()` de CSS se quita desde el navegador y los números
 * viajarían igual en la respuesta. Se enseña la FORMA de lo que hay dentro, que es lo que hace
 * entender qué se está perdiendo, y ni se pide `/gacha/ev` mientras la puerta está puesta.
 *
 * Lo que dice, en este orden: cuánto falta, sobre cuánto, y por dónde seguir. El número que el
 * jugador necesita para decidir va primero; el motivo, al final.
 *
 * NO se disfraza de error. No se ha roto nada y el jugador no ha hecho nada mal: le falta jugar.
 */
export function TrackerGate({ acceso, token = null, onComprado = () => {} }: {
  acceso: TrackerAccess
  // Los dos con valor por defecto: los tests existentes de esta puerta la montan sin ellos, de
  // antes de que se pudiera comprar el pase, y no tienen por qué saber de la compra para seguir
  // probando lo que ya probaban. Sin token, `PassOffer` no se ofrece (hace falta para cobrar).
  token?: string | null
  onComprado?: () => void
}) {
  const hecho = acceso.required_usd > 0
    ? Math.min(1, acceso.wagered_usd / acceso.required_usd)
    : 1

  return (
    <div style={{ position: 'relative', minHeight: 560, borderRadius: 20, overflow: 'hidden' }}>
      {/* El fondo, inerte: sin eventos, sin selección y sin un solo dato medido. */}
      <div aria-hidden style={{
        filter: 'blur(7px) saturate(.55)', opacity: .55,
        pointerEvents: 'none', userSelect: 'none',
        padding: 24,
        display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(280px,1fr))', gap: 18,
      }}>
        {FANTASMAS.map((f) => <Fantasma key={f.name} f={f} />)}
      </div>

      {/* Velo, para que el fondo no compita con lo que hay que leer. */}
      <div aria-hidden style={{
        position: 'absolute', inset: 0,
        background: 'linear-gradient(to bottom,rgba(6,8,11,.25),rgba(6,8,11,.72) 75%)',
      }} />

      <div style={{
        position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
        justifyContent: 'center', padding: 'clamp(16px,3vw,40px)', overflowY: 'auto',
      }}>
        <div style={{
          width: 560, maxWidth: '100%', borderRadius: 22, padding: 'clamp(20px,3vw,30px)',
          border: '1px solid #ffffff1f', background: 'rgba(10,12,17,.92)',
          backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)',
          boxShadow: '0 40px 100px rgba(0,0,0,.7)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span aria-hidden style={{
              width: 38, height: 38, borderRadius: 11, flex: 'none',
              background: `${AMBAR}1a`, border: `1px solid ${AMBAR}59`,
              display: 'grid', placeItems: 'center', fontSize: 16,
            }}>🔒</span>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontFamily: FONTS.mono, fontSize: 9, letterSpacing: '.22em', color: AMBAR }}>
                MACHINE TRACKER
              </div>
              <h2 style={{ margin: '2px 0 0', fontSize: 24, fontWeight: 700, color: COLORS.text }}>
                {formatUsd(acceso.missing_usd)} to go
              </h2>
            </div>
          </div>

          <p style={{ margin: '14px 0 0', fontSize: 13.5, lineHeight: 1.55, color: '#aab3bf' }}>
            Wager <strong style={{ color: COLORS.text }}>{formatUsd(acceso.required_usd)}</strong> in
            Pack Battles or Battle Royale over the last {acceso.window_days} days to unlock live
            machine data. Gacha pulls don&apos;t count.
          </p>

          {/* La barra convierte "me faltan 40" en "voy por dos tercios", que es lo que hace seguir
              en vez de abandonar. Las marcas de cuarto dan la escala sin tener que leer cifras. */}
          <div style={{ marginTop: 18 }}>
            <div style={{
              display: 'flex', justifyContent: 'space-between', fontFamily: FONTS.mono,
              fontSize: 10, color: '#7d8794', marginBottom: 7, fontVariantNumeric: 'tabular-nums',
            }}>
              <span style={{ color: COLORS.text, fontWeight: 700 }}>
                {formatUsd(acceso.wagered_usd)} wagered
              </span>
              <span>{formatUsd(acceso.required_usd)}</span>
            </div>
            <div style={{
              position: 'relative', height: 12, borderRadius: 99,
              background: '#ffffff14', overflow: 'hidden',
            }}>
              <div style={{
                height: '100%', width: `${hecho * 100}%`, borderRadius: 99,
                background: `linear-gradient(90deg,${ROSA},${VERDE})`,
              }} />
              {[25, 50, 75].map((p) => (
                <span key={p} aria-hidden style={{
                  position: 'absolute', left: `${p}%`, top: 0, bottom: 0, width: 1,
                  background: 'rgba(6,8,11,.6)',
                }} />
              ))}
            </div>
            <div style={{
              display: 'flex', justifyContent: 'space-between', fontFamily: FONTS.mono,
              fontSize: 8.5, color: '#5c6673', marginTop: 5, padding: '0 1px',
            }}>
              <span />
              {[0.25, 0.5, 0.75].map((f) => (
                <span key={f}>{formatUsd(acceso.required_usd * f)}</span>
              ))}
              <span />
            </div>
          </div>

          {/* Por dónde seguir. No es adorno: sin esto el aviso dice "te faltan 40" y deja al
              jugador buscándose la vida para gastarlos.

              UN SOLO botón al Lobby, y no uno por modo. Los dos llevaban al mismo sitio, así que
              eran dos caminos para una decisión que se toma igual: mirar qué hay abierto y elegir.
              Ahí están las dos listas juntas y el filtro por modo. */}
          <Link
            to="/play/lobby"
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
              marginTop: 18, minHeight: 46, borderRadius: 12, textDecoration: 'none',
              fontFamily: FONTS.display, fontSize: 14.5, fontWeight: 800, color: '#06120c',
              background: `linear-gradient(135deg,${ROSA},${VERDE})`,
            }}
          >
            Find a match →
          </Link>

          {/* La compra va AQUÍ y no en un ajuste aparte: es el momento exacto en que alguien
              siente que le falta la herramienta, justo tras leer cuánto le queda para entrar
              jugando. Sin precios configurados no se pinta nada (ver `PassOffer`). */}
          <PassOffer prices={acceso.pass_prices} token={token} onComprado={onComprado} />

          <div style={{
            marginTop: 14, paddingTop: 14, borderTop: '1px solid #ffffff14',
            fontFamily: FONTS.mono, fontSize: 9.5, lineHeight: 1.6, color: '#5c6673',
          }}>
            Rolling {acceso.window_days}-day window: what you wager today counts for the next{' '}
            {acceso.window_days} days, and anything older drops off.
          </div>
        </div>
      </div>
    </div>
  )
}

/** Una tarjeta del fondo. Todo lo que enseña es inventado: ver `trackerFantasmas`. */
function Fantasma({ f }: { f: (typeof FANTASMAS)[number] }) {
  return (
    <div style={{
      borderRadius: 16, border: '1px solid #ffffff17', background: '#0c0f15', overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '13px 16px', borderBottom: '1px solid #ffffff12',
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <span style={{ width: 8, height: 8, borderRadius: 3, background: f.acento, flex: 'none' }} />
          <span style={{ fontSize: 13.5, fontWeight: 700, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {f.name}
          </span>
        </span>
        <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: '#aab3bf', flex: 'none' }}>{f.price}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: 16 }}>
        <svg viewBox="0 0 100 62" style={{ width: 92, height: 57, flex: 'none' }}>
          <path d="M 10 56 A 42 42 0 0 1 90 56" fill="none" stroke="#ffffff17" strokeWidth="7" strokeLinecap="round" />
          <path d="M 10 56 A 42 42 0 0 1 90 56" fill="none" stroke={f.acento} strokeWidth="7"
            strokeLinecap="round" strokeDasharray={`${f.dash} 200`} />
        </svg>
        <div>
          <div style={{ fontFamily: FONTS.mono, fontSize: 26, fontWeight: 700, color: f.acento }}>{f.value}</div>
          <div style={{ fontFamily: FONTS.mono, fontSize: 10, color: '#7d8794', marginTop: 3 }}>{f.sub}</div>
        </div>
      </div>
      {/* Barras en vez de una tabla: la forma de lo que hay dentro, sin inventar cifras que
          alguien pudiera tomarse en serio si un día el desenfoque falla. */}
      <div style={{ padding: '0 16px 14px', display: 'flex', flexDirection: 'column', gap: 4 }}>
        {[92, 84, 88, 78].map((w, i) => (
          <div key={w} style={{
            height: 11, borderRadius: 5, width: `${w}%`,
            background: `rgba(255,255,255,${0.07 - i * 0.01})`,
          }} />
        ))}
      </div>
    </div>
  )
}
