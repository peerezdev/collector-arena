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

  // Contador de peticiones: `identityToken` de Privy típicamente cambia dos veces al cargar (de
  // nulo a token real), así que puede haber dos `fetchTrackerAccess` en vuelo a la vez. Sin esto,
  // si la de SIN token (que responde `allowed: false`) tarda más que la de CON token, su `.then`
  // llega el último y pisa el acceso bueno con la puerta cerrada. Con el contador, solo la
  // respuesta de la petición MÁS RECIENTE puede tocar el estado; cualquier otra se descarta.
  const ultimaPeticion = useRef(0)
  const pedirAcceso = useRef(() => {})
  pedirAcceso.current = () => {
    const id = ++ultimaPeticion.current
    fetchTrackerAccess(identityToken)
      .then((a) => { if (ultimaPeticion.current === id) setAcceso(a) })
      // Si no se puede preguntar, NO se abre: una puerta que se cae abierta ante un fallo de red
      // no es una puerta. Se deja el aviso con lo que se sabe, que es nada.
      .catch(() => {
        if (ultimaPeticion.current !== id) return
        setAcceso({ allowed: false, wagered_usd: 0, required_usd: 100,
                    missing_usd: 100, window_days: 7, via: null, pass_until: null, pass_prices: {} })
      })
  }

  useEffect(() => {
    pedirAcceso.current()
  }, [identityToken])

  // Si el pase caduca (o CC deja de ver la apuesta) con la pantalla ya abierta, `PanelEv` recibe
  // un 403 de `/gacha/ev*` y llama aquí: se vuelve a pedir el acceso, que al llegar `allowed:
  // false` hace aparecer la puerta sola. No se pone `acceso` a `null` a mano porque eso enseñaría
  // el "Measuring…" un instante, como si fuera la primera carga.
  //
  // Identidad estable (useCallback con `[]`, apoyada en el ref de arriba): si cambiara en cada
  // render de esta pantalla, el efecto de los dos carriles de `PanelEv` la tiene como dependencia
  // y se reiniciaría sin motivo, perdiendo el primer tic del sondeo.
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

      {/* Mientras no se sabe, no se enseña ninguna de las dos cosas: enseñar el panel y quitarlo
          medio segundo después sería peor que esperar, y enseñar el aviso a quien sí tiene acceso
          es acusarle de algo que no es verdad. */}
      {acceso == null ? null : acceso.allowed
        ? <PanelEv token={identityToken} acceso={acceso} onSinAcceso={onSinAcceso} />
        : <TrackerGate acceso={acceso} />}
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
  // Todavía sin usar aquí: lo necesita la tarea que pinta cuándo caduca el pase junto al
  // UPDATED/STALE de la cabecera, y `acceso` solo vive en el componente padre.
  acceso: TrackerAccess
  onSinAcceso: () => void
}) {
  void acceso
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
  // Qué tarjeta se está arrastrando. En un ref y no en estado: cambia en cada `dragover` y volver
  // a pintar la rejilla entera a esa frecuencia hace que el arrastre se sienta pegajoso.
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
          // Un 403 no es un fallo: es que el pase caducó (o dejó de contar la apuesta) con la
          // pantalla abierta. Enseñar "Couldn't load the tracker" diría que algo se ha roto cuando
          // no se ha roto nada; lo correcto es volver a la puerta.
          if ((e as { status?: number })?.status === 403) { onSinAcceso(); return }
          // Solo se da por fallida la PRIMERA carga: una vez hay tarjetas en pantalla, un sondeo
          // que falle no debe borrarlas, porque lo de antes sigue siendo cierto y vaciar la
          // pantalla por un fallo de red pasajero es peor que enseñarlo un minuto más viejo.
          setFallo((antes) => antes || filasRef.current == null)
        })
    }
    const rapido = () => {
      if (dormido() || filasRef.current == null) return
      fetchEvLive(token)
        .then((d) => { if (!cancelado) setFilas((f) => (f ? aplicarVivo(f, d.rows) : f)) })
        .catch((e) => {
          if (cancelado) return
          // Mismo trato que el carril lento: un 403 es la puerta, no un fallo de red que se pueda
          // ignorar como el resto de errores de este carril.
          if ((e as { status?: number })?.status === 403) { onSinAcceso(); return }
          /* el carril rápido es un extra: si falla por otra razón, se sigue viendo lo del lento */
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

  /** Marcar o desmarcar UNA máquina en el selector. Al marcarla, entra la última de la rejilla. */
  function alternarUna(code: string) {
    const seOculta = !ocultas.has(code)
    cambiar(alternar(ocultas, code))
    // Al ocultar no se toca el orden: el código se queda sin efecto porque `ordenar` recibe las
    // filas ya filtradas. Se comprobó mutando el código que limpiarlo no cambia nada.
    if (!seOculta) guardarYOrdenar(mostrarAlFinal(mostradas, orden, code))
  }

  function guardarYOrdenar(siguiente: string[]) {
    setOrden(siguiente)
    guardarOrden(siguiente)
  }

  /** Soltar la tarjeta que se arrastra sobre `destino`. */
  function soltarSobre(destino: string) {
    const code = arrastrando.current
    arrastrando.current = null
    if (!code || code === destino) return
    // El primer arrastre congela lo que hay en pantalla: una tarjeta que nunca se tocó no tiene
    // posición, así que sin esto no habría nada que mover.
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
            {/* Sin esto, deshacer un orden que no gusta obliga a arrastrarlo todo de vuelta. */}
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
