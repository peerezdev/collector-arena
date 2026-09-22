import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// Shape of what ChatDock hands to <TipModal> — TipModal's own props type isn't exported, and
// this test only needs the fields it asserts on, not TipModal's internals.
interface CapturedTipModalProps {
  open: boolean
  to: { wallet: string; alias?: string | null }
  source: 'profile' | 'chat'
  amountInicial?: string
  onClose: () => void
}

// Mock the chat hook so ChatDock doesn't open a real WebSocket. `chatState.messages` is
// mutable so individual tests can inject system announcements. `chatState.ownWallet` backs
// the useEmbeddedSolanaAddress mock below, so tip tests can set "who am I" per test.
// `tipModalCalls` records every prop set ChatDock hands to <TipModal>, so wiring tests can
// assert on WHO the modal was opened for, not just that a tip button exists somewhere.
const { chatState, tipModalCalls, toasts, flags, busqueda, badges } = vi.hoisted(() => ({
  chatState: { messages: [] as any[], ownWallet: null as string | null,
               canPost: false, onlineUsers: [] as { wallet: string; name: string }[],
               send: vi.fn() as ReturnType<typeof vi.fn> },
  tipModalCalls: [] as CapturedTipModalProps[],
  toasts: [] as string[],
  flags: { tips: true },
  busqueda: { resultados: [] as { wallet: string; alias: string | null; online: boolean }[],
              cargando: false,
              llamadas: [] as { consulta: string; activo: boolean }[] },
  badges: {} as Record<string, { rank: string | null; tags: string[] }>,
}))
vi.mock('../../badges/useBadges', () => ({ useBadges: () => badges }))
// Igual que en el perfil: aquí se prueba la pantalla con las propinas encendidas. Es un GETTER y
// no un valor fijo porque `/tip` es hoy el único comando: los dos tests de "propinas apagadas"
// necesitan verlo en false, y `vi.mock` se iza una sola vez por fichero.
vi.mock('../../../featureFlags', () => ({ get TIPS_ENABLED() { return flags.tips } }))
vi.mock('@privy-io/react-auth', () => ({ useIdentityToken: () => ({ identityToken: 'tok' }) }))
// La búsqueda de jugadores tiene sus propios tests (espera de 250 ms, caché, fallos): aquí solo
// importa el CABLEADO, o sea con qué consulta se la llama, si se la deja activa, y qué hace el
// ChatDock con lo que devuelve. Devolver nada cuando está inactiva imita al hook de verdad.
vi.mock('../../../onchain/userSearch', () => ({
  useUserSearch: (_token: string | null, consulta: string, activo: boolean) => {
    busqueda.llamadas.push({ consulta, activo })
    return { resultados: activo ? busqueda.resultados : [], cargando: activo && busqueda.cargando }
  },
}))
vi.mock('../../../hooks/useChat', () => ({
  useChat: () => ({ messages: chatState.messages, send: chatState.send, canPost: chatState.canPost,
                    online: 0, onlineUsers: chatState.onlineUsers }),
}))
vi.mock('../../../wallet/embedded', () => ({ useEmbeddedSolanaAddress: () => chatState.ownWallet }))
// TipModal is Task 5's own component, already tested there; ChatDock only needs to know it was
// opened for the right recipient, not TipModal's internals — so the mock records its props
// (open/to/source) instead of rendering anything.
// Los avisos se capturan en vez de pintarse: lo que se comprueba es SI se avisa y de qué,
// no cómo se ve el toast, que tiene sus propios tests.
vi.mock('../../toastBus', () => ({ showToast: (m: string) => { toasts.push(m) } }))
vi.mock('../../components/TipModal', () => ({
  TipModal: (props: CapturedTipModalProps) => { tipModalCalls.push(props); return null },
}))

import { ChatDock } from './ChatDock'
import { addDrop } from '../../drops/dropsStore'

// ChatDock uses useNavigate (system-announcement buttons), so it needs a Router.
const renderDock = () => render(<MemoryRouter><ChatDock /></MemoryRouter>)

beforeEach(() => {
  localStorage.clear()
  chatState.messages = []
  chatState.ownWallet = null
  chatState.canPost = false
  chatState.onlineUsers = []
  chatState.send = vi.fn()
  tipModalCalls.length = 0
  toasts.length = 0
  flags.tips = true
  busqueda.resultados = []
  busqueda.cargando = false
  busqueda.llamadas.length = 0
})

describe('ChatDock live drops', () => {
  // Recent Drops is hidden for now (kept in the code for future reuse) — the render-drops
  // tests below are skipped until it's re-enabled in ChatDock.
  it.skip('renders a drop row with the opener username', () => {
    addDrop({
      id: 'mint-1', name: 'Pikachu', valueUsd: 123.5, rarity: 'Rare',
      image: null, source: 'gacha', wallet: 'WalletABCDEF1234', username: 'neo',
      ts: Date.now(),
    })
    renderDock()
    expect(screen.getByText('Pikachu')).toBeTruthy()
    expect(screen.getByText('neo')).toBeTruthy()
  })

  it.skip('falls back to a short wallet when username is null', () => {
    addDrop({
      id: 'mint-2', name: 'Charizard', valueUsd: 999, rarity: 'Epic',
      image: null, source: 'gacha', wallet: 'So1anaAAAAAAAAAAAAAAZZZZ', username: null,
      ts: Date.now(),
    })
    renderDock()
    expect(screen.getByText('Charizard')).toBeTruthy()
    expect(screen.getByText('So1a…ZZZZ')).toBeTruthy()
  })

  // Regression: a drop with ts in epoch SECONDS (backend / legacy cache) must render
  // a sane relative time, not "~20608d ago" from treating seconds as milliseconds.
  it.skip('renders a seconds-epoch ts as a recent time, not decades ago', () => {
    addDrop({
      id: 'mint-secs', name: 'Mew', valueUsd: 50, rarity: 'Rare',
      image: null, source: 'gacha', wallet: 'WalletABCDEF1234', username: 'kai',
      ts: Math.floor(Date.now() / 1000), // seconds, like the backend emits
    })
    renderDock()
    expect(screen.getByText('Mew')).toBeTruthy()
    // no drop should render a decades-old age from misreading seconds as ms
    expect(screen.queryByText(/\d{3,}d ago/)).toBeNull()
  })

  // Regression: drops persisted before the global-drops change lack wallet/username.
  // ChatDock must render them (as 'anon') instead of crashing on userColor(undefined).
  it.skip('renders a legacy drop without wallet/username without crashing', () => {
    addDrop({
      id: 'mint-legacy', name: 'Squirtle', valueUsd: 10, rarity: 'Common',
      image: null, source: 'gacha', ts: Date.now(),
    } as any)
    renderDock()
    expect(screen.getByText('Squirtle')).toBeTruthy()
    expect(screen.getByText('anon')).toBeTruthy()
  })

  // Lobby v2: an Epic drop gets a "BIG PULL" badge next to its name — the badge is
  // driven by rarity, not value, so a low-value Epic still earns it.
  it.skip('shows a BIG PULL badge for an Epic drop (rarity-driven, not value)', () => {
    addDrop({
      id: 'mint-bigpull', name: 'Mewtwo', valueUsd: 200, rarity: 'Epic',
      image: null, source: 'gacha', wallet: 'WalletABCDEF1234', username: 'ash',
      ts: Date.now(),
    })
    renderDock()
    expect(screen.getByText('Mewtwo')).toBeTruthy()
    // The dropsStore accumulates in-memory across tests, so earlier Epics may also
    // carry the badge — assert at least one is present.
    expect(screen.getAllByText('BIG PULL').length).toBeGreaterThanOrEqual(1)
  })

  // System announcements (battle created / big hit / winner) render as a highlighted row,
  // and carry their action button when present.
  it('renders a system announcement with its action button', () => {
    chatState.messages = [{
      user: '📢 Arena', text: 'Nueva Pack Battle · entrada $50 USDC', ts: Date.now(),
      kind: 'system', action: { label: 'Unirse', battleId: 'b1', mode: 'pack' },
    }]
    renderDock()
    expect(screen.getByText('Nueva Pack Battle · entrada $50 USDC')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Unirse' })).toBeTruthy()
  })

  it('renders a battle-created event as "{creator} created a Pack Battle $50" + Join', () => {
    chatState.messages = [{
      user: 'prueba2', text: 'created a Pack Battle', ts: Date.now(),
      kind: 'system', event: 'created', amountUsd: 250, mode: 'pack',   // 250 → unique vs test drops
      action: { label: 'Join', battleId: 'b7', mode: 'pack' },
    }]
    renderDock()
    expect(screen.getByText('prueba2')).toBeTruthy()
    expect(screen.getByText(/created a Pack Battle/)).toBeTruthy()
    expect(screen.getByText('$250')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Join' })).toBeTruthy()
  })

  it('renders a system announcement without an action (no button)', () => {
    chatState.messages = [{
      user: '📢 Arena', text: '🔥 neo sacó Charizard · $300 (x6.0 la tirada)', ts: Date.now(),
      kind: 'system',
    }]
    renderDock()
    expect(screen.getByText(/neo sacó Charizard/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Unirse' })).toBeNull()
  })

  it('renders a big-hit event with the machine chip, player, name and gold value, no button', () => {
    chatState.messages = [{
      user: 'neo', text: 'pulled Charizard', ts: Date.now(),
      kind: 'system', event: 'hit', amountUsd: 320, machine: 'TCG Prime', mult: 10,
    }]
    renderDock()
    expect(screen.getByText('neo')).toBeTruthy()
    expect(screen.getByText(/pulled Charizard/)).toBeTruthy()
    expect(screen.getByText('$320')).toBeTruthy()
    expect(screen.getByText('TCG PRIME')).toBeTruthy()                       // machine the hit came from
    expect(screen.getByText('(x10)')).toBeTruthy()                           // hit multiple
    expect(screen.queryByRole('button', { name: /View|Join/ })).toBeNull()   // hits carry no action button
  })

  it('a hit with no machine falls back to a GACHA chip', () => {
    chatState.messages = [{ user: 'neo', text: 'pulled Charizard', ts: Date.now(), kind: 'system', event: 'hit', amountUsd: 320 }]
    renderDock()
    expect(screen.getByText('GACHA')).toBeTruthy()
  })

  it('renders a winner event like created: player + mode + gold value + multiplier + View button', () => {
    chatState.messages = [{
      user: 'mole', text: 'won a Pack Battle', ts: Date.now(),
      kind: 'system', event: 'winner', amountUsd: 1200, mode: 'pack', mult: 5,
      action: { label: 'View', battleId: 'b9', mode: 'pack' },
    }]
    renderDock()
    expect(screen.getByText('mole')).toBeTruthy()
    expect(screen.getByText(/won a Pack Battle/)).toBeTruthy()
    expect(screen.getByText('$1,200')).toBeTruthy()
    expect(screen.getByText(/\(x5\)/)).toBeTruthy()                     // take ÷ entry multiplier
    expect(screen.getByRole('button', { name: 'View' })).toBeTruthy()
  })
})

describe('ChatDock · perfiles clicables', () => {
  it('el nombre de quien habla lleva a su perfil', () => {
    chatState.messages = [{ user: 'Mauro', wallet: 'So1anaAAA111', text: 'hola', ts: 1 }]
    renderDock()
    const link = screen.getByRole('link', { name: 'Mauro' })
    expect(link.getAttribute('href')).toBe('/profile/So1anaAAA111')
  })

  it('un aviso SIN dueño no enlaza, pero sigue enseñando el nombre', () => {
    // El caso real: los avisos guardados antes de que existiera la columna `wallet`. Se pinta el
    // nombre igual —la línea tiene que seguir leyéndose— pero sin enlace, porque
    // `/profile/undefined` prometería un perfil que no existe.
    //
    // Con `event` a propósito: es la rama donde el nombre SÍ se pinta. Un aviso sin evento
    // enseña solo el texto, así que allí no habría nada que comprobar.
    chatState.messages = [{
      user: 'Battle Arena', text: 'won a Pack Battle', ts: 1,
      kind: 'system', event: 'winner', amountUsd: 500,
    }]
    renderDock()
    expect(screen.queryByRole('link', { name: 'Battle Arena' })).toBeNull()
    expect(screen.getByText('Battle Arena')).toBeTruthy()
  })

  it('un aviso CON dueño sí enlaza', () => {
    // "X ganó una Pack Battle" nombra a una persona, y esa persona tiene perfil.
    chatState.messages = [{
      user: 'Neo', wallet: 'So1anaBBB222', text: 'won a Pack Battle', ts: 1,
      kind: 'system', event: 'winner', amountUsd: 500,
    }]
    renderDock()
    expect(screen.getByRole('link', { name: 'Neo' }).getAttribute('href'))
      .toBe('/profile/So1anaBBB222')
  })

  it('la wallet va escapada', () => {
    chatState.messages = [{ user: 'X', wallet: 'a/b?c', text: 'hola', ts: 1 }]
    renderDock()
    expect(screen.getByRole('link', { name: 'X' }).getAttribute('href')).toBe('/profile/a%2Fb%3Fc')
  })
})

describe('ChatDock rank emblems and tags', () => {
  beforeEach(() => { for (const k of Object.keys(badges)) delete badges[k] })

  it('shows the emblem and tags of a ranked, tagged author, and keeps the link name', () => {
    badges['So1anaAAA111'] = { rank: 'gold', tags: ['TEAM'] }
    chatState.messages = [{ user: 'kairo', wallet: 'So1anaAAA111', text: 'hi', ts: 1 }]
    renderDock()
    expect(screen.getByRole('img', { name: 'Gold' })).toBeTruthy()
    expect(screen.getByText('TEAM')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'kairo' }).getAttribute('href')).toBe('/profile/So1anaAAA111')
  })

  it('shows neither on a message without a wallet', () => {
    chatState.messages = [{ user: 'House', text: 'hello', ts: 1 }]
    renderDock()
    expect(screen.queryByRole('img', { name: /bronze|silver|gold|platinum|diamond|obsidian/i })).toBeNull()
    expect(screen.queryByText('TEAM')).toBeNull()
  })
})

describe('ChatDock · el nombre de quien habla', () => {
  it('ya NO ofrece un botón de propina al lado de cada nombre', () => {
    // Se quitó por ruidoso: repetido en cada mensaje, en la parte de la pantalla donde menos
    // sitio sobra. La vía desde el chat es el comando `/tip`, que tiene sus propios tests.
    chatState.messages = [
      { user: 'Rival', wallet: 'WalletB', text: 'hola', ts: 1 },
      { user: 'Otro', wallet: 'WalletC', text: 'ey', ts: 2 },
    ]
    renderDock()
    expect(screen.queryByRole('button', { name: /tip/i })).toBeNull()
  })

  it('el nombre sigue enlazando al perfil', () => {
    // Lo que NO se quitó al quitar el botón: el enlace era lo otro que colgaba de `Autor`.
    chatState.messages = [{ user: 'Rival', wallet: 'WalletB', text: 'hola', ts: 1 }]
    renderDock()
    expect(screen.getByRole('link', { name: 'Rival' }).getAttribute('href')).toBe('/profile/WalletB')
  })
})

describe('ChatDock · menciones', () => {
  const conectados = [
    { wallet: 'WalletAAAA1111', name: 'ana' },
    { wallet: 'WalletBBBB2222', name: 'Bea' },
  ]

  function escribir(texto: string) {
    const campo = screen.getByPlaceholderText(/type a message/i)
    fireEvent.change(campo, { target: { value: texto } })
    return campo
  }

  it('escribir @ abre la lista de conectados', () => {
    chatState.canPost = true
    chatState.onlineUsers = conectados
    renderDock()
    escribir('hola @')
    expect(screen.getByRole('listbox')).toBeTruthy()
    expect(screen.getByText('ana')).toBeTruthy()
  })

  it('filtra según se escribe, por nombre', () => {
    chatState.canPost = true
    chatState.onlineUsers = conectados
    renderDock()
    escribir('hola @be')
    expect(screen.getByText('Bea')).toBeTruthy()
    expect(screen.queryByText('ana')).toBeNull()
  })

  it('sin sesión no ofrece mencionar a nadie', () => {
    // El campo está deshabilitado, pero además no tiene sentido ofrecer algo que no se puede usar.
    chatState.canPost = false
    chatState.onlineUsers = conectados
    renderDock()
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('al enviar, las menciones viajan resueltas a wallets', () => {
    chatState.canPost = true
    chatState.onlineUsers = conectados
    renderDock()
    // Con el espacio detrás la mención está CERRADA y la lista ya no está abierta, así que Enter
    // envía. Escrita a medias, Enter elegiría: ese es el test siguiente.
    const campo = escribir('gracias @ana ')
    fireEvent.keyDown(campo, { key: 'Enter' })
    expect(chatState.send).toHaveBeenCalledWith('gracias @ana ',
      [{ wallet: 'WalletAAAA1111', label: 'ana' }])
  })

  it('Enter con la lista abierta elige, NO envía', () => {
    // Si enviara, el mensaje saldría a medio escribir y sin la mención puesta.
    chatState.canPost = true
    chatState.onlineUsers = conectados
    renderDock()
    const campo = escribir('hola @an')
    fireEvent.keyDown(campo, { key: 'Enter' })
    expect(chatState.send).not.toHaveBeenCalled()
  })
})

describe('ChatDock · comandos', () => {
  const ana = { wallet: 'WalletANA1111', alias: 'ana', online: true }
  const bea = { wallet: 'WalletBEA2222', alias: 'bea', online: false }
  // `anabel` existe para que el test dorado distinga "exacto" de "por prefijo": es el fallo
  // realista (buscar "ana" y quedarse con el primero que EMPIECE por "ana"), y con una lista de
  // nombres que no se parecen entre sí no se nota.
  const anabel = { wallet: 'WalletANABEL33', alias: 'anabel', online: true }

  function escribir(texto: string) {
    const campo = screen.getByPlaceholderText(/type a message/i) as HTMLInputElement
    fireEvent.change(campo, { target: { value: texto } })
    return campo
  }

  it('escribir / abre la lista de comandos', () => {
    chatState.canPost = true
    renderDock()
    escribir('/')
    expect(screen.getByRole('listbox')).toBeTruthy()
    expect(screen.getByText('/tip')).toBeTruthy()
    expect(screen.getByText('Send USDC to another player')).toBeTruthy()
  })

  it('con las propinas apagadas, /tip no aparece', () => {
    // El comando lo apaga la MISMA bandera que el botón TIP de cada mensaje: si se ofreciera,
    // ejecutarlo acabaría en un 503 `tips_disabled` del backend.
    flags.tips = false
    chatState.canPost = true
    renderDock()
    escribir('/')
    expect(screen.queryByText('/tip')).toBeNull()
  })

  it('con las propinas apagadas, escribir / DICE que no hay comandos', () => {
    // `/tip` es hoy el único comando, así que con las propinas apagadas la lista queda vacía, y
    // una lista vacía es indistinguible de "los comandos están rotos". Tiene que decirlo.
    flags.tips = false
    chatState.canPost = true
    renderDock()
    escribir('/')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(screen.getByText(/no commands are available right now/i)).toBeTruthy()
  })

  it('en el primer argumento de /tip ofrece usuarios', () => {
    busqueda.resultados = [ana, bea]
    chatState.canPost = true
    renderDock()
    escribir('/tip an')
    expect(busqueda.llamadas[busqueda.llamadas.length - 1])
      .toMatchObject({ consulta: 'an', activo: true })
    expect(screen.getByRole('listbox')).toBeTruthy()
    expect(screen.getByText('ana')).toBeTruthy()
  })

  it('en el nombre del comando NO se molesta al servidor buscando jugadores', () => {
    // El freno de `useUserSearch` vive en su `activo`: escribir "/ti" no es buscar a nadie.
    chatState.canPost = true
    renderDock()
    escribir('/ti')
    expect(busqueda.llamadas.every((l) => l.activo === false)).toBe(true)
  })

  it('dentro de un comando, la @ no abre la lista de menciones', () => {
    // Comando y mención son EXCLUYENTES, y manda el comando porque abre el mensaje.
    chatState.canPost = true
    chatState.onlineUsers = [{ wallet: 'WalletZZZ', name: 'zoe' }]
    renderDock()
    escribir('/tip @z')
    expect(screen.queryByText('zoe')).toBeNull()
  })

  it('un texto que empieza por / NUNCA se envía como mensaje', () => {
    // Ni el comando desconocido, ni el válido: publicar "/tip ana 5" en la sala enseñaría a
    // quién y cuánto le da dinero el jugador, además de no hacer lo que pedía.
    busqueda.resultados = [ana]
    chatState.canPost = true
    renderDock()

    const campo = escribir('/loquesea hola')
    fireEvent.keyDown(campo, { key: 'Enter' })
    expect(chatState.send).not.toHaveBeenCalled()

    escribir('/tip ana 5')
    fireEvent.click(screen.getByRole('button', { name: '➤' }))    // también por el botón
    expect(chatState.send).not.toHaveBeenCalled()
  })

  it('Enter con la lista de comandos abierta elige, NO ejecuta ni envía', () => {
    chatState.canPost = true
    renderDock()
    const campo = escribir('/t')
    fireEvent.keyDown(campo, { key: 'Enter' })
    expect(campo.value).toBe('/tip ')
    expect(chatState.send).not.toHaveBeenCalled()
    // Y no se ha EJECUTADO "/t" por el camino: si Enter enviara además de elegir, el jugador
    // vería un "Unknown command" por completar el comando que la lista le estaba ofreciendo.
    expect(screen.queryByText(/unknown command/i)).toBeNull()
  })

  it('/tip ana 5 abre el modal con destinatario e importe', () => {
    // La lista está montada para que solo pase la coincidencia EXACTA. `bea` va la primera, así
    // que un cableado que coja `resultados[0]` manda el dinero a quien no es; y `anabel` va antes
    // que `ana`, así que resolver "por prefijo" (el fallo realista) también manda el dinero a
    // quien no es. Con nombres que no se parecen, las dos versiones rotas pasarían el test.
    busqueda.resultados = [bea, anabel, ana]
    chatState.canPost = true
    renderDock()
    const campo = escribir('/tip ana 5')
    fireEvent.keyDown(campo, { key: 'Enter' })

    expect(tipModalCalls.length).toBeGreaterThan(0)
    const ultima = tipModalCalls[tipModalCalls.length - 1]
    expect(ultima.open).toBe(true)
    expect(ultima.to.wallet).toBe(ana.wallet)
    expect(ultima.to.alias).toBe('ana')
    expect(ultima.amountInicial).toBe('5')
    expect(ultima.source).toBe('chat')
    expect(campo.value).toBe('')
  })

  it('un espacio delante NO convierte el comando en un mensaje público', () => {
    // " /tip ana 5" no empieza por barra, así que sin recortar se iría publicado en la sala
    // contando a quién y cuánto le da dinero este jugador. Está a un carácter: pegar el comando,
    // o el teclado del móvil.
    busqueda.resultados = [ana]
    chatState.canPost = true
    renderDock()
    const campo = escribir('  /tip ana 5')
    fireEvent.keyDown(campo, { key: 'Enter' })

    expect(chatState.send).not.toHaveBeenCalled()
    expect(tipModalCalls[tipModalCalls.length - 1].to.wallet).toBe(ana.wallet)
  })

  it('con un espacio delante, la lista de comandos también se abre', () => {
    chatState.canPost = true
    renderDock()
    escribir(' /')
    expect(screen.getByRole('listbox')).toBeTruthy()
    expect(screen.getByText('/tip')).toBeTruthy()
  })

  it('con las propinas ENCENDIDAS, un comando que no existe lo dice mientras se escribe', () => {
    // Mismo callejón que motivó la tarjeta de "no hay comandos": una lista vacía mientras escribes
    // no se distingue de que el autocompletado esté roto.
    chatState.canPost = true
    renderDock()
    escribir('/xyz')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(screen.getByText(/no command matches "\/xyz"/i)).toBeTruthy()
    expect(screen.getByText(/\/tip/)).toBeTruthy()          // y dice cuál sí hay
  })

  it('/tip con un usuario que no existe lo dice, no abre el modal y CONSERVA lo escrito', () => {
    busqueda.resultados = [bea]
    chatState.canPost = true
    renderDock()
    const campo = escribir('/tip fantasma 5')
    fireEvent.keyDown(campo, { key: 'Enter' })

    expect(tipModalCalls).toHaveLength(0)
    expect(screen.getByText(/no player found for "fantasma"/i)).toBeTruthy()
    expect(chatState.send).not.toHaveBeenCalled()
    // Un comando que falla casi siempre se arregla cambiando una palabra: borrarlo obliga a
    // reescribirlo entero.
    expect(campo.value).toBe('/tip fantasma 5')
  })

  it('si la búsqueda aún no ha contestado, no acusa al jugador de inventarse el nombre', () => {
    // El caso real: pegar `/tip ana 5` y pulsar Enter sin pausa. La búsqueda espera 250 ms, así
    // que todavía no hay resultados y el jugador no ha hecho nada mal. Además, repetir el Enter es
    // LO ÚNICO que lo arregla (a la segunda contesta la caché), así que el texto no puede
    // borrarse.
    busqueda.resultados = []
    busqueda.cargando = true
    chatState.canPost = true
    renderDock()
    const campo = escribir('/tip ana 5')
    fireEvent.keyDown(campo, { key: 'Enter' })

    expect(tipModalCalls).toHaveLength(0)
    expect(screen.queryByText(/no player found/i)).toBeNull()
    expect(screen.getByText(/still looking for "ana"/i)).toBeTruthy()
    expect(campo.value).toBe('/tip ana 5')
  })

  it('/tip con una cantidad que no es un número no abre el modal y lo dice', () => {
    // Abrir el modal con "mucho" dentro dejaría al jugador mirando un botón apagado sin motivo.
    busqueda.resultados = [ana]
    chatState.canPost = true
    renderDock()
    const campo = escribir('/tip ana mucho')
    fireEvent.keyDown(campo, { key: 'Enter' })

    expect(tipModalCalls).toHaveLength(0)
    expect(screen.getByText(/not a valid amount/i)).toBeTruthy()
  })

  it('un comando desconocido responde sin llamar al servidor', () => {
    chatState.canPost = true
    renderDock()
    const campo = escribir('/roll 20')
    fireEvent.keyDown(campo, { key: 'Enter' })

    expect(chatState.send).not.toHaveBeenCalled()
    expect(screen.getByText(/unknown command "\/roll"/i)).toBeTruthy()
  })
})

describe('ChatDock · aviso de mención', () => {
  const mio = 'WalletMIA'

  it('avisa si te mencionan con el panel plegado', () => {
    chatState.ownWallet = mio
    chatState.messages = [{ user: 'Ana', wallet: 'WalletA', text: 'hola @yo', ts: 5,
                            mentions: [{ wallet: mio, label: 'yo' }] }]
    render(<MemoryRouter><ChatDock collapsed onToggle={vi.fn()} /></MemoryRouter>)
    expect(toasts.some((t) => /mentioned you/i.test(t))).toBe(true)
  })

  it('NO avisa si estás mirando el chat al final: el mensaje ya se destaca solo', () => {
    chatState.ownWallet = mio
    chatState.messages = [{ user: 'Ana', wallet: 'WalletA', text: 'hola @yo', ts: 5,
                            mentions: [{ wallet: mio, label: 'yo' }] }]
    renderDock()
    expect(toasts.some((t) => /mentioned you/i.test(t))).toBe(false)
  })

  it('no te avisa de tus propias menciones', () => {
    chatState.ownWallet = mio
    chatState.messages = [{ user: 'Yo', wallet: mio, text: '@yo mismo', ts: 5,
                            mentions: [{ wallet: mio, label: 'yo' }] }]
    render(<MemoryRouter><ChatDock collapsed onToggle={vi.fn()} /></MemoryRouter>)
    expect(toasts.some((t) => /mentioned you/i.test(t))).toBe(false)
  })
})
