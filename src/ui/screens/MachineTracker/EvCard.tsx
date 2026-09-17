import { useState } from 'react'
import { Link } from 'react-router-dom'
import { COLORS, FONTS, formatUsd } from '../../theme'
import type { EvRow } from '../../../onchain/gachaClient'
import { RATIO_MAX, RATIO_MIN, anguloAguja, estadoDe, etiqueta, ratioDesdeEdge } from './evDial'
import { ACENTO, acentoDe, afirma, colorRareza, fondoFila } from './evAcento'

/**
 * Una máquina del gacha medida sobre el feed público de Collector Crypt.
 *
 * El dial marca el ratio MEDIDO: cuánto devuelve el sobre por cada dólar que cuesta, con 1.00
 * justo arriba. Se eligió por ser comparable de un vistazo entre tarjetas sin leer una cifra.
 *
 * La marca blanca del arco es el ratio del MODELO, calculado sobre el pool de cartas y las odds
 * que publica CC. La distancia entre marca y aguja es la pregunta que da sentido a la tarjeta:
 * "¿se está comportando como debería?".
 *
 * Regla que manda sobre el color: si el veredicto no se sostiene —ventana a medias, hueco dentro,
 * muestra corta— el número se pinta en gris aunque sea malísimo. Un rojo fuerte sobre seis horas de
 * datos afirma algo que los datos no dicen.
 */
export function EvCard({ fila, nota, onArrastrar }: {
  fila: EvRow
  nota?: string
  /** Fires when the card starts being dragged. Without it the card cannot be repositioned. */
  onArrastrar?: () => void
}) {
  const [arrastrable, setArrastrable] = useState(false)
  const estado = estadoDe(fila.realized_verdict)
  const ratio = ratioDesdeEdge(fila.realized_edge_pct)
  const lab = etiqueta(estado, fila)

  // UN solo acento por tarjeta, y de él sale todo lo demás. Ver `evAcento`: solo un veredicto
  // confirmado se lleva verde o rosa; lo que no se puede concluir va en ámbar.
  const acento = acentoDe(estado)
  const confirmado = afirma(estado)
  const edge = fila.realized_edge_pct

  return (
    <article style={{
      // El borde y el fondo también llevan el acento, muy desvaído: una tarjeta confirmada tiene
      // que destacar en la rejilla sin tener que leerla.
      background: confirmado
        ? `linear-gradient(170deg,${acento}0d,transparent 42%),${COLORS.panel}`
        : COLORS.panel,
      border: `1px solid ${confirmado ? `${acento}59` : COLORS.border}`,
      borderRadius: 16, boxShadow: '0 8px 24px #00000055',
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}
      // Draggable ONLY while the cursor is over the handle. If the article were always
      // `draggable`, selecting a number on the card would start a drag.
      draggable={arrastrable}
      onDragStart={onArrastrar}
      onDragEnd={() => setArrastrable(false)}
    >
      <header style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
        padding: '13px 15px', borderBottom: '1px solid #ffffff12',
        background: `linear-gradient(90deg,${acento}14,transparent 70%)`,
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }}>
          {/* El punto del acento: la señal más rápida de la tarjeta. Se ve antes que el título. */}
          <span aria-hidden style={{
            width: 9, height: 9, borderRadius: 3, background: acento,
            boxShadow: `0 0 10px ${acento}66`, flex: 'none',
          }} />
          <span style={{
            fontSize: 13.5, fontWeight: 700, letterSpacing: '-.01em',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>{fila.name}</span>
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 'none' }}>
        <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: COLORS.muted, whiteSpace: 'nowrap' }}>
          ${fila.pack_price}
          {fila.buyback_pct ? ` · bb ${Math.round(fila.buyback_pct * 100)}%` : ''}
        </span>
        {/* The handle. It only appears when the screen knows how to reorder, so the card still
            works in places where there is no order to touch.

            `onMouseDown` turns `draggable` on and `onDragEnd` turns it off: that is what makes
            the drag start HERE and not from any point of the card. The browser does not let you
            mark only a child as draggable. */}
        {onArrastrar && (
          <span
            aria-hidden
            title="Drag to reorder"
            onMouseDown={() => setArrastrable(true)}
            onMouseUp={() => setArrastrable(false)}
            style={{
              cursor: 'grab', color: '#5d6774', fontSize: 13, lineHeight: 1,
              padding: '2px 1px', userSelect: 'none',
            }}
          >
            ⠿
          </span>
        )}
        </span>
      </header>

      <div style={{ padding: '13px 15px', display: 'flex', alignItems: 'center', gap: 14 }}>
        <Dial ratio={ratio} tinta={acento} modelo={fila.model_ratio} />
        <div style={{ minWidth: 0 }}>
          {/* Etiquetado a propósito: sin la palabra, el número grande y el del modelo son dos
              cifras parecidas sin nada que diga cuál es la medida y cuál lo esperado. */}
          <div style={{ fontFamily: FONTS.mono, fontSize: 8, letterSpacing: '.18em', color: '#5d6774' }}>
            MEASURED
          </div>
          <div style={{
            fontFamily: FONTS.mono, fontSize: 25, fontWeight: 700, lineHeight: 1,
            color: ratio == null ? ACENTO.sinDatos : acento,
            // El halo solo cuando el color AFIRMA algo. En ámbar sería subrayar una duda.
            textShadow: confirmado ? `0 0 16px ${acento}66` : 'none',
            fontVariantNumeric: 'tabular-nums', marginTop: 2,
            display: 'flex', alignItems: 'baseline', gap: 8,
          }}>
            {ratio == null ? '—' : ratio.toFixed(3)}
            {/* La pastilla del edge solo con veredicto confirmado: es la conclusión en una cifra, y
                ponerla sobre un intervalo que cruza el cero la convertiría en una promesa. */}
            {confirmado && edge != null && (
              <span style={{
                fontFamily: FONTS.mono, fontSize: 10, fontWeight: 700, color: '#06080b',
                background: acento, borderRadius: 6, padding: '2px 7px',
              }}>
                {edge > 0 ? '+' : ''}{edge.toFixed(1)}%
              </span>
            )}
          </div>
          <div style={{ fontFamily: FONTS.mono, fontSize: 9.5, color: COLORS.muted, marginTop: 5, lineHeight: 1.55 }}>
            {fila.realized_edge_pct == null ? 'no measurement yet' : (
              <>
                edge {fila.realized_edge_pct > 0 ? '+' : ''}{fila.realized_edge_pct.toFixed(2)}%<br />
                {fila.realized_ci_lo_pct != null && (
                  <>95% CI {fila.realized_ci_lo_pct.toFixed(2)} … {fila.realized_ci_hi_pct?.toFixed(2)}</>
                )}
              </>
            )}
            {/* Lo esperado, del pool de cartas. Va debajo y en la misma línea visual que lo medido
                porque la tarjeta entera existe para poder comparar los dos: "debería pagar 1.080 y
                está pagando 0.938" dice mucho más que cualquiera de los dos números por separado. */}
            {fila.model_ratio != null && (
              <>
                <br />
                {/* El punto es el MISMO glifo que la marca del dial: es lo que ata el número a su
                    marca sin gastar una leyenda aparte, que en 112 px no cabe. */}
                <span style={{ color: '#8b95a3' }}>
                  <span aria-hidden style={{ color: '#ffffffdd', fontSize: 9 }}>●</span>{' '}
                  model {fila.model_ratio.toFixed(3)}
                  {fila.model_ev != null && ` · $${fila.model_ev.toFixed(2)}`}
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Rachas por rareza. Se enseñan SIEMPRE, también sin veredicto: se miden sobre las tiradas
          observadas y no dependen de que la ventana esté completa.

          Dice "cold", nunca "due". El gacha usa VRF y cada tirada es independiente: una rareza que
          lleva 60 sin salir tiene la misma probabilidad en la 61. Presentarlo como que le toca
          empujaría a la gente a perseguirlo, que es justo lo contrario de lo que hace esta página. */}
      {fila.tiers?.length > 0 && (
        <div style={{ padding: '0 15px 11px' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: FONTS.mono, fontSize: 10 }}>
              <thead>
                <tr>
                  {/* Izquierda lo ESPERADO (del pool de cartas), derecha lo OBSERVADO (del feed).
                      Separadas para que no se lean como una sola cosa: P y VALUE son lo que CC
                      declara, GAP y AGO son lo que ha pasado de verdad. */}
                  {['TIER', 'P', 'VALUE', 'GROSS', 'GAP', 'AVG'].map((h, i) => (
                    <th key={h} style={{
                      textAlign: i === 0 ? 'left' : 'right', fontWeight: 400, fontSize: 8.5,
                      letterSpacing: '.1em', color: '#5d6774', padding: '0 0 4px',
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {fila.tiers.map((t) => (
                  <tr key={t.tier} style={{ background: fondoFila(t.tier) }}>
                    <td style={{
                      color: colorRareza(t.tier), fontWeight: 700,
                      padding: '4px 6px', borderRadius: '7px 0 0 7px',
                    }}>{t.tier}</td>
                    {/* Un guion mientras no se haya barrido el pool de esa máquina. Nunca un 0:
                        diría que esa rareza no sale nunca o que no vale nada. */}
                    <td style={{ textAlign: 'right', color: COLORS.muted, fontVariantNumeric: 'tabular-nums' }}>
                      {t.probability == null ? '—' : `${(t.probability * 100).toFixed(t.probability < 0.01 ? 1 : 0)}%`}
                    </td>
                    <td style={{ textAlign: 'right', color: COLORS.muted, fontVariantNumeric: 'tabular-nums' }}>
                      {t.value == null ? '—' : formatUsd(t.value)}
                    </td>
                    <td style={{ textAlign: 'right', color: COLORS.text, fontVariantNumeric: 'tabular-nums' }}>
                      {t.gross == null ? '—' : formatUsd(t.gross)}
                    </td>
                    <td style={{
                      textAlign: 'right', fontVariantNumeric: 'tabular-nums',
                      color: t.cold ? '#f5c542' : COLORS.text,
                    }}>{t.current == null ? `${t.sample}+` : t.current}</td>
                    <td style={{
                      textAlign: 'right', color: colorRareza(t.tier), fontVariantNumeric: 'tabular-nums',
                      padding: '4px 6px', borderRadius: '0 7px 7px 0',
                    }}>
                      {t.average == null ? '—' : t.average}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* El pie va en su propia banda, más oscura: separa la conclusión de los datos que la
          sostienen, y así el veredicto no se lee como una fila más de la tabla. */}
      <div style={{ marginTop: 'auto', padding: '11px 15px 13px', borderTop: '1px solid #ffffff12',
                    background: 'rgba(0,0,0,.25)' }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
        }}>
          <span style={{ fontFamily: FONTS.mono, fontSize: 8.5, letterSpacing: '.14em', color: '#5d6774' }}>
            {fila.realized_window_hours}H · N={fila.realized_n_pulls.toLocaleString('en-US')}
            {/* Sin esto el mismo 0.938 significaría dos cosas distintas según el interruptor, y
                nadie sabría cuál está mirando. */}
            {nota ? ` · ${nota}` : ''}
          </span>
          {/* En pastilla y con el acento: es la conclusión de la tarjeta y tiene que poder leerse
              sin recorrer nada más. */}
          <span style={{
            fontFamily: FONTS.mono, fontSize: 9, fontWeight: 700, letterSpacing: '.08em',
            color: acento, border: `1px solid ${acento}59`, borderRadius: 99,
            padding: '3px 9px', whiteSpace: 'nowrap',
          }}>
            {lab.texto}
          </span>
        </div>
        {/* El detalle no es decoración: es lo único que distingue "no sé" de "no sé TODAVÍA", y sin
            él las tres formas de no concluir se leen igual. */}
        {lab.detalle && (
          <div style={{ fontFamily: FONTS.mono, fontSize: 9, color: COLORS.muted, marginTop: 6, lineHeight: 1.5 }}>
            {lab.detalle}
          </div>
        )}
        {/* From the conclusion to the pulls behind it. The card states the 48 hour summary; this
            is what sits underneath, and it is the only way to check it. It leads to Winners with
            the machine already filtered, which also stopped being in the side menu. */}
        <Link
          to={`/winners?machine=${encodeURIComponent(fila.machine)}`}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 11,
            fontFamily: FONTS.mono, fontSize: 9.5, letterSpacing: '.06em', textDecoration: 'none',
            color: COLORS.muted, border: `1px solid ${COLORS.border}`, borderRadius: 7,
            padding: '4px 9px',
          }}
        >
          RECENT PULLS →
        </Link>
      </div>
    </article>
  )
}


/** Un punto del arco, en coordenadas del SVG. `r` es la distancia desde el eje de la aguja. */
function punto(ratio: number, r: number): [number, number] {
  const rad = (anguloAguja(ratio) - 90) * (Math.PI / 180)
  return [56 + r * Math.cos(rad), 60 + r * Math.sin(rad)]
}

/**
 * El arco, con la aguja de lo MEDIDO y la marca de lo ESPERADO.
 *
 * Sin ratio no se dibuja aguja: una aguja en el centro se leería como "paga justo".
 *
 * La marca del modelo es lo que convierte el dial en una comparación en vez de un número suelto.
 * La distancia entre marca y aguja responde a la única pregunta que importa aquí: ¿se está
 * comportando como debería? Va en blanco y en trazo fino a propósito, para que se lea como
 * referencia y no compita con la aguja, que es lo que de verdad hemos medido.
 */
function Dial({ ratio, tinta, modelo }: { ratio: number | null; tinta: string; modelo?: number | null }) {
  const ang = ratio == null ? null : anguloAguja(ratio)
  const [x, y] = ratio == null ? [56, 60] : punto(ratio, 32)
  // r=40: JUSTO POR DENTRO de la banda (que va de 43.5 a 52.5). Comprobado en todo el rango
  // 0.75–1.25: así el punto nunca toca la etiqueta del 1.00, y queda a 8 px de la punta de la
  // aguja, que solo coinciden cuando lo medido y lo esperado son lo mismo — y entonces se ven
  // alineados, que es exactamente lo que ha pasado.
  const [mx, my] = modelo == null ? [0, 0] : punto(modelo, 40)
  return (
    <svg width="112" height="70" viewBox="0 0 112 70" style={{ flex: 'none' }} aria-hidden="true">
      <path d="M8 60 A48 48 0 0 1 104 60" fill="none" stroke="#ffffff14" strokeWidth="9" strokeLinecap="round" />
      {/* Tres formas distintas y ni una repetida, que era el fallo: escala en TEXTO, modelo en
          PUNTO sobre el arco, medida en AGUJA desde el centro.
          Antes el modelo se dibujaba como una rayita idéntica a la marca de escala del 1.00, así
          que con un modelo cerca de 1.00 —el caso normal— las dos se solapaban y parecían dos
          agujas. La rayita se ha quitado: la etiqueta ya dice dónde está el 1.00, y el vértice del
          arco es ese punto. */}
      <text x="56" y="10" textAnchor="middle" fill="#5d6774" fontFamily="monospace" fontSize="7">1.00</text>
      <text x="8" y="69" textAnchor="start" fill="#5d6774" fontFamily="monospace" fontSize="6.5">{RATIO_MIN}</text>
      <text x="104" y="69" textAnchor="end" fill="#5d6774" fontFamily="monospace" fontSize="6.5">{RATIO_MAX}</text>
      {modelo != null && (
        <>
          {/* El halo oscuro lo separa de la banda: sin él, un punto blanco sobre gris claro se
              pierde justo en la zona donde más importa, alrededor de 1.00. */}
          <circle cx={mx} cy={my} r="4.4" fill={COLORS.panel} />
          <circle cx={mx} cy={my} r="2.8" fill="#ffffffdd" />
        </>
      )}
      {ang != null && (
        <>
          <line x1="56" y1="60" x2={x} y2={y} stroke={tinta} strokeWidth="2.5" strokeLinecap="round" />
          <circle cx="56" cy="60" r="3.5" fill={tinta} />
        </>
      )}
    </svg>
  )
}
