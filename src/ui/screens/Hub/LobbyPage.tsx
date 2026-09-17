import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { battleHref } from '../../battle/battleHref'
import { useNavigate } from 'react-router-dom'
import { useIdentityToken } from '@privy-io/react-auth'
import type { LiveBattle } from './hubMockData'
import { QuickMatch } from './QuickMatch'
import { LiveBattles } from './LiveBattles'
import { ModeGuide } from './ModeGuide'
import { RoyaleDemoNotice } from './RoyaleDemoNotice'
import { showToast } from '../../toastBus'
import { useBattles } from '../../../onchain/useBattles'
import { openBattleToLive } from './openBattleToLive'
import { joinBattle, cancelBattle } from '../../../onchain/packBattleClient'
import { useEmbeddedSolanaAddress } from '../../../wallet/embedded'
import { useDelegationGate } from '../../components/useDelegationGate'
import { DelegationGate } from '../../components/DelegationGate'
import { CreateBattleModal } from './CreateBattleModal'
import { DemoPicker } from './DemoPicker'
import { loadMachineList } from '../../useMachines'
import { canCreateRoyale } from '../../../onchain/config'
import { leerModos, paramModos } from './lobbyFilter'
import { ClaimBanner } from './ClaimBanner'

/**
 * El Lobby: TODAS las partidas abiertas, con el modo como filtro.
 *
 * Antes eran dos páginas, /play/arena y /play/royale, que renderizaban esto mismo con un prop
 * distinto. Se unieron por la liquidez: con pocos jugadores a la vez, partir la lista en dos hace
 * que cada mitad parezca vacía y el juego muerto. Ver `lobbyFilter`.
 *
 * Las LISTAS se mezclan, las TARJETAS no: cada modo conserva la suya (Royale la ancha, Pack la
 * rejilla compacta) bajo su propio encabezado, porque tienen reglas y riesgo distintos y una card
 * que no grita de qué modo es sería peor que tenerlas separadas.
 */
export function LobbyPage() {
  const [params, setParams] = useSearchParams()
  const modos = leerModos(params.toString() ? `?${params.toString()}` : '')
  const navigate = useNavigate()
  const { identityToken } = useIdentityToken()
  const meWallet = useEmbeddedSolanaAddress()
  const { battles } = useBattles()
  const gate = useDelegationGate()
  const [createOpen, setCreateOpen] = useState(false)
  const [demoOpen, setDemoOpen] = useState(false)

  // Warm the machine catalogue so Create Battle opens with machines ready.
  useEffect(() => { void loadMachineList() }, [])


  // Se le pasan TODAS a LiveBattles: el filtro de modo lo aplica él, que es quien tiene el
  // desplegable. Aquí solo se decide qué entra en el lobby y qué va a "Recent".
  const enLobby = battles.map((b) => openBattleToLive(b, meWallet))
  // Lobbies Y partidas en curso: una royale arrancada desaparecía de la sección entera —también
  // para quien la estaba jugando—, y solo se volvía a ver en el modal de "te las perdiste". Las
  // terminadas sí se quedan fuera: para esas está la tarjeta de recap. La card ya distingue el
  // caso (`action: join | watch` y el estado "Live" en rojo).


  // Los fallos de estas acciones van SIEMPRE por toast. El aviso nace de pulsar un botón de una
  // card, y un banner sobre la lista deja el mensaje lejos de lo que lo provocó — y encima
  // desplaza las cards al aparecer.
  const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

  function onCancel(b: LiveBattle) {
    if (!identityToken) { showToast('Sign in to cancel'); return }
    cancelBattle(identityToken, b.id).catch((e) => showToast(errMsg(e)))
  }

  // Replay = la ruta SIN ?view=result, que es lo que deja correr el reveal otra vez.
  const onReplay = (b: LiveBattle) => navigate(battleHref(b.id, { view: 'reveal' }))

  function onBattleAction(b: LiveBattle) {
    // 'watch' cubre tanto Watch (en juego) como Result (liquidada): battleHref las separa.
    if (b.action === 'watch') { navigate(battleHref(b.id, { status: b.battleStatus })); return }
    if (!identityToken) { showToast('Sign in to join'); return }
    gate.requireDelegation(async () => {
      try {
        await joinBattle(identityToken, b.id)
        navigate('/play/battle/' + b.id)
      } catch (e) {
        showToast(errMsg(e))   // p. ej. fondos insuficientes, o alguien llenó el hueco antes
      }
    })
  }

  return (
    <div style={{ padding: '24px clamp(14px,2.4vw,28px) 44px', display: 'flex', flexDirection: 'column', gap: 26 }}>
      {/* Por encima incluso de la guía: es lo único de esta página que CADUCA. Cuando CC cierre
          la bóveda deja de tener sentido, y hasta entonces el que tiene tokens sin reclamar
          quiere enterarse antes de ponerse a mirar partidas. Se descarta y no vuelve. */}
      <ClaimBanner />

      {/* Arriba del todo: es lo que contesta "¿qué es esto?", y esa pregunta llega antes que
          cualquier otra. Plegable y con memoria, así que quien ya lo sabe la cierra una vez y le
          queda una sola línea. */}
      <ModeGuide />

      {/* SIEMPRE, con cualquier filtro y se haya visto o no. El vídeo tiene que estar localizable
          en todo momento: el que vuelve semanas después a decidir si entra a una Royale de 250 $
          quiere repasarlo, y mandarle a buscarlo por el Help es perderlo. Por eso tampoco se
          esconde filtrando solo Pack Battle. */}
      <RoyaleDemoNotice />

      {/* El botón sale siempre: Pack Battle se puede crear en cualquier caso, y Royale se enseña
          dentro del modal con su etiqueta "Soon". Antes se ESCONDÍA el botón entero al filtrar
          Royale sin permiso, así que desde ahí no había forma de crear ni una Pack. */}
      <QuickMatch
        onCreate={() => setCreateOpen(true)}
        onPlayDemo={modos.has('pack') ? () => setDemoOpen(true) : undefined}
      />

      {/* UNA sola lista con los dos modos mezclados y la MISMA tarjeta para ambos. LiveBattles ya
          pintaba una rejilla uniforme que sirve para cualquier modo, y la propia tarjeta lleva su
          chapa de Pack Battle o Battle Royale, así que no hacía falta nada más. Sacar las royale
          a una tarjeta ancha aparte partía la lista en dos alturas y obligaba a bajar para ver la
          otra mitad.

          Se le pasan TAMBIÉN las terminadas: su pestaña "Recent" ya las enseña, de cualquier modo
          y ordenadas por cuándo se liquidaron. Tener además una sección "Recent" propia debajo era
          repetir la misma lista dos veces en la misma pantalla. */}
      <LiveBattles
        battles={enLobby}
        meWallet={meWallet}
        modos={modos}
        onModos={(m) => setParams(paramModos(m))}
        onBattleAction={onBattleAction}
        onCancel={onCancel}
        onReplay={onReplay}
        onOpen={(b) => navigate(battleHref(b.id, { status: b.battleStatus }))}
      />

      <DelegationGate gate={gate} />
      {createOpen && (
        /* Solo se fija el modo si el usuario está mirando UNO. Con los dos marcados no ha dicho
           a qué quiere jugar, así que lo elige en el modal. */
        <CreateBattleModal
          royaleDisponible={canCreateRoyale(meWallet)}
          lockedMode={modos.size === 1 ? [...modos][0] : undefined}
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => { setCreateOpen(false); navigate('/play/battle/' + id) }}
        />
      )}
      {demoOpen && (
        <DemoPicker
          onClose={() => setDemoOpen(false)}
          onPick={(m) => { setDemoOpen(false); navigate('/play/demo/' + m) }}
        />
      )}
    </div>
  )
}
