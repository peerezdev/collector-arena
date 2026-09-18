import { useCallback, useEffect, useRef, useState } from 'react'
import { useIdentityToken } from '@privy-io/react-auth'
import { COLORS, FONTS } from '../../theme'
import { fetchEvLive, fetchEvRows, fetchTrackerAccess, type EvRow, type TrackerAccess }
  from '../../../onchain/gachaClient'
import { EvCard } from './EvCard'
import { alternar, guardarOcultas, leerOcultas, visibles } from './hiddenMachines'
import { guardarOrden, leerOrden, materializar, mostrarAlFinal, mover, ordenar } from './ordenTracker'
import { enModo, guardarModo, leerModo, type Modo } from './evModo'
import { LENTO_MS, RAPIDO_MS, aplicarVivo } from './evVivo'
import { TrackerGate } from './TrackerGate'
import { TrackerHelp } from './TrackerHelp'
import { estaRancio, horaActualizacion } from './actualizado'

/**
 * El Machine Tracker: cuánto paga de verdad cada máquina del gacha.
 *
 * Vivía dentro de la página de Winners, encima del feed de ganadores. Se saca a su propia pantalla
 * porque ya no es un panel: tiene su propia ingesta, su propio barrido del pool de cartas y su
 * propia puerta de acceso, y compartir sitio con una lista de ganadores recientes hacía que lo
 * segundo pareciera una nota al pie de lo primero.
 *
 * DETRÁS DE UNA PUERTA. Hace falta llevar apostados 100 USDC en Pack Battle o Battle Royale en los
 * últimos 7 días. El gacha no cuenta, y eso no es un descuido: el tracker existe justo para decidir
 * si abrir sobres merece la pena, así que pedir gasto en gacha para poder verlo sería circular.
 * La regla completa y por qué la ventana es rodante, en `tracker_access` del backend.
 */
export function MachineTrackerPage() {
  const { identityToken } = useIdentityToken()
  const [acceso, setAcceso] = useState<TrackerAccess | null>(null)

  // Request counter: Privy's `identityToken` typically changes twice while loading (from null to
  // a real token), so two `fetchTrackerAccess` calls can be in flight at once. Without this, if
  // the one WITHOUT a token (which answers `allowed: false`) takes longer than the one WITH it,
  // its `.then` lands last and overwrites the good access with a closed gate. With the counter,
  // only the response of the MOST RECENT request can touch the state; any other is discarded.
  const ultimaPeticion = useRef(0)
  const [falloAcceso, setFalloAcceso] = useState(false)
  const pedirAcceso = useRef(() => {})
  pedirAcceso.current = () => {
    const id = ++ultimaPeticion.current
    fetchTrackerAccess(identityToken)
      .then((a) => {
        if (ultimaPeticion.current !== id) return
        setAcceso(a)
        setFalloAcceso(false)
      })
      // If it cannot be asked, it does NOT open: a gate that falls open on a network error is no
      // gate. But it invents nothing either. It used to install a hardcoded access here (wagered
      // 0, required 100, no prices), which told someone who had wagered 80 that they had wagered
      // nothing, and made the pass offer vanish along with the prices. Worst of all right after
      // paying: the refresh that follows the purchase fails, and whoever just paid gets the gate
      // back saying "$100 to go", with the buy buttons already disabled and no way out but a
      // reload. So the last known access is kept, and the failure is said out loud with a way to
      // retry.
      .catch(() => {
        if (ultimaPeticion.current !== id) return
        setFalloAcceso(true)
      })
  }

  useEffect(() => {
    pedirAcceso.current()
  }, [identityToken])

  // If the pass expires (or Collector Crypt stops seeing the wager) while the screen is already
  // open, `PanelEv` gets a 403 from `/gacha/ev*` and calls in here: access is asked for again,
  // and when it comes back `allowed: false` the gate appears on its own. `acceso` is not set to
  // `null` by hand, because that would flash "Measuring…" for an instant, as if this were the
  // first load.
  //
  // Stable identity (useCallback with `[]`, leaning on the ref above): if it changed on every
  // render of this screen, the effect behind `PanelEv`'s two lanes has it as a dependency and
  // would restart for no reason, losing the first tick of the poll.
  const onSinAcceso = useCallback(() => pedirAcceso.current(), [])

  return (
    <div style={{ padding: '24px clamp(14px,2.4vw,28px) 44px', display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h1 style={{ fontFamily: FONTS.display, fontSize: 26, fontWeight: 800, margin: 0 }}>Machine Tracker</h1>
        <p style={{ color: COLORS.muted, fontSize: 13.5, margin: '6px 0 0' }}>
          What every Collector Crypt machine is actually paying back, measured on the public feed.
        </p>
      </div>

      {/* El explicador va con acceso o sin él: quien todavía no puede entrar merece saber qué es
          lo que le estamos pidiendo que se gane. */}
      <TrackerHelp />

      {/* Access could not be checked. What sits below is still the last thing the server said, so
          it is left alone: the failure is stated and a retry is offered, which is what whoever
          just paid actually needs. */}
      {falloAcceso && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
          padding: '12px 14px', borderRadius: 12,
          border: '1px solid #ffd16659', background: '#ffd1661a',
        }}>
          <span style={{ fontSize: 13, color: COLORS.text }}>
            Couldn&apos;t check your access. If you just paid, your pass is already active: this
            only failed to refresh.
          </span>
          <button
            type="button"
            onClick={() => pedirAcceso.current()}
            style={{
              marginLeft: 'auto', minHeight: 34, padding: '0 14px', borderRadius: 9,
              border: '1px solid #ffffff26', background: '#ffffff12', cursor: 'pointer',
              fontFamily: FONTS.mono, fontSize: 11, letterSpacing: '.1em', color: COLORS.text,
            }}
          >
            TRY AGAIN
          </button>
        </div>
      )}

      {/* Mientras no se sabe, no se enseña ninguna de las dos cosas: enseñar el panel y quitarlo
          medio segundo después sería peor que esperar, y enseñar el aviso a quien sí tiene acceso
          es acusarle de algo que no es verdad. */}
      {acceso == null ? null : acceso.allowed
        ? <PanelEv token={identityToken} acceso={acceso} onSinAcceso={onSinAcceso} />
        : <TrackerGate acceso={acceso} token={identityToken} onComprado={onSinAcceso} />}
    </div>
  )
}

/**
 * Cuánto paga de verdad cada máquina, medido sobre el feed público de Collector Crypt.
 *
 * Las máquinas sin nada que decir todavía —ventana a medias, muestra corta— van AL FINAL y no se
 * esconden: que una máquina lleve seis horas midiéndose es información, y ocultarla haría pensar
 * que no existe.
 */
function PanelEv({ token, acceso, onSinAcceso }: {
  token: string | null
  acceso: TrackerAccess
  onSinAcceso: () => void
}) {
  const [filas, setFilas] = useState<EvRow[] | null>(null)
  const [fallo, setFallo] = useState(false)
  // Cuándo se calculó lo que se está viendo. Del carril LENTO: es de donde salen el edge y el
  // intervalo, que son los números grandes de la tarjeta.
  const [sello, setSello] = useState<number | null>(null)
  // Se refresca solo para poder decir "esto lleva rato parado" sin depender de que llegue nada.
  const [ahoraSeg, setAhoraSeg] = useState(() => Math.floor(Date.now() / 1000))
  useEffect(() => {
    const t = setInterval(() => setAhoraSeg(Math.floor(Date.now() / 1000)), 15_000)
    return () => clearInterval(t)
  }, [])
  // Se lee una vez al montar: la preferencia no cambia sola, y releerla en cada render obligaría a
  // tocar localStorage constantemente.
  const [ocultas, setOcultas] = useState<Set<string>>(() => leerOcultas())
  const [orden, setOrden] = useState<string[]>(() => leerOrden())
  // Which card is being dragged. In a ref and not in state: it changes on every `dragover`, and
  // repainting the whole grid at that rate makes the drag feel sticky.
  const arrastrando = useRef<string | null>(null)
  const [eligiendo, setEligiendo] = useState(false)
  const [modo, setModo] = useState<Modo>(() => leerModo())
  // Los sondeos se montan una sola vez y no pueden leer `filas` de su cierre, que se quedaría
  // congelado en el primer valor. El ref les da el actual sin volver a montar los intervalos.
  const filasRef = useRef<EvRow[] | null>(null)
  filasRef.current = filas

  // Dos carriles, porque la tarjeta mezcla dos cosas que se mueven a ritmos muy distintos: el
  // intervalo cuesta 4.000 remuestreos por máquina y no se mueve, las rachas cambian con cada
  // tirada y cuestan una consulta. Ver `evVivo`.
  useEffect(() => {
    let cancelado = false
    const dormido = () => typeof document !== 'undefined' && document.visibilityState === 'hidden'

    const lento = () => {
      if (dormido()) return
      fetchEvRows(undefined, token)
        .then((d) => { if (!cancelado) { setFilas(d.rows); setSello(d.updated_at); setFallo(false) } })
        .catch((e) => {
          if (cancelado) return
          // A 403 is not a failure: the pass expired (or the wager stopped counting) with the
          // screen open. Showing "Couldn't load the tracker" would say something broke when
          // nothing broke; the right move is to go back to the gate.
          if ((e as { status?: number })?.status === 403) { onSinAcceso(); return }
          // Only the FIRST load counts as failed: once there are cards on screen, a failed poll
          // must not wipe them, because what was there is still true, and emptying the screen
          // over a passing network error is worse than showing it a minute staler.
          setFallo((antes) => antes || filasRef.current == null)
        })
    }
    const rapido = () => {
      if (dormido() || filasRef.current == null) return
      fetchEvLive(token)
        .then((d) => { if (!cancelado) setFilas((f) => (f ? aplicarVivo(f, d.rows) : f)) })
        .catch((e) => {
          if (cancelado) return
          // Same treatment as the slow lane: a 403 is the gate, not a network error that can be
          // ignored like the rest of this lane's errors.
          if ((e as { status?: number })?.status === 403) { onSinAcceso(); return }
          /* the fast lane is a bonus: if it fails for any other reason, the slow lane still shows */
        })
    }

    lento()
    const a = setInterval(lento, LENTO_MS)
    const b = setInterval(rapido, RAPIDO_MS)
    // Al volver a la pestaña se pide ya, sin esperar al siguiente tic: si no, se vería un minuto de
    // datos viejos justo cuando alguien acaba de mirar.
    const despertar = () => { if (!dormido()) { lento(); rapido() } }
    document.addEventListener('visibilitychange', despertar)
    return () => {
      cancelado = true
      clearInterval(a); clearInterval(b)
      document.removeEventListener('visibilitychange', despertar)
    }
  }, [token, onSinAcceso])

  function cambiar(siguiente: Set<string>) {
    setOcultas(siguiente)
    guardarOcultas(siguiente)
  }

  /** Tick or untick ONE machine in the selector. Ticking it puts it last in the grid. */
  function alternarUna(code: string) {
    const seOculta = !ocultas.has(code)
    cambiar(alternar(ocultas, code))
    // Hiding does not touch the order: the code simply has no effect, because `ordenar` gets the
    // rows already filtered. Mutating the code proved that cleaning it up changes nothing.
    if (!seOculta) guardarYOrdenar(mostrarAlFinal(mostradas, orden, code))
  }

  function guardarYOrdenar(siguiente: string[]) {
    setOrden(siguiente)
    guardarOrden(siguiente)
  }

  /** Drop the card being dragged onto `destino`. */
  function soltarSobre(destino: string) {
    const code = arrastrando.current
    arrastrando.current = null
    if (!code || code === destino) return
    // The first drag freezes what is on screen: a card that was never touched has no position,
    // so without this there would be nothing to move.
    const base = materializar(mostradas, orden)
    guardarYOrdenar(mover(base, base.indexOf(code), base.indexOf(destino)))
  }

  // Estos tres casos devolvían `null` cuando esto era un PANEL dentro de la página de Winners: si
  // fallaba, el feed de ganadores seguía debajo y no se notaba. Ahora es la pantalla entera, así
  // que un `null` dejaría al jugador mirando un título y nada más, sin saber si está roto, si está
  // cargando o si no hay datos.
  if (fallo) {
    return (
      <Aviso>
        Couldn&apos;t load the tracker. It measures the live Collector Crypt feed, so this is
        usually temporary. Reload in a moment.
      </Aviso>
    )
  }
  if (filas == null) {
    return <div style={{ fontFamily: FONTS.mono, fontSize: 11, color: COLORS.muted }}>Measuring…</div>
  }
  if (filas.length === 0) {
    return <Aviso>No machines measured yet. The tracker needs 48 hours of feed before it can say
      anything about a machine.</Aviso>
  }

  // La conversión es una vista, no otra medición: el backend mide el valor de la carta y aquí se
  // le aplica la recompra si el usuario quiere ver lo que recuperaría vendiendo.
  const mostradas = ordenar(visibles(filas, ocultas), orden).map((f) => enModo(f, modo))
  const rancio = estaRancio(sello, ahoraSeg)

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontFamily: FONTS.mono, fontSize: 10, letterSpacing: '.2em', color: COLORS.muted }}>
          RETURN PER DOLLAR · LAST 48H
        </span>
        {/* La hora de la última medición, con aviso si se queda parada. Ya pasó una vez: la ingesta
            se quedó muda cinco horas aparentando estar bien, y esto lo habría dicho al minuto. */}
        <span style={{
          fontFamily: FONTS.mono, fontSize: 10, letterSpacing: '.1em',
          color: rancio ? '#f5c542' : '#5d6774',
        }}>
          {rancio ? 'STALE · ' : 'UPDATED '}{horaActualizacion(sello)}
        </span>
        {/* Only this route truly expires: whoever got in by wagering or on the house sees the
            gate close again on its own as soon as they stop meeting that condition, not on a
            fixed date. Whoever bought a pass does have a date, and it is the only one they can
            plan around. */}
        {acceso?.via === 'pass' && acceso.pass_until && (
          <EtiquetaPase hasta={acceso.pass_until} ahoraSeg={ahoraSeg} />
        )}
        {/* Las dos lecturas de la MISMA medición. Se ofrece elegir porque las dos son ciertas: el
            coleccionista se queda las cartas buenas y el que juega por valor las revende. Sin este
            interruptor habría que decidir por él y esconder la mitad de la verdad. */}
        <div style={{ display: 'flex', border: `1px solid ${COLORS.border}`, borderRadius: 8, overflow: 'hidden' }}>
          {([['cashout', 'if you sell back'], ['keep', 'if you keep it']] as const).map(([m, etiqueta]) => (
            <button
              key={m}
              type="button"
              aria-pressed={modo === m}
              onClick={() => { setModo(m); guardarModo(m) }}
              style={{
                fontFamily: FONTS.mono, fontSize: 9.5, cursor: 'pointer', border: 0,
                padding: '4px 10px',
                background: modo === m ? '#ffffff12' : 'transparent',
                color: modo === m ? COLORS.text : COLORS.muted,
              }}
            >
              {etiqueta}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setEligiendo((v) => !v)}
          aria-expanded={eligiendo}
          style={{
            fontFamily: FONTS.mono, fontSize: 10, color: COLORS.muted, cursor: 'pointer',
            background: 'transparent', border: `1px solid ${COLORS.border}`, borderRadius: 8,
            padding: '3px 9px',
          }}
        >
          {mostradas.length} of {filas.length} machines {eligiendo ? '▴' : '▾'}
        </button>
      </div>

      {eligiendo && (
        <div style={{
          border: `1px solid ${COLORS.border}`, borderRadius: 12, background: COLORS.panel,
          padding: 12, display: 'flex', flexDirection: 'column', gap: 10,
        }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" onClick={() => cambiar(new Set())} style={enlaceMini}>Show all</button>
            <button type="button" onClick={() => cambiar(new Set(filas.map((f) => f.machine)))} style={enlaceMini}>Hide all</button>
            {/* Without this, undoing an order you dislike means dragging everything back. */}
            {orden.length > 0 && (
              <button type="button" onClick={() => guardarYOrdenar([])} style={enlaceMini}>Reset order</button>
            )}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(170px,1fr))', gap: 4 }}>
            {filas.map((f) => {
              const vista = !ocultas.has(f.machine)
              return (
                <label key={f.machine} style={{
                  display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer',
                  fontFamily: FONTS.mono, fontSize: 10.5, padding: '3px 4px',
                  color: vista ? COLORS.text : COLORS.muted,
                }}>
                  <input
                    type="checkbox"
                    checked={vista}
                    onChange={() => alternarUna(f.machine)}
                    style={{ accentColor: COLORS.green, cursor: 'pointer' }}
                  />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {f.name}
                  </span>
                </label>
              )
            })}
          </div>
        </div>
      )}

      {/* Con todo oculto la rejilla quedaría vacía y parecería que la página está rota. */}
      {mostradas.length === 0 ? (
        <div style={{ fontFamily: FONTS.mono, fontSize: 11, color: COLORS.muted }}>
          All machines hidden. Open the selector above to bring some back.
        </div>
      ) : (
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(290px,1fr))', gap: 12,
        }}>
          {mostradas.map((f) => (
            <div
              key={f.machine}
              onDragOver={(e) => { if (arrastrando.current) e.preventDefault() }}
              onDrop={(e) => { e.preventDefault(); soltarSobre(f.machine) }}
            >
              <EvCard
                fila={f}
                nota={modo === 'cashout' && f.buyback_pct ? 'AT BUYBACK' : 'AT CARD VALUE'}
                onArrastrar={() => { arrastrando.current = f.machine }}
                onFinArrastre={() => { arrastrando.current = null }}
              />
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

const enlaceMini: React.CSSProperties = {
  fontFamily: FONTS.mono, fontSize: 10, letterSpacing: '.06em', color: COLORS.muted,
  background: 'transparent', border: `1px solid ${COLORS.border}`, borderRadius: 7,
  padding: '3px 9px', cursor: 'pointer',
}

/** Un estado vacío o de fallo con la misma caja que el resto. Existe porque esto pasó de panel a
 *  pantalla: antes se podía no decir nada, ahora no. */
function Aviso({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontFamily: FONTS.mono, fontSize: 12, lineHeight: 1.7, color: COLORS.muted,
      background: COLORS.panel, border: `1px solid ${COLORS.border}`, borderRadius: 14,
      padding: '18px 20px', maxWidth: 520,
    }}>
      {children}
    </div>
  )
}

/**
 * How much of the pass is left, in the tracker header.
 *
 * It leads with the time remaining and not with the date, because that is the question being
 * asked: a date on its own makes you count on your fingers to find out whether it is running out.
 * The date comes right after, which is what you plan around.
 *
 * Under a day it switches to hours: "0 days left" reads as expired when there is still an
 * afternoon of it left. It turns amber at 2 days, which is when the figure stops being decoration
 * and becomes a warning worth acting on.
 */
function EtiquetaPase({ hasta, ahoraSeg }: { hasta: number; ahoraSeg: number }) {
  const quedan = hasta - ahoraSeg
  const dias = Math.floor(quedan / 86_400)
  const horas = Math.max(0, Math.floor(quedan / 3600))
  const restante = dias >= 1 ? `${dias} day${dias === 1 ? '' : 's'} left`
                             : `${horas} hour${horas === 1 ? '' : 's'} left`
  const acabando = quedan <= 2 * 86_400
  const color = acabando ? '#ffd166' : COLORS.muted

  return (
    <span
      title="Machine Tracker pass"
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '3px 9px', borderRadius: 999,
        border: `1px solid ${acabando ? '#ffd16659' : COLORS.border}`,
        background: acabando ? '#ffd1661a' : '#ffffff08',
        fontFamily: FONTS.mono, fontSize: 9.5, letterSpacing: '.08em', color,
      }}
    >
      PASS · {restante}
      <span style={{ color: COLORS.muted }}>
        until {new Date(hasta * 1000).toLocaleDateString()}
      </span>
    </span>
  )
}
