import { useState, useRef, useReducer, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useIdentityToken } from '@privy-io/react-auth'
import { COLORS, FONTS, GRADIENT, formatUsd, rarityGlow } from '../../theme'
import { useChat, type ChatLine } from '../../../hooks/useChat'
import { useDrops } from '../../drops/useDrops'
import { useProfile } from '../../../hooks/useProfile'
import { useEmbeddedSolanaAddress } from '../../../wallet/embedded'
import { NombreUsuario } from '../../badges/NombreUsuario'

/**
 * El nombre de quien habla, enlazado a su perfil, con la acción de propina al lado.
 *
 * Solo enlaza si el mensaje trae wallet. NO la tienen los mensajes anteriores a que se empezara a
 * guardar ni los avisos de la casa, que no son de nadie — y un enlace a `/profile/undefined` es
 * peor que texto plano: promete algo y lleva a una página vacía.
 *
 * El aspecto es el mismo en los dos casos a propósito: el nombre ya va coloreado por usuario, y
 * subrayar solo unos pocos convertiría una lista de nombres en un mosaico.
 *
 * Aquí NO se ofrece dar propina. Hubo un botón TIP al lado de cada nombre y se quitó: repetido en
 * cada mensaje era ruido constante, y la lista de nombres es justo donde menos sitio sobra. La
 * forma de dar propina desde el chat es el comando `/tip`, que llega al mismo modal.
 *
 * The rank emblem and tags come from `NombreUsuario`; messages without a wallet have neither,
 * since they belong to nobody.
 */
function Autor({ msg, style }: { msg: ChatLine; style: React.CSSProperties }) {
  if (!msg.wallet) return <span style={style}>{msg.user}</span>
  return (
    <NombreUsuario wallet={msg.wallet} size={15}>
      <Link to={`/profile/${encodeURIComponent(msg.wallet)}`} title={`View ${msg.user}'s profile`}
        style={{ ...style, textDecoration: 'none' }}>
        {msg.user}
      </Link>
    </NombreUsuario>
  )
}
import { useReducedMotion } from '../../useReducedMotion'
import { showToast } from '../../toastBus'
import { UsernameModal } from '../../components/UsernameModal'
import { TipModal } from '../../components/TipModal'
import { useStickToBottom } from './useStickToBottom'
import { MentionAutocomplete, type CandidatoLista } from './MentionAutocomplete'
import { buscarMencion, resolverMenciones } from './mentions'
import { parseComando, comandosDisponibles, buscarComando, type ComandoEscrito } from './commands'
import { useUserSearch, type UsuarioEncontrado } from '../../../onchain/userSearch'
import { MessageText } from './MessageText'
import type { LiveDrop } from '../../drops/dropsStore'

// Opener label for a drop row: username if known, else a short wallet.
function dropOpener(drop: LiveDrop): string {
  if (drop.username) return drop.username
  const w = drop.wallet ?? ''
  return w.length > 8 ? `${w.slice(0, 4)}…${w.slice(-4)}` : (w || 'anon')
}

// Palette for coloring usernames deterministically
const USER_COLORS = ['#b78cff', '#00ffc4', '#5ad1ff', '#ff6b6b', '#ffd166', '#f7c59f']
function userColor(user: string | null | undefined): string {
  const s = user || 'anon'
  let hash = 0
  for (let i = 0; i < s.length; i++) hash = (hash * 31 + s.charCodeAt(i)) | 0
  return USER_COLORS[Math.abs(hash) % USER_COLORS.length]
}

// Normalize a timestamp to milliseconds. Backend emits epoch SECONDS; local/legacy
// drops are stored in ms. Anything below ~1e12 is seconds (pre-2001 in ms terms).
function toMs(ts: number): number {
  return ts < 1e12 ? ts * 1000 : ts
}

function formatTs(ts: number): string {
  const d = new Date(toMs(ts))
  const hh = d.getHours().toString().padStart(2, '0')
  const mm = d.getMinutes().toString().padStart(2, '0')
  return `${hh}:${mm}`
}

function ago(ts: number): string {
  const s = Math.max(0, Math.floor((Date.now() - toMs(ts)) / 1000))
  if (s < 60) return 'just now'
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export function ChatDock({
  collapsed = false,
  onToggle,
  chatOnly = false,
  onClose,
}: {
  collapsed?: boolean
  onToggle?: () => void
  /** Chat-only mode (mobile full-screen): hides the Recent Drops section. */
  chatOnly?: boolean
  /** When provided, shows a close (✕) button in the chat header (mobile drawer). */
  onClose?: () => void
}) {
  const navigate = useNavigate()
  const drops = useDrops()
  const { messages, send, canPost, online, onlineUsers } = useChat()
  const { identityToken } = useIdentityToken()
  const { username } = useProfile()
  const ownWallet = useEmbeddedSolanaAddress()
  const reducedMotion = useReducedMotion()
  const [draft, setDraft] = useState('')
  const [nameModal, setNameModal] = useState(false)
  // A quién se le va a dar propina, y cuánto. Hoy solo lo pone el comando `/tip ana 5`: el botón
  // que había al lado de cada nombre se quitó por ruidoso. `amount` llega ya escrito desde el
  // comando, así que el modal abre relleno y solo queda confirmar.
  const [tipTarget, setTipTarget] =
    useState<{ wallet: string; alias?: string | null; amount?: string } | null>(null)
  // Las respuestas a los comandos (errores y avisos) se guardan APARTE de `messages`: no pasan por
  // el servidor, no las ve nadie más, y `messages` es lo que llega del socket.
  const [respuestas, setRespuestas] = useState<ChatLine[]>([])
  // Escape cierra la lista del autocompletado sin borrar lo escrito. Se recuerda CON QUÉ TEXTO se
  // cerró, y no la posición del cursor: el comando se detecta por el principio del mensaje, así
  // que mover el cursor no lo cierra, pero escribir cualquier otra cosa sí lo reabre.
  const [cerradaEn, setCerradaEn] = useState<string | null>(null)
  const promptedName = useRef(false)
  // El chat se abre por el ÚLTIMO mensaje y sigue el ritmo sin arrastrar a quien lee.
  const inputRef = useRef<HTMLInputElement>(null)
  // Posición del cursor: la mención que se está escribiendo depende de DÓNDE está el cursor, no
  // del final del texto, para que escribir en medio de un mensaje ya escrito también la abra.
  const [cursor, setCursor] = useState(0)
  const listaRef = useRef<HTMLDivElement>(null)
  // Lo que se pinta: lo que llegó del servidor MÁS las respuestas locales a los comandos, siempre
  // al final. Son contestaciones a lo que acabas de escribir, así que su sitio es el último, y
  // ordenar por `ts` solo serviría para que una respuesta se colara por encima si el reloj del
  // servidor va adelantado.
  const lineas = respuestas.length === 0 ? messages : [...messages, ...respuestas]
  const { pegadoAlFondo, nuevosSinVer, bajarDelTodo, alHacerScroll } =
    useStickToBottom(listaRef, lineas.length)

  // First time the user focuses the chat with no username set, nudge them to pick one.
  function onChatFocus() {
    if (canPost && !username && !promptedName.current) {
      promptedName.current = true
      showToast('Set a username so others recognize you in chat', 'info', {
        label: 'Choose username', onClick: () => setNameModal(true),
      })
    }
  }

  // Aviso cuando te mencionan y NO lo estás viendo: con el panel plegado o habiendo subido a
  // leer. Si estás abajo y mirando, el mensaje ya se destaca solo y un toast encima sería ruido.
  // Se recuerda el último avisado para no repetirlo en cada render.
  const ultimoAvisado = useRef<number | null>(null)
  useEffect(() => {
    const ultimo = messages[messages.length - 1]
    if (!ultimo || !ownWallet) return
    const meNombra = ultimo.mentions?.some((m) => m.wallet === ownWallet)
    if (!meNombra || ultimo.wallet === ownWallet) return
    if (ultimoAvisado.current === ultimo.ts) return
    if (!collapsed && pegadoAlFondo) return
    ultimoAvisado.current = ultimo.ts
    showToast(`${ultimo.user} mentioned you`, 'info', {
      label: 'View', onClick: () => { onToggle?.(); bajarDelTodo() },
    })
  }, [messages, ownWallet, collapsed, pegadoAlFondo, onToggle, bajarDelTodo])

  const [, forceTick] = useReducer((x: number) => x + 1, 0)
  useEffect(() => {
    const id = setInterval(forceTick, 60_000)
    return () => clearInterval(id)
  }, [])

  // ── Resizable divider state ──
  const [dropsHeight, setDropsHeight] = useState(
    () => Math.max(120, Math.min(Math.round(window.innerHeight / 2), window.innerHeight - 260)),
  )
  const dragRef = useRef<{ startY: number; startHeight: number } | null>(null)

  function handleResizerPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = { startY: e.clientY, startHeight: dropsHeight }
  }

  function handleResizerPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragRef.current) return
    const newHeight = dragRef.current.startHeight + (e.clientY - dragRef.current.startY)
    const clamped = Math.max(120, Math.min(newHeight, window.innerHeight - 260))
    setDropsHeight(clamped)
  }

  function handleResizerPointerUp(e: React.PointerEvent<HTMLDivElement>) {
    e.currentTarget.releasePointerCapture(e.pointerId)
    dragRef.current = null
  }

  // Un espacio de más por delante NO puede cambiar lo que es el mensaje: " /tip ana 5" no empieza
  // por barra, así que se iría PUBLICADO en la sala contando a quién y cuánto da dinero este
  // jugador. Y está a un carácter de distancia: pegar el comando o el teclado del móvil ya lo
  // meten. Se descuenta lo que sobra por delante y el cursor se mueve con él.
  //
  // Por detrás no se recorta: "/tip ana " con el espacio final significa que el cursor está en el
  // importe, y quitarlo devolvería la lista de destinatarios encima de lo ya escrito.
  const textoComando = draft.trimStart()
  const sobraDelante = draft.length - textoComando.length

  // El comando que se está escribiendo. Comando y mención son EXCLUYENTES y manda el comando,
  // porque la barra abre el mensaje: dentro de "/tip @ana", la arroba es parte de un argumento
  // suyo, no una mención a medias.
  const comando = canPost
    ? parseComando(textoComando, Math.max(0, cursor - sobraDelante))
    : null
  const defComando = comando ? buscarComando(comando.nombre) : undefined
  // Un comando apagado por bandera no existe para el jugador: ni se ofrece, ni busca a nadie, ni
  // se ejecuta. Ver `featureFlags.ts`.
  const comandoVivo = defComando?.disponible() ? defComando : undefined

  // Qué argumento pide un jugador (el destinatario de `/tip`). Se le pregunta al REGISTRO en vez
  // de mirar si el comando se llama "tip": añadir otro comando con destinatario no debe obligar a
  // tocar el ChatDock.
  const idxUsuario = comandoVivo ? comandoVivo.args.findIndex((a) => a.tipo === 'usuario') : -1
  const enArgUsuario = !!comando && idxUsuario >= 0 && comando.argActivo === idxUsuario
  const consultaUsuario = comando && idxUsuario >= 0 ? (comando.args[idxUsuario] ?? '') : ''
  // La búsqueda sigue viva mientras se escriba el comando ENTERO, no solo mientras el cursor está
  // sobre el destinatario: al pasar al importe hay que seguir sabiendo a quién resolvía "ana", y
  // como la consulta no cambia, responde la caché sin una petición nueva. Con `activo` en false
  // (cualquier otro texto) el hook no pide nada: ese es su freno.
  const { resultados, cargando } =
    useUserSearch(identityToken ?? null, consultaUsuario, idxUsuario >= 0)

  // La mención que se está escribiendo y a quién ofrece. Se filtra por nombre Y por wallet: quien
  // no tiene alias se identifica por su wallet, y es la única forma de encontrarlo.
  const mencion = canPost && !comando ? buscarMencion(draft, cursor) : null

  /** Lo que enseña la lista: comandos, jugadores o menciones, según lo que se esté escribiendo. */
  function candidatosLista(): CandidatoLista[] {
    if (cerradaEn === draft) return []              // cerrada con Escape hasta escribir otra cosa
    if (comando) {
      // En el NOMBRE del comando (argActivo -1), los comandos que empiezan por lo tecleado.
      if (comando.argActivo === -1) {
        return comandosDisponibles()
          .filter((c) => c.nombre.startsWith(comando.nombre))
          .map((c) => ({ wallet: `/${c.nombre}`, name: `/${c.nombre}`, detalle: c.descripcion }))
      }
      // En el argumento de destinatario, lo que devuelva la búsqueda. Quien no tiene alias se
      // identifica por su wallet, que es además lo que hay que escribir para elegirlo.
      if (enArgUsuario) {
        return resultados.slice(0, 6)
          .map((u) => ({ wallet: u.wallet, name: u.alias ?? u.wallet, online: u.online }))
      }
      return []
    }
    if (!mencion) return []
    const q = mencion.consulta.toLowerCase()
    return onlineUsers
      .filter((u) => u.name.toLowerCase().includes(q) || u.wallet.toLowerCase().startsWith(q))
      .slice(0, 6)
  }
  const candidatos = candidatosLista()

  // Escribiendo el NOMBRE de un comando, una lista vacía no dice nada: se lee igual que "esto está
  // roto". Pasa en dos sitios distintos y hay que distinguirlos, porque la salida no es la misma:
  // sin ningún comando disponible (hoy, con las propinas apagadas, `/tip` es el único) no hay nada
  // que probar; con comandos pero sin coincidencia, lo que falla es lo tecleado y se enseña qué
  // hay.
  const avisoComandos = comando && comando.argActivo === -1 && cerradaEn !== draft
      && candidatos.length === 0
    ? (comandosDisponibles().length === 0
        ? 'No commands are available right now.'
        : `No command matches "/${comando.nombre}". ${textoComandos()}`)
    : null

  /** Deja el foco en el campo con el cursor en `pos`: seguir escribiendo tras elegir de la lista
   *  tiene que ser lo natural, no tener que volver a pulsar en el input. */
  function ponerCursor(pos: number) {
    requestAnimationFrame(() => {
      inputRef.current?.focus()
      inputRef.current?.setSelectionRange(pos, pos)
      setCursor(pos)
    })
  }

  function elegirMencion(u: { wallet: string; name: string }) {
    if (!mencion) return
    const antes = draft.slice(0, mencion.desde)
    const despues = draft.slice(mencion.desde + 1 + mencion.consulta.length)
    setDraft(`${antes}@${u.name} ${despues.replace(/^\s+/, '')}`)
    ponerCursor(antes.length + u.name.length + 2)
  }

  /** Sustituye el trozo `n` del comando (0 = su nombre, 1 = el primer argumento) por `valor`, sin
   *  tocar lo que venga detrás. Los espacios de más se normalizan a uno: quien elige de la lista
   *  espera "/tip ana ", no "/tip   ana". */
  function ponerTrozo(n: number, valor: string) {
    const trozos = textoComando.slice(1).split(/\s+/)
    while (trozos.length <= n) trozos.push('')
    trozos[n] = valor
    const antes = `/${trozos.slice(0, n + 1).join(' ')} `
    setDraft(antes + trozos.slice(n + 1).filter((t) => t !== '').join(' '))
    ponerCursor(antes.length)
  }

  function elegirCandidato(item: CandidatoLista) {
    if (!comando) { elegirMencion(item); return }
    // El nombre del comando llega con barra ("/tip") porque es como se lee en la lista; en el
    // texto la barra ya está puesta.
    if (comando.argActivo === -1) ponerTrozo(0, item.name.replace(/^\//, ''))
    else ponerTrozo(comando.argActivo + 1, item.name)
  }

  /** ¿Este mensaje me nombra? Sin wallet propia (sesión cerrada) nunca. */
  function meMenciona(msg: ChatLine): boolean {
    return !!ownWallet && !!msg.mentions?.some((m) => m.wallet === ownWallet)
  }

  /** Contesta en el chat sin pasar por el servidor: es una respuesta a lo que TÚ acabas de
   *  escribir, y no tiene por qué verla la sala entera. */
  function responder(texto: string) {
    setRespuestas((prev) => [...prev,
      { user: 'Battle Arena', text: texto, ts: Date.now(), kind: 'system' }])
  }

  /** Qué comandos hay, para las respuestas de error. Sin ninguno se DICE, en vez de dejar una
   *  frase que enumera una lista vacía. */
  function textoComandos(): string {
    const vivos = comandosDisponibles()
    return vivos.length
      ? `Available commands: ${vivos.map((c) => `/${c.nombre}`).join(', ')}`
      : 'No commands are available right now.'
  }

  /**
   * Abre el modal de propina con el destinatario y el importe que traía el comando.
   *
   * Devuelve si el comando se COMPLETÓ: lo que falla deja lo escrito en el campo, porque casi
   * siempre se arregla cambiando una palabra y reescribirlo entero es peor.
   */
  function ejecutarTip(args: string[]): boolean {
    const [aQuien, cuanto] = args
    if (!aQuien) {
      responder('Usage: /tip <player> <amount>, for example /tip ana 5')
      return false
    }
    // Por nombre o wallet EXACTOS, nunca por parecido ni "el primero de la lista": ahí se decide a
    // quién se le manda dinero, y "ana" no puede acabar en manos de "anabel".
    const q = aQuien.toLowerCase()
    const destino: UsuarioEncontrado | undefined = resultados.find(
      (u) => (u.alias ?? '').toLowerCase() === q || u.wallet.toLowerCase() === q)
    if (!destino) {
      // La búsqueda tiene 250 ms de espera, así que quien pega el comando entero y pulsa Enter sin
      // pausa llega ANTES que la respuesta. Ahí no ha hecho nada mal: decirle que ese jugador no
      // existe es acusarle de un fallo nuestro, y encima mandarle a hacer lo que ya hizo.
      responder(cargando
        ? `Still looking for "${aQuien}". Try again in a moment.`
        : `No player found for "${aQuien}". Pick one from the list while you type the name.`)
      return false
    }
    // El importe puede faltar, y entonces lo pide el modal, que es su trabajo. Lo que no vale es
    // uno ESCRITO que no sea un número mayor que 0: abrir el modal con "mucho" dentro dejaría al
    // jugador mirando un botón apagado sin saber por qué.
    if (cuanto !== undefined) {
      const n = Number(cuanto)
      if (!Number.isFinite(n) || n <= 0) {
        responder(`"${cuanto}" is not a valid amount. Use a number greater than 0, like /tip ${aQuien} 5`)
        return false
      }
    }
    setTipTarget({ wallet: destino.wallet, alias: destino.alias, amount: cuanto })
    return true
  }

  /** Ejecuta el comando escrito. Nada de esto viaja al servidor. */
  function ejecutarComando(cmd: ComandoEscrito) {
    const def = buscarComando(cmd.nombre)
    // Un comando apagado se contesta igual que uno inexistente: para el jugador no existe, y
    // decirle "está apagado" solo le hablaría de una función que no puede usar.
    if (!def?.disponible()) { responder(`Unknown command "/${cmd.nombre}". ${textoComandos()}`); return }
    // Solo se limpia el campo cuando el comando SALIÓ. Tras un error, lo escrito se queda: se
    // corrige una palabra, o se vuelve a pulsar Enter cuando la búsqueda ya ha contestado, que es
    // justo lo que arregla el caso de pegar el comando y no esperar.
    if (def.nombre === 'tip') { if (ejecutarTip(cmd.args)) setDraft(''); return }
    // Un comando registrado que nadie ejecuta es un fallo nuestro, no del jugador.
    responder(`/${def.nombre} is not available yet.`)
  }

  function handleSend() {
    if (!draft.trim()) return
    // Un texto que empieza por barra es un comando: se EJECUTA y no se envía, exista o no. Si se
    // enviara, "/tip ana 5" saldría publicado en la sala, contando a quién y cuánto da dinero
    // este jugador, y encima sin hacer lo que pedía.
    const cmd = parseComando(textoComando, textoComando.length)
    if (cmd) { ejecutarComando(cmd); return }
    // Las etiquetas se resuelven contra los conectados AHORA: si el mencionado se fue mientras
    // se escribía, el servidor la descartará igual.
    send(draft, resolverMenciones(draft, onlineUsers))
    setDraft('')
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    // Con la lista abierta (menciones o comandos), Enter es para ELEGIR (lo maneja el
    // autocompletado) y no para enviar: si no, se mandaría el mensaje a medio escribir.
    if (e.key === 'Enter' && candidatos.length === 0) handleSend()
  }

  if (collapsed) {
    return (
      <aside
        style={{
          background: '#0c1019',
          borderLeft: `1px solid ${COLORS.border}`,
          height: '100%',
          width: 36,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          paddingTop: 14,
          gap: 10,
        }}
      >
        <button
          onClick={() => onToggle?.()}
          title="Expand chat"
          style={{
            background: 'transparent',
            border: `1px solid ${COLORS.border}`,
            color: COLORS.muted,
            borderRadius: 8,
            width: 26,
            height: 26,
            cursor: 'pointer',
            fontSize: 13,
          }}
        >
          ‹
        </button>
        <div
          style={{
            writingMode: 'vertical-rl',
            transform: 'rotate(180deg)',
            fontFamily: FONTS.mono,
            fontSize: 10,
            letterSpacing: '0.16em',
            color: COLORS.muted,
            marginTop: 8,
          }}
        >
          CHAT
        </div>
      </aside>
    )
  }

  return (
    <aside
      style={{
        background: '#0c1019',
        borderLeft: chatOnly ? 'none' : `1px solid ${COLORS.border}`,
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
      }}
    >
      {/* Recent Drops — hidden for now, kept for future reuse. Re-enable by changing
          `false &&` back to `!chatOnly &&`. */}
      {false && !chatOnly && (<>
      {/* ── RECENT DROPS ── */}
      <div
        style={{
          height: dropsHeight,
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        {/* Header — fixed (stays visible while the list scrolls) */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '14px 16px 10px',
            flexShrink: 0,
          }}
        >
          <div
            style={{
              fontFamily: FONTS.mono,
              fontSize: 10.5,
              fontWeight: 700,
              letterSpacing: '0.16em',
              color: COLORS.text,
              display: 'flex',
              alignItems: 'center',
              gap: 7,
            }}
          >
            {/* Pulse dot */}
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: COLORS.green,
                boxShadow: `0 0 8px ${COLORS.green}`,
                display: 'inline-block',
              }}
            />
            RECENT DROPS
          </div>
          {onToggle && (
            <button
              onClick={onToggle}
              title="Collapse panel"
              style={{
                background: 'transparent',
                border: `1px solid ${COLORS.border}`,
                color: COLORS.muted,
                cursor: 'pointer',
                borderRadius: 7,
                width: 24,
                height: 24,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 14,
                lineHeight: 1,
              }}
            >
              ›
            </button>
          )}
        </div>

        {/* Drop items — scrolls (header above stays fixed) */}
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '0 16px 14px' }}>
        {drops.length === 0 ? (
          <div style={{ fontSize: 11, color: COLORS.muted }}>
            No drops yet — open a pack to see it here.
          </div>
        ) : (
          drops.map((drop) => {
            const glow = rarityGlow(drop.rarity)             // null for common → no box
            const accent = glow ?? COLORS.muted              // dot / image tint for common
            const isMine = !!username && !!drop.username && drop.username === username
            const isEpic = (drop.rarity ?? '').toLowerCase() === 'epic'
            return (
              <div
                key={drop.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '8px 10px',
                  margin: '3px 0',
                  borderRadius: 12,
                  border: glow ? `1px solid ${glow}55` : '1px solid transparent',
                  background: glow ? `${glow}0d` : 'transparent',
                  boxShadow: glow ? `0 0 14px -6px ${glow}` : 'none',
                  animation: reducedMotion ? undefined : 'ba-dropin .32s ease-out both',
                }}
              >
                {/* Rarity-glow dot */}
                <span
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    background: accent,
                    boxShadow: `0 0 8px ${accent}`,
                    flexShrink: 0,
                  }}
                />

                {/* Card image / emoji */}
                <div
                  style={{
                    width: 28,
                    height: 38,
                    borderRadius: 6,
                    background: `radial-gradient(circle at 40% 30%,${accent}33,#10141c)`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 13,
                    flexShrink: 0,
                    overflow: 'hidden',
                  }}
                >
                  {drop.image ? (
                    <img
                      src={drop.image}
                      alt={drop.name}
                      style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: 6 }}
                    />
                  ) : (
                    '🃏'
                  )}
                </div>

                {/* Name + username + rarity */}
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                    }}
                  >
                    <span
                      style={{
                        fontSize: 11.5,
                        fontWeight: 600,
                        color: COLORS.text,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {drop.name}
                    </span>
                    {isEpic && (
                      <span
                        style={{
                          fontFamily: FONTS.mono,
                          fontSize: 8,
                          fontWeight: 800,
                          letterSpacing: '0.06em',
                          color: '#1a1305',
                          background: 'linear-gradient(135deg,#ffe28a,#f5c542)',
                          borderRadius: 5,
                          padding: '1.5px 5px',
                          flexShrink: 0,
                        }}
                      >
                        BIG PULL
                      </span>
                    )}
                  </div>
                  <div
                    style={{
                      fontSize: 9,
                      color: isMine ? COLORS.green : userColor(drop.username ?? drop.wallet),
                      fontWeight: 700,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {isMine ? 'you' : dropOpener(drop)}
                  </div>
                  <div style={{ fontSize: 9, color: COLORS.muted }}>{drop.rarity ?? ''}</div>
                </div>

                {/* Value + time */}
                <div
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'flex-end',
                    flexShrink: 0,
                    marginLeft: 'auto',
                  }}
                >
                  <div
                    style={{
                      fontFamily: FONTS.display,
                      fontWeight: 800,
                      fontSize: 12,
                      color: isEpic ? '#f5c542' : COLORS.green,
                    }}
                  >
                    {drop.valueUsd != null ? formatUsd(drop.valueUsd) : ''}
                  </div>
                  <div style={{ fontSize: 9, color: COLORS.muted }}>
                    {ago(drop.ts)}
                  </div>
                </div>
              </div>
            )
          })
        )}
        </div>
      </div>

      {/* ── RESIZER HANDLE ── */}
      <div
        onPointerDown={handleResizerPointerDown}
        onPointerMove={handleResizerPointerMove}
        onPointerUp={handleResizerPointerUp}
        style={{
          height: 6,
          cursor: 'row-resize',
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#0c1019',
          borderTop: `1px solid ${COLORS.border}`,
          borderBottom: `1px solid ${COLORS.border}`,
        }}
      >
        {/* Grip dots */}
        <div
          style={{
            width: 24,
            height: 3,
            borderRadius: 2,
            background: COLORS.border,
          }}
        />
      </div>
      </>)}

      {/* ── CHAT REGION (flex: 1, scrolls internally) ── */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* CHAT heading — matches LIVE DROPS style */}
        <div
          style={{
            padding: '10px 16px 4px',
            display: 'flex',
            alignItems: 'center',
            gap: 7,
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: '50%',
              background: COLORS.green,
              boxShadow: `0 0 8px ${COLORS.green}`,
              display: 'inline-block',
              flexShrink: 0,
            }}
          />
          <span
            style={{
              fontFamily: FONTS.mono,
              fontSize: 10.5,
              fontWeight: 700,
              letterSpacing: '0.16em',
              color: COLORS.text,
            }}
          >
            CHAT
          </span>
          <span
            style={{
              fontSize: 10,
              color: COLORS.muted,
              marginLeft: 4,
            }}
          >
            {online} online
          </span>
          {(onToggle || onClose) && (
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              {onToggle && (
                <button
                  onClick={onToggle}
                  title="Collapse panel"
                  style={{
                    background: 'transparent', border: `1px solid ${COLORS.border}`,
                    color: COLORS.muted, borderRadius: 8, width: 26, height: 26, cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, lineHeight: 1,
                  }}
                >
                  ›
                </button>
              )}
              {onClose && (
                <button
                  onClick={onClose}
                  title="Close chat"
                  style={{
                    background: 'transparent', border: `1px solid ${COLORS.border}`,
                    color: COLORS.muted, borderRadius: 8, width: 26, height: 26, cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, lineHeight: 1,
                  }}
                >
                  ✕
                </button>
              )}
            </span>
          )}
        </div>

        {/* ── MESSAGES ──
            El envoltorio relativo existe para poder colgar el aviso de "mensajes nuevos" sobre
            la lista sin sacarlo del flujo de la columna. */}
        <div style={{ flex: 1, minHeight: 0, position: 'relative', display: 'flex' }}>
        <div
          ref={listaRef}
          onScroll={alHacerScroll}
          data-testid="chat-messages"
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '6px 16px',
            display: 'flex',
            flexDirection: 'column',
            gap: 11,
          }}
        >
          {lineas.length === 0 ? (
            <div style={{ fontSize: 11, color: COLORS.muted, fontFamily: FONTS.body, marginTop: 8 }}>
              Be the first to write…
            </div>
          ) : (
            lineas.map((msg, idx) => msg.kind === 'system' && (msg.event === 'created' || msg.event === 'hit' || msg.event === 'winner') ? (
              /* Structured system event — same inline look for all three:
                 icon/tag + "{who} {text}" + gold value (+ optional button).
                 created: "{creator} created a Pack Battle $50" [Join]
                 hit:     "[TCG PRIME] {who} pulled {card} $320"  (hits are always gacha pulls;
                          the chip shows the machine it came from, or "GACHA" if unknown)
                 winner:  "🏆 {who} won a Pack Battle $1.2k" [View] */
              <div key={`${msg.ts}-${idx}`} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '3px 2px' }}>
                {msg.event === 'created' ? (
                  <svg width="12" height="12" viewBox="0 0 16 16" fill={COLORS.green} style={{ flexShrink: 0 }} aria-hidden="true">
                    <circle cx="8" cy="5" r="3" />
                    <path d="M2 15c0-3.3 2.7-6 6-6s6 2.7 6 6z" />
                  </svg>
                ) : msg.event === 'winner' ? (
                  <span style={{ flexShrink: 0, fontSize: 12, lineHeight: 1 }} aria-hidden="true">🏆</span>
                ) : (
                  /* hit — a gacha pull; the chip names the machine it came from */
                  <span style={{ flexShrink: 0, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', padding: '1px 7px', borderRadius: 6, background: 'rgba(169,139,255,.14)', border: '1px solid rgba(169,139,255,.4)', fontFamily: FONTS.mono, fontSize: 9, fontWeight: 700, letterSpacing: '.08em', color: '#a98bff' }}>
                    {(msg.machine ?? 'GACHA').toUpperCase()}
                  </span>
                )}
                <span style={{ flex: 1, fontSize: 12, fontFamily: FONTS.body, lineHeight: 1.35 }}>
                  <Autor msg={msg} style={{ color: userColor(msg.user), fontWeight: 700 }} />
                  <span style={{ color: COLORS.muted }}> {msg.text} </span>
                  {msg.amountUsd != null && (
                    <span style={{ color: '#f5c542', fontWeight: 800 }}>{formatUsd(msg.amountUsd)}</span>
                  )}
                  {(msg.event === 'hit' || msg.event === 'winner') && msg.mult != null && msg.mult > 0 && (
                    <span style={{ color: COLORS.muted, fontWeight: 700 }}> (x{msg.mult % 1 === 0 ? msg.mult : msg.mult.toFixed(1)})</span>
                  )}
                </span>
                {msg.action && (
                  <button onClick={() => navigate(`/play/battle/${msg.action!.battleId}`)} style={{
                    flexShrink: 0, background: 'transparent', border: `1px solid ${COLORS.green}`, borderRadius: 7,
                    padding: '3px 10px', color: COLORS.green, fontFamily: FONTS.display, fontWeight: 800,
                    fontSize: 11, cursor: 'pointer', whiteSpace: 'nowrap',
                  }}>{msg.action.label}</button>
                )}
              </div>
            ) : msg.kind === 'system' ? (
              /* System announcement: big hit / winner */
              <div key={`${msg.ts}-${idx}`} style={{
                display: 'flex', alignItems: 'center', gap: 8,
                background: 'rgba(0,255,196,0.06)', border: `1px solid ${COLORS.border}`,
                borderLeft: `3px solid ${COLORS.green}`, borderRadius: 8, padding: '7px 10px',
              }}>
                <span style={{ flex: 1, fontSize: 12, color: COLORS.text, fontFamily: FONTS.body, lineHeight: 1.3 }}>
                  {msg.text}
                </span>
                {msg.action && (
                  <button onClick={() => navigate(`/play/battle/${msg.action!.battleId}`)} style={{
                    flexShrink: 0, background: COLORS.green, border: 'none', borderRadius: 7,
                    padding: '5px 11px', color: '#06120c', fontFamily: FONTS.display, fontWeight: 800,
                    fontSize: 11, cursor: 'pointer', whiteSpace: 'nowrap',
                  }}>{msg.action.label}</button>
                )}
              </div>
            ) : (
              <div
                key={`${msg.ts}-${idx}`}
                style={meMenciona(msg) ? {
                  // Un mensaje que te nombra tiene que saltar a la vista sin leerlo entero.
                  background: `${COLORS.green}0f`,
                  borderLeft: `2px solid ${COLORS.green}`,
                  marginLeft: -16, marginRight: -16, padding: '4px 16px 4px 14px',
                } : undefined}
              >
                {/* Row: avatar + name + timestamp */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 7,
                    marginBottom: 2,
                  }}
                >
                  {/* Avatar */}
                  <div
                    style={{
                      width: 21,
                      height: 21,
                      borderRadius: '50%',
                      background: GRADIENT,
                      flexShrink: 0,
                    }}
                  />
                  {/* Username */}
                  <Autor
                    msg={msg}
                    style={{
                      fontWeight: 700,
                      fontSize: 11.5,
                      color: userColor(msg.user),
                      fontFamily: FONTS.body,
                    }}
                  />
                  {/* Timestamp */}
                  <span style={{ fontSize: 9, color: COLORS.muted, marginLeft: 'auto' }}>
                    {formatTs(msg.ts)}
                  </span>
                </div>
                {/* Bubble text */}
                <div
                  style={{
                    fontSize: 12,
                    color: COLORS.text,
                    paddingLeft: 28,
                    lineHeight: 1.35,
                    fontFamily: FONTS.body,
                  }}
                >
                  <MessageText text={msg.text} mentions={msg.mentions} />
                </div>
              </div>
            ))
          )}
        </div>

        {/* Solo cuando hay algo que anunciar Y el jugador no está abajo: si estuviera abajo ya
            lo habría visto, y un aviso permanente sería ruido. */}
        {!pegadoAlFondo && nuevosSinVer > 0 && (
          <button
            onClick={() => bajarDelTodo()}
            style={{
              position: 'absolute', bottom: 10, left: '50%', transform: 'translateX(-50%)',
              background: COLORS.panel2, border: `1px solid ${COLORS.border}`, borderRadius: 999,
              padding: '5px 12px', color: COLORS.text, fontFamily: FONTS.mono, fontSize: 10.5,
              cursor: 'pointer', whiteSpace: 'nowrap', boxShadow: '0 4px 14px #0008',
            }}
          >
            {nuevosSinVer} new {nuevosSinVer === 1 ? 'message' : 'messages'} ↓
          </button>
        )}
        </div>

        {/* ── CHAT INPUT ── */}
        <div
          style={{
            padding: '12px 16px',
            borderTop: `1px solid ${COLORS.border}`,
            display: 'flex',
            gap: 8,
            position: 'relative',   // la lista de menciones se cuelga de aquí
          }}
        >
          <MentionAutocomplete
            // Al cambiar la lista se remonta, y el resaltado vuelve al primero. Es lo que se
            // espera al seguir escribiendo para filtrar.
            key={candidatos.map((u) => u.wallet).join(',')}
            candidatos={candidatos}
            onElegir={elegirCandidato}
            onCerrar={() => setCerradaEn(draft)}
          />
          {/* Un comando a medias que no ofrece nada se lee como "esto está roto". Se dice con
              todas las letras, tanto si no hay ninguno como si lo tecleado no existe. */}
          {avisoComandos && (
            <div
              role="status"
              style={{
                position: 'absolute', bottom: '100%', left: 0, right: 0, marginBottom: 6,
                background: COLORS.panel2, border: `1px solid ${COLORS.border}`, borderRadius: 10,
                padding: '8px 11px', boxShadow: '0 8px 24px #000a', zIndex: 5,
                fontFamily: FONTS.body, fontSize: 11.5, color: COLORS.muted,
              }}
            >
              {avisoComandos}
            </div>
          )}
          <input
            ref={inputRef}
            onSelect={(e) => setCursor(e.currentTarget.selectionStart ?? 0)}
            disabled={!canPost}
            placeholder={canPost ? 'Type a message…' : 'Log in to chat'}
            value={draft}
            onFocus={onChatFocus}
            onChange={(e) => { setDraft(e.target.value); setCursor(e.target.selectionStart ?? 0) }}
            onKeyDown={handleKeyDown}
            style={{
              flex: 1,
              background: '#0a0e16',
              border: `1px solid ${COLORS.border}`,
              borderRadius: 10,
              padding: '10px 12px',
              color: COLORS.text,
              fontSize: 12,
              outline: 'none',
              fontFamily: FONTS.body,
              cursor: canPost ? 'text' : 'not-allowed',
              opacity: canPost ? 1 : 0.6,
            }}
          />
          <button
            disabled={!canPost}
            onClick={handleSend}
            style={{
              width: 38,
              borderRadius: 10,
              border: 'none',
              background: GRADIENT,
              color: '#06120c',
              cursor: canPost ? 'pointer' : 'not-allowed',
              opacity: canPost ? 1 : 0.5,
              fontSize: 14,
            }}
          >
            ➤
          </button>
        </div>
      </div>
      {nameModal && <UsernameModal onClose={() => setNameModal(false)} />}
      {tipTarget && (
        <TipModal
          open
          to={{ wallet: tipTarget.wallet, alias: tipTarget.alias }}
          amountInicial={tipTarget.amount}
          source="chat"
          onClose={() => setTipTarget(null)}
        />
      )}
    </aside>
  )
}
