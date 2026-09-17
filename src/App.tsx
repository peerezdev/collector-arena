import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Suspense, lazy } from 'react'
import { COLORS } from './ui/theme'
import { Hub } from './ui/screens/Hub/Hub'
import { LobbyPage } from './ui/screens/Hub/LobbyPage'
import { AppShell } from './ui/layouts/AppShell'
import { BattleFlow } from './ui/flows/BattleFlow'
import { VerifyBattlePage } from './ui/screens/battle/VerifyBattlePage'
import { DemoPage } from './ui/screens/Demo/DemoPage'
import { DemoFlow } from './ui/flows/DemoFlow'
import { ProfilePage } from './ui/screens/Profile/ProfilePage'
import { LeaderboardPage } from './ui/screens/Leaderboard/LeaderboardPage'
import { HelpPage } from './ui/screens/Help/HelpPage'
import { WinnersPage } from './ui/screens/Winners/WinnersPage'
import { MachineTrackerPage } from './ui/screens/MachineTracker/MachineTrackerPage'
import { ClaimScreen } from './ui/screens/Claim/ClaimScreen'
import { usePrivy } from '@privy-io/react-auth'

const GachaVault = lazy(() => import('./ui/screens/gacha/GachaVault'))

/**
 * A dónde cae quien escribe la dirección a secas.
 *
 * Con sesión, al Lobby: el que vuelve quiere jugar, y hacerle pasar por una pantalla de banners
 * es un peaje. Sin sesión, a Home, que es donde se explica qué es esto y por qué merece la pena.
 *
 * `ready` importa: mientras Privy resuelve la sesión no se sabe cuál de los dos es, y elegir a
 * ciegas mandaría a un jugador con sesión a la portada durante un parpadeo.
 */
function Entrada() {
  const { ready, authenticated } = usePrivy()
  if (!ready) return null
  return <Navigate to={authenticated ? '/play/lobby' : '/home'} replace />
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Quien ya entró va al Lobby; quien no, a la portada. Home dejó de ser una parada para
            el jugador que vuelve —era un menú de pósters encima de un menú— y pasó a ser lo que
            de verdad es: la pantalla que explica el juego a quien todavía no juega. */}
        <Route path="/" element={<Entrada />} />
        <Route element={<AppShell />}>
          <Route path="/home" element={<Hub />} />
          {/* Un solo Lobby con el modo como filtro. Ver `lobbyFilter`: partir la lista en dos
              partía también la liquidez visible, y cada mitad parecía un juego muerto. */}
          <Route path="/play/lobby" element={<LobbyPage />} />
          {/* Las rutas viejas siguen vivas como redirección: hay enlaces sueltos por ahí (el
              Home, los banners, lo que la gente tenga guardado) y romperlos no aporta nada. */}
          <Route path="/play/arena" element={<Navigate to="/play/lobby?mode=pack" replace />} />
          <Route path="/play/royale" element={<Navigate to="/play/lobby?mode=royale" replace />} />
          <Route path="/play/battle/:battleId" element={<BattleFlow />} />
          {/* Página propia y no un modal: verificar pide una URL que se pueda guardar y mandar. */}
          <Route path="/play/battle/:battleId/verify" element={<VerifyBattlePage />} />
          <Route path="/play/demo/:mode" element={<DemoFlow />} />
          {/* Banco de pruebas de los reveals. Fuera de la navegación a propósito. */}
          <Route path="/demo" element={<DemoPage />} />
          <Route
            path="/play/gacha"
            element={
              <Suspense
                fallback={
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      height: '100%',
                      color: COLORS.muted,
                    }}
                  >
                    Loading…
                  </div>
                }
              >
                <GachaVault />
              </Suspense>
            }
          />
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="/profile/:wallet" element={<ProfilePage />} />
          <Route path="/ranking" element={<LeaderboardPage />} />
          <Route path="/machine-tracker" element={<MachineTrackerPage />} />
          {/* El feed de ganadores sigue accesible por URL, pero fuera de la barra. */}
          <Route path="/winners" element={<WinnersPage />} />
          <Route path="/help" element={<HelpPage />} />
          {/* Fuera de la barra a propósito: se enlaza desde fuera (un tuit) y deja de tener
              sentido en cuanto CC cierre la bóveda, así que no merece un hueco fijo en el menú. */}
          <Route path="/claim" element={<ClaimScreen />} />
        </Route>
        <Route path="*" element={<Entrada />} />
      </Routes>
    </BrowserRouter>
  )
}
