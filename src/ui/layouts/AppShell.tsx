import { useState, useEffect, useRef } from 'react'
import { battleHref } from '../battle/battleHref'
import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { usePrivy, useIdentityToken } from '@privy-io/react-auth'
import { COLORS, GRADIENT, FONTS, formatUsd, Z } from '../theme'
import { fmtGimmighouls, fmtGimmighoulsCompacto } from '../gimmighouls'
import { useUsdcBalance } from '../../wallet/useUsdcBalance'
import { useReservedBalance, availableUsd } from '../../wallet/useReservedBalance'
import { useProfile } from '../../hooks/useProfile'
import { useReducedMotion } from '../useReducedMotion'
import { useIsWide } from '../useIsWide'
import { AuthButtons } from '../components/AuthButtons'
import { RadioPlayer } from '../components/RadioPlayer'
import { MobileRadioBar } from '../components/MobileRadioBar'
import { useRadio } from '../radio/useRadio'

// 🔇 Radio en pausa. El componente, el store y sus tests siguen en su sitio: esto solo deja de
// montarlo, así que no suena nada ni aparece el botón. Para recuperarla, `true` y ya.
const RADIO_ENABLED = false
import { DepositModal } from '../components/DepositModal'
import { LeftRail, NAV_ICONS } from '../screens/Hub/LeftRail'
import { ChatDock } from '../screens/Hub/ChatDock'
import { useChat } from '../../hooks/useChat'
import { RematchToastHost } from '../components/RematchToast'
import { BattleAlertsHost } from '../components/BattleAlertsHost'
import { TipAlertsHost } from '../components/TipAlertsHost'
import { OnboardingTutorial } from '../components/OnboardingTutorial'
import { NAV_ITEMS, type HubNav } from '../screens/Hub/hubMockData'
import { useKeyboardInset } from '../useKeyboardInset'
import { NAV_ROUTES, activeNavFromPath } from './navRoutes'
import { Toaster } from '../toast'
import { UnseenModal } from '../components/UnseenModal'
import { YoloSummaryOverlay } from '../screens/gacha/GachaVault'
import { pendingPacksToResults, type YoloResult } from '../screens/gacha/pendingToResult'
import { fetchPendingPacks, markPacksRevealed, type PendingPack } from '../../onchain/gachaClient'
import { fetchUnseenBattles, markBattlesSeen, type UnseenBattle } from '../../onchain/packBattleClient'
import { useUnseenBattlesVersion } from '../screens/battle/unseenBattlesBus'
import { holdBalance } from '../../wallet/balanceHold'
import { usePendingPacksVersion } from '../screens/gacha/pendingPacksBus'

const DOCK_KEY = 'ba.dockCollapsed'
const ONBOARD_KEY = 'ba.onboarded'   // set once the first-visit tutorial is finished/skipped

export function AppShell() {
  const { pathname } = useLocation()
  const reducedMotion = useReducedMotion()
  const { usdc } = useUsdcBalance()
  const { reserved, locked } = useReservedBalance()
  const { gimmighouls } = useProfile()
  const { authenticated } = usePrivy()

  // Breakpoints copied verbatim from Hub.tsx
  const wideRail = useIsWide('(min-width: 760px)')
  const wideDock = useIsWide('(min-width: 1100px)')

  // ── Sobres pagados y sin abrir ─────────────────────────────────────────────
  // Vive aquí y no en el gacha porque el jugador puede acabar en cualquier sección con sobres
  // pendientes: abrió otra pestaña a mitad de una tirada, o cerró la página antes de revelar.
  // Abrirlos sí ocurre en el gacha —es donde vive el reveal—, así que el modal lleva allí.
  const navigate = useNavigate()
  const { identityToken } = useIdentityToken()
  const [pendingPacks, setPendingPacks] = useState<PendingPack[]>([])
  const [pendingOpen, setPendingOpen] = useState(false)
  // Sobres por los que ya se preguntó. La lista se refresca al cambiar de ruta, y un sobre sigue
  // pendiente hasta que el jugador lo VE: sin esto, ir al gacha a abrirlo volvía a levantar el
  // modal encima, obligando a cerrarlo dos veces. Solo se auto-abre si aparece alguno nuevo.
  const promptedRef = useRef<Set<string>>(new Set())
  const pendingVersion = usePendingPacksVersion()
  const [unseenBattles, setUnseenBattles] = useState<UnseenBattle[]>([])
  const battlesVersion = useUnseenBattlesVersion()

  useEffect(() => {
    if (!identityToken) { setPendingPacks([]); setUnseenBattles([]); setPendingOpen(false); return }
    let cancelled = false
    // Un ítem sigue pendiente hasta que el jugador lo VE, así que la lista se refresca al cambiar
    // de ruta o cuando un reveal avisa. El modal solo se AUTO-abre si aparece algo nuevo (por
    // memo o battle_id): sin esto, ir a verlo levantaría el modal encima al volver.
    Promise.allSettled([fetchPendingPacks(identityToken), fetchUnseenBattles(identityToken)])
      .then(([pr, br]) => {
        if (cancelled) return
        const ps = pr.status === 'fulfilled' ? pr.value : []
        const bs = br.status === 'fulfilled' ? br.value : []
        setPendingPacks(ps)
        setUnseenBattles(bs)
        const freshIds = [
          ...ps.map((p) => `pack:${p.memo}`),
          ...bs.map((b) => `battle:${b.battle_id}`),
        ].filter((k) => !promptedRef.current.has(k))
        if (freshIds.length > 0) {
          [...ps.map((p) => `pack:${p.memo}`), ...bs.map((b) => `battle:${b.battle_id}`)]
            .forEach((k) => promptedRef.current.add(k))
          setPendingOpen(true)
        }
      })
    return () => { cancelled = true }
  }, [identityToken, pathname, pendingVersion, battlesVersion])

  // El saldo se congela mientras QUEDEN sobres sin abrir, no solo mientras el modal esté a la
  // vista. Antes se soltaba al pulsar Open —el modal se cerraba para navegar al gacha— y el saldo
  // se pintaba justo en el hueco previo a abrir, que es cuando más delata: con turbo el
  // auto-buyback ya movió el USDC. Se suelta cuando la lista queda vacía, es decir, cuando el
  // jugador ya ha visto sus cartas.
  // Se congela el saldo mientras QUEDE algo sin ver: un sobre por abrir (el turbo ya movió el
  // USDC) o una batalla sin ver (ganar acredita el botín antes de que lo veas). Se suelta cuando
  // ambas listas quedan vacías, es decir cuando ya vio todo.
  // …pero SOLO por lo que puede destripar algo. Un reembolso (anulada o cancelada por el creador)
  // devuelve la entrada y no tiene resultado que revelar, así que congelar el saldo por él no
  // protege nada: solo deja un "—" donde debería ir el dinero, y encima al recargar, porque en
  // frío el saldo ni llega a cargarse.
  const hasSpoiler = pendingPacks.length > 0 || unseenBattles.some((b) => b.status === 'settled')
  useEffect(() => {
    if (!hasSpoiler) return
    return holdBalance()
  }, [hasSpoiler])

  function goBattle(b: UnseenBattle, straightToResult: boolean) {
    setPendingOpen(false)
    navigate(battleHref(b.battle_id, { view: straightToResult ? 'result' : 'reveal' }))
  }

  function goOpenPending(packs: PendingPack[]) {
    setPendingOpen(false)
    navigate('/play/gacha', { state: { openPacks: packs } })
  }

  // Skip: el jugador renuncia a la animación, no al resultado. Se resuelve lo que falte por
  // resolver, se enseña qué tocó en texto y se marcan como vistos — que es lo que descongela el
  // saldo. Enseñar y descongelar en el MISMO paso es lo que evita que el número subiendo sea el
  // spoiler de algo que nunca llegó a verse.
  const [skipSummary, setSkipSummary] = useState<{ results: YoloResult[]; machineCodes: string[] } | null>(null)
  const [pendingBusy, setPendingBusy] = useState(false)

  async function skipPending() {
    if (!identityToken || pendingPacks.length === 0) return
    setPendingBusy(true)
    const summary = await pendingPacksToResults(identityToken, pendingPacks)
    try {
      await markPacksRevealed(identityToken, pendingPacks.map((x) => x.memo))
    } catch { /* se reintenta al recargar */ }
    setPendingBusy(false)
    setSkipSummary(summary)
    setPendingPacks([])          // suelta la congelación: ya sabe lo que le tocó
  }

  // Salida en bloque de las batallas: el modal ya enseña el resultado de cada una en su fila, así
  // que entrar en ellas es opcional. Marcarlas vistas es lo que las quita de la lista y, si no
  // quedan sobres, suelta la congelación del saldo.
  async function seeAllBattles() {
    if (!identityToken || unseenBattles.length === 0) return
    setPendingBusy(true)
    try {
      await markBattlesSeen(identityToken, unseenBattles.map((b) => b.battle_id))
    } catch { /* se reintenta: siguen sin ver al recargar */ }
    setPendingBusy(false)
    setUnseenBattles([])
  }

  function closePending() {
    setPendingOpen(false)
    setSkipSummary(null)
  }

  const [depositOpen, setDepositOpen] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  // Alto del teclado del móvil: 0 mientras esté cerrado y en escritorio.
  const kbInset = useKeyboardInset()
  const [dockCollapsed, setDockCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(DOCK_KEY) === '1' } catch { return false }
  })

  // Radio is a singleton store — subscribing here only tells us whether the
  // mobile mini-player (bottom stack) will render, to size the content padding.
  const { tracks: radioTracks, collapsed: radioCollapsed, tryAutoplay } = useRadio()

  // Arranca la música al entrar, salvo que el usuario la parara a mano alguna vez en este
  // navegador. tryAutoplay ya contempla que el navegador BLOQUEE el autoplay sin gesto previo:
  // en ese caso deja armado un disparo al primer click o tecla, así que suena igualmente en
  // cuanto el usuario toca algo. Y comprueba la preferencia por dentro.
  useEffect(() => { if (RADIO_ENABLED) tryAutoplay() }, [tryAutoplay])
  const mobileRadio = RADIO_ENABLED && !wideRail && radioTracks.length > 0
  // When the mini-player is collapsed only the floating re-open button shows, so
  // the content only needs to clear the nav (not the full radio bar).
  const mobileRadioBar = mobileRadio && !radioCollapsed

  // First-visit onboarding — show the guided tour once per browser.
  const [showOnboarding, setShowOnboarding] = useState<boolean>(() => {
    try { return localStorage.getItem(ONBOARD_KEY) !== '1' } catch { return false }
  })
  function dismissOnboarding() {
    try { localStorage.setItem(ONBOARD_KEY, '1') } catch { /* ignore */ }
    setShowOnboarding(false)
  }

  function toggleDock() {
    setDockCollapsed((c) => {
      const next = !c
      try { localStorage.setItem(DOCK_KEY, next ? '1' : '0') } catch { /* ignore */ }
      return next
    })
  }

  // ── Mobile chat unread dot ─────────────────────────────────────────────────
  // On mobile the chat is only mounted while open, so keep a persistent connection here to know
  // when a new message arrives while it's closed. Mark everything seen when the chat is open.
  const { messages: chatMessages } = useChat(!wideRail)
  const [seenChat, setSeenChat] = useState(0)
  const seenInit = useRef(false)
  useEffect(() => {
    if (!seenInit.current && chatMessages.length > 0) { seenInit.current = true; setSeenChat(chatMessages.length) }
  }, [chatMessages.length])
  useEffect(() => {
    if (chatOpen) setSeenChat(chatMessages.length)   // viewing the chat clears the badge
  }, [chatOpen, chatMessages.length])
  const chatUnread = !wideRail && !chatOpen && chatMessages.length > seenChat

  const active: HubNav = activeNavFromPath(pathname) ?? 'lobby'

  // ── Grid columns — mirrors Hub.tsx logic + collapsible dock column ──────────
  const showDock = wideRail && wideDock
  const dockCol = showDock ? (dockCollapsed ? '36px' : '320px') : ''
  let gridCols: string
  if (wideRail && wideDock)  gridCols = `92px 1fr ${dockCol}`
  else if (wideRail)         gridCols = '92px 1fr'
  else                       gridCols = '1fr'

  // ── Grid placement — the topbar (row 1) spans the full content width so it sits
  //    over the dock and pushes Recent Drops (row 2) down. Rail spans both rows.
  const contentStart = wideRail ? 2 : 1
  const totalCols = showDock ? 3 : (wideRail ? 2 : 1)
  const headerColumn = `${contentStart} / ${totalCols + 1}`
  const contentColumn = `${contentStart} / ${contentStart + 1}`

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: gridCols,
        gridTemplateRows: 'auto minmax(0, 1fr)', // row 1 = full-width topbar, row 2 = content + dock (capped so inner overflow:auto engages)
        height: '100dvh',
        // Ambient colour wash (from the mockup): magenta + cyan + gold radials spread across the whole
        // viewport so the background reads coloured, not black. The shell is 100dvh so it stays put.
        background: 'radial-gradient(900px 620px at 12% 0%,rgba(255,46,151,.28),transparent 62%),radial-gradient(840px 580px at 92% 4%,rgba(0,255,196,.18),transparent 60%),radial-gradient(820px 660px at 82% 56%,rgba(52,211,224,.17),transparent 62%),radial-gradient(900px 680px at 4% 92%,rgba(255,46,151,.22),transparent 62%),radial-gradient(760px 560px at 56% 116%,rgba(245,197,66,.11),transparent 62%),#0a0710',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* ── LEFT RAIL (desktop/tablet) o BOTTOM NAV (móvil) ───────────────── */}
      {wideRail ? (
        <div style={{ gridColumn: '1 / 2', gridRow: '1 / 3', minHeight: 0 }}>
          <LeftRail active={active} />
        </div>
      ) : (
        // Mobile bottom stack: radio mini-player floating above the tab bar.
        <div style={{ position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 50 }}>
          {RADIO_ENABLED && <MobileRadioBar />}
          <BottomNav
            active={active}
            onNavigate={() => setChatOpen(false)}
            onChat={() => setChatOpen((o) => !o)}
            chatActive={chatOpen}
            chatUnread={chatUnread}
          />
        </div>
      )}

        {/* Global topbar (row 1) — spans the full content width, over the dock, so Recent Drops sits below it */}
        <header
          style={{
            gridColumn: headerColumn,
            gridRow: '1 / 2',
            // Topbar sits above page content so its dropdowns (profile menu, radio) overflow ON TOP.
            // Grid siblings paint in DOM order, and <main> comes after — without this the menu
            // renders under the content. Kept below the chat drawer / modals (z ≥ 100).
            position: 'relative',
            zIndex: Z.chrome,
            display: 'flex',
            alignItems: 'center',
            flexWrap: 'wrap',
            rowGap: 8,
            gap: wideRail ? 14 : 10,
            padding: wideRail ? '12px 18px' : '12px 14px',
            borderBottom: '1px solid rgba(255,255,255,.07)',
            background: wideRail ? 'transparent' : 'rgba(8,10,14,.9)',
            backdropFilter: wideRail ? undefined : 'blur(14px)',
            WebkitBackdropFilter: wideRail ? undefined : 'blur(14px)',
          }}
        >
          {/* Brand — "Collector Arena" wordmark on desktop; logo dot on mobile (no rail there) */}
          {wideRail ? (
            <span style={{ fontFamily: FONTS.display, fontWeight: 700, fontSize: 19, letterSpacing: '-.01em', whiteSpace: 'nowrap' }}>
              Collector <span style={{ color: COLORS.green }}>Arena</span>
            </span>
          ) : (
            <img
              src="/logo-rail.png"
              alt="Collector Arena"
              width={32}
              height={32}
              style={{ flex: 'none', objectFit: 'contain', display: 'block' }}
            />
          )}

          {/* Spacer */}
          <div style={{ flex: 1 }} />

          {/* Radio — global player in the header on desktop; on mobile it lives in the
              bottom stack (MobileRadioBar). The store is a singleton, so audio survives
              navigation and breakpoint changes either way. */}
          {RADIO_ENABLED && wideRail && <RadioPlayer />}

          {/* Balance + Gimmighouls — labelled groups on desktop; one divided box on mobile */}
          {authenticated && (wideRail ? (
            // Desktop: USDC + Gimmighouls in ONE pill — image left (full height) + label above number.
            <div style={{ display: 'flex', alignItems: 'stretch', background: '#11161f', border: `1px solid ${COLORS.border}`, borderRadius: 13, overflow: 'hidden' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 15px' }}>
                <img src="/usdc.svg" alt="" style={{ height: 20, width: 'auto', display: 'block' }} />
                <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', lineHeight: 1.1 }}>
                  <span style={{ fontFamily: FONTS.mono, fontSize: 8.5, fontWeight: 700, letterSpacing: '.18em', color: COLORS.muted, paddingBottom: '1px' }}>USDC</span>
                  <span style={{ fontFamily: FONTS.display, fontWeight: 800, fontSize: 15 }}>
                    {availableUsd(usdc, reserved) != null ? formatUsd(availableUsd(usdc, reserved)!) : '—'}
                  </span>
                  {locked != null && locked > 0 && (
                    <span style={{ fontSize: 9, color: COLORS.muted }}>{formatUsd(locked)} reserved</span>
                  )}
                </div>
              </div>
              <span style={{ width: 1, background: COLORS.border }} />
              <div style={{ display: 'flex', alignItems: 'center', gap: 1, padding: '6px 15px' }} title="Gimmighouls">
                <img src="/gimmighoul.png" alt="" style={{ height: 23, width: 'auto', display: 'block' }} />
                <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', lineHeight: 1.1 }}>
                  <span style={{ fontFamily: FONTS.mono, fontSize: 8.5, fontWeight: 700, letterSpacing: '.18em', color: COLORS.muted, paddingBottom: '1px' }}>GIMMIGHOULS</span>
                  <span style={{ fontFamily: FONTS.display, fontWeight: 800, fontSize: 15 }}>
                    {gimmighouls != null ? fmtGimmighouls(gimmighouls) : '—'}
                  </span>
                </div>
              </div>
            </div>
          ) : (
            // Mobile: one pill — USDC | Gimmighouls | compact "+" deposit square.
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '6px 6px 6px 12px', borderRadius: 11, background: 'rgba(255,255,255,.05)', border: `1px solid ${COLORS.border}` }}>
              <img src="/usdc.svg" alt="" width={15} height={15} style={{ display: 'block' }} />
              <span style={{ fontSize: 13, fontWeight: 700 }}>
                {availableUsd(usdc, reserved) != null ? formatUsd(availableUsd(usdc, reserved)!) : '—'}
              </span>
              <span style={{ width: 1, height: 12, background: 'rgba(255,255,255,.12)' }} />
              <img src="/gimmighoul.png" alt="" title="Gimmighouls" width={15} height={15} style={{ display: 'block' }} />
              <span style={{ fontSize: 13, fontWeight: 700 }}>
                {gimmighouls != null ? fmtGimmighoulsCompacto(gimmighouls) : '—'}
              </span>
              <button
                onClick={() => setDepositOpen(true)}
                title="Deposit"
                aria-label="Deposit"
                style={{ width: 26, height: 26, borderRadius: 8, border: 0, cursor: 'pointer', fontSize: 15, fontWeight: 700, color: '#06170f', background: 'linear-gradient(135deg,#3df0a0,#13c98a)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 0 }}
              >
                +
              </button>
            </div>
          ))}

          {/* Deposit (desktop — mobile has the compact "+" inside the balance pill) */}
          {authenticated && wideRail && (
            <button
              onClick={() => setDepositOpen(true)}
              style={{
                background: GRADIENT,
                border: 'none',
                borderRadius: 10,
                padding: '9px 16px',
                color: '#06120c',
                fontWeight: 800,
                fontSize: 13,
                fontFamily: FONTS.display,
                cursor: 'pointer',
                whiteSpace: 'nowrap',
                boxShadow: '0 0 18px -6px rgba(0,255,196,.7)',
              }}
            >
              + Deposit
            </button>
          )}

          {/* Account pill */}
          <AuthButtons variant="compact" />

        </header>

        {/* Routed page content (row 2, content column) */}
        <main
          style={{
            gridColumn: contentColumn,
            gridRow: '2 / 3',
            minWidth: 0,
            minHeight: 0,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            paddingBottom: wideRail ? 0 : mobileRadioBar ? 128 : 72, // space for the mobile bottom stack (nav + optional radio bar)
          }}
        >
          {/* Mobile: Live Drops strip at the top of the scroll (scrolls away on scroll down) */}
          {/* Recent Drops strip removed from all screens (component kept: LiveDropsStrip.tsx).
              To restore on mobile: import it and render `{!wideRail && <LiveDropsStrip />}` here. */}
          <Outlet />
        </main>

      {/* ── CHAT DOCK (row 2, right column — desktop) ─────────────────────── */}
      {showDock && (
        <div style={{ gridColumn: '3 / 4', gridRow: '2 / 3', minHeight: 0 }}>
          <ChatDock collapsed={dockCollapsed} onToggle={toggleDock} />
        </div>
      )}

      {/* ── FLOATING CHAT BUTTON (tablet only — mobile opens chat from the bottom nav) ── */}
      {wideRail && !wideDock && (
        <button
          onClick={() => setChatOpen((o) => !o)}
          title="Chat"
          style={{
            position: 'fixed',
            bottom: 24,
            right: 20,
            width: 48,
            height: 48,
            borderRadius: '50%',
            background: GRADIENT,
            border: 'none',
            color: '#06120c',
            fontSize: 20,
            cursor: 'pointer',
            zIndex: 100,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 4px 16px #ff2e9755',
          }}
        >
          💬
        </button>
      )}

      {/* ── DEPOSIT MODAL ─────────────────────────────────────────────────── */}
      <DepositModal open={depositOpen} onClose={() => setDepositOpen(false)} />

      {/* ── TOASTS ────────────────────────────────────────────────────────── */}
      {/* Los toasts van abajo, por encima de la pila móvil (nav + barra de radio si está) y
          dejando hueco al RematchToast, que vive en bottom 28/84 con z-index menor. */}
      {pendingOpen && !skipSummary && (pendingPacks.length > 0 || unseenBattles.length > 0) && (
        <UnseenModal
          packs={pendingPacks}
          battles={unseenBattles}
          busy={pendingBusy}
          onOpenAllPacks={() => goOpenPending(pendingPacks)}
          onSkipPacks={() => void skipPending()}
          onWatchBattle={(b) => goBattle(b, false)}
          onResultBattle={(b) => goBattle(b, true)}
          onSeeAllBattles={() => void seeAllBattles()}
        />
      )}

      {/* Tras el Skip se enseña el MISMO resumen que al abrir varios sobres, con la máquina de
          cada carta etiquetada: un lote de pendientes puede mezclar máquinas. */}
      {skipSummary && (
        <YoloSummaryOverlay
          results={skipSummary.results}
          machineCodes={skipSummary.machineCodes}
          buybackPct={null}
          onClose={closePending}
        />
      )}

      <Toaster bottomOffset={wideRail ? 24 : mobileRadioBar ? 148 : 92} />
      <RematchToastHost />   {/* app-wide rematch challenge toast (bottom-centre) */}
      <BattleAlertsHost />   {/* app-wide join / battle-starting toasts */}
      <TipAlertsHost />      {/* aviso al que recibe una propina; solo le llega a él */}

      {/* ── ONBOARDING — first-visit guided tour ──────────────────────────── */}
      {showOnboarding && <OnboardingTutorial onClose={dismissOnboarding} reducedMotion={reducedMotion} />}

      {/* ── CHAT — tablet: side drawer (full dock) · mobile: full-screen chat-only over the nav ── */}
      {chatOpen && !(wideRail && wideDock) && (wideRail ? (
        <>
          <div onClick={() => setChatOpen(false)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 110 }} />
          <div style={{
            position: 'fixed', top: 0, right: 0, width: 'min(340px, 100vw)', height: '100vh', zIndex: 120, overflowY: 'auto',
            transition: reducedMotion ? 'none' : 'transform 0.22s cubic-bezier(0.4,0,0.2,1)',
          }}>
            <ChatDock />
          </div>
        </>
      ) : (
        // Mobile: chat only, full screen except the bottom nav.
        // `bottom` sube con el teclado. Sin esto la casilla de texto queda DEBAJO de él —el
        // teclado no encoge el viewport de maquetación— y había que hacer scroll para leer lo
        // que uno estaba escribiendo. Con el teclado cerrado el inset es 0 y esto no hace nada.
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 60 + kbInset, zIndex: 120, background: '#0c1019', display: 'flex', flexDirection: 'column' }}>
          <ChatDock chatOnly />
        </div>
      ))}
    </div>
  )
}

// ─── Bottom navigation bar (móvil) — mirrors Hub.tsx BottomNav verbatim ──────
function BottomNav({
  active,
  onNavigate,
  onChat,
  chatActive,
  chatUnread,
}: {
  active: HubNav
  onNavigate: () => void
  onChat: () => void
  chatActive: boolean
  chatUnread: boolean
}) {
  const INACTIVE = '#5c6675'
  const btn = {
    display: 'flex', flexDirection: 'column' as const, alignItems: 'center', gap: 3,
    background: 'transparent', border: 'none', cursor: 'pointer',
    padding: 0, fontFamily: FONTS.body, flex: '1 1 0', minWidth: 0, lineHeight: 1.5,
  }
  return (
    <nav
      style={{
        background: 'rgba(8,10,14,.95)',
        backdropFilter: 'blur(14px)',
        WebkitBackdropFilter: 'blur(14px)',
        borderTop: '1px solid rgba(255,255,255,.07)',
        display: 'flex',
        alignItems: 'center',
        padding: '9px 2px 12px',
      }}
    >
      {NAV_ITEMS.map((item) => {
        const isActive = !chatActive && item.id === active
        const color = isActive ? COLORS.green : INACTIVE
        return (
          <Link key={item.id} to={NAV_ROUTES[item.id]} onClick={onNavigate} title={item.label}
            style={{ ...btn, textDecoration: 'none', color }}>
            {NAV_ICONS[item.id]}
            <span style={{ fontSize: 9.5, fontWeight: isActive ? 600 : 400 }}>{item.label}</span>
          </Link>
        )
      })}
      {/* Chat lives in the nav on mobile (no floating button) */}
      <button onClick={onChat} title="Chat" style={{ ...btn, color: chatActive ? COLORS.green : INACTIVE }}>
        <span style={{ position: 'relative', display: 'inline-flex' }}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" /></svg>
          {chatUnread && (
            <span aria-label="Unread messages" style={{ position: 'absolute', top: -3, right: -4, width: 9, height: 9, borderRadius: '50%', background: COLORS.red, border: '2px solid rgba(8,10,14,.95)', boxShadow: `0 0 6px ${COLORS.red}` }} />
          )}
        </span>
        <span style={{ fontSize: 9.5, fontWeight: chatActive ? 600 : 400 }}>Chat</span>
      </button>
    </nav>
  )
}
