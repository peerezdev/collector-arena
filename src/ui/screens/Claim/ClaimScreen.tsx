// Claim del airdrop $CARDS. Ruta enlazable desde fuera y fuera de la barra lateral a
// propósito: deja de tener sentido en cuanto CC cierre la bóveda, y una entrada muerta
// en el menú es peor que no tenerla.
import { useEffect, useRef, useState } from 'react'
import { useIdentityToken } from '@privy-io/react-auth'
import { config } from '../../../onchain/config'
import { fetchAirdrop, claimAirdrop, AirdropError } from '../../../onchain/airdropClient'
import type { AirdropStatus } from '../../../onchain/airdropClient'
import { FONTS } from '../../theme'

const CARDS = (base: number): string => (base / 1_000_000).toLocaleString('en-US')

/**
 * Paleta de la propia página de claim de Collector Crypt, a propósito y no la del juego.
 *
 * Esta pantalla enseña un token que no es nuestro: el naranja de $CARDS es lo que hace que la
 * cifra se lea de un vistazo como "esto son tus CARDS" para quien ya ha visto el claim oficial.
 * El acento se gasta UNA vez, en el símbolo del token; el resto de la tarjeta calla.
 */
const CC = {
  card: '#111820', cardBorde: '#1e2a38',
  texto: '#ffffff', apagado: '#94a3b8',
  naranja: '#f97316', naranjaOscuro: '#ea580c',
  verde: '#22c55e', rojo: '#ef4444', azul: '#38bdf8',
} as const

/** La tarjeta centrada: el único contenedor de esta pantalla, en todos sus estados. */
function Tarjeta({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      minHeight: '60vh', padding: 24,
    }}>
      <div style={{
        background: CC.card, borderRadius: 20, border: `1px solid ${CC.cardBorde}`,
        padding: 'clamp(28px,4vw,40px)', width: 'min(480px, 100%)', textAlign: 'center',
        boxShadow: '0 8px 32px rgba(0,0,0,.4)', fontFamily: FONTS.display,
      }}>
        {children}
      </div>
    </div>
  )
}

function Titulo({ children }: { children: React.ReactNode }) {
  return (
    <h1 style={{
      margin: 0, color: CC.texto, fontSize: 26, fontWeight: 700,
      letterSpacing: '-.01em', lineHeight: 1.2,
    }}>{children}</h1>
  )
}

/** Banner de estado. El color ES la información: verde ya está, rojo no te toca, naranja te falta algo. */
function Aviso({ tono, children }: { tono: 'ok' | 'mal' | 'aviso'; children: React.ReactNode }) {
  const color = tono === 'ok' ? CC.verde : tono === 'mal' ? CC.rojo : CC.naranja
  return (
    <div style={{
      marginTop: 24, padding: '14px 18px', borderRadius: 12,
      background: `${color}1a`, border: `1px solid ${color}4d`,
      color, fontSize: 14.5, lineHeight: 1.5,
    }}>{children}</div>
  )
}

/** El bloque de la cifra: es la protagonista de la pantalla y por eso lleva el único acento. */
function Cifra({ etiqueta, base }: { etiqueta: string; base: number }) {
  return (
    <div style={{
      marginTop: 24, padding: '24px 28px', borderRadius: 16,
      background: `linear-gradient(135deg, ${CC.naranja}1a 0%, ${CC.azul}1a 100%)`,
      border: `1px solid ${CC.naranja}33`,
    }}>
      <div style={{ color: CC.apagado, fontSize: 13.5, marginBottom: 8 }}>{etiqueta}</div>
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ color: CC.texto, fontSize: 'clamp(30px,6vw,38px)', fontWeight: 800, letterSpacing: '-.02em' }}>
          {CARDS(base)}
        </span>
        <span style={{ color: CC.naranja, fontSize: 'clamp(22px,4.5vw,30px)', fontWeight: 800 }}>$CARDS</span>
      </div>
    </div>
  )
}

function Boton({ onClick, disabled, children }: { onClick: () => void; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        marginTop: 24, width: '100%', minHeight: 52, borderRadius: 12, border: 0,
        cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? .7 : 1,
        fontFamily: FONTS.display, fontSize: 17, fontWeight: 700, color: '#fff',
        background: disabled
          ? 'linear-gradient(135deg,#374151,#1f2937)'
          : `linear-gradient(135deg,${CC.naranja},${CC.naranjaOscuro})`,
      }}
    >{children}</button>
  )
}

function EnlaceTx({ firma }: { firma: string }) {
  return (
    <a
      href={`https://solscan.io/tx/${firma}`}
      target="_blank"
      rel="noopener noreferrer"
      style={{
        display: 'inline-block', marginTop: 18, color: CC.azul, fontSize: 13,
        wordBreak: 'break-all', textDecoration: 'underline',
      }}
    >{firma}</a>
  )
}

type CargaFase = 'cargando' | 'listo' | 'error'

export function ClaimScreen() {
  const { identityToken } = useIdentityToken()
  const [cargaFase, setCargaFase] = useState<CargaFase>('cargando')
  const [estado, setEstado] = useState<AirdropStatus | null>(null)
  const [firma, setFirma] = useState<string | null>(null)
  const [avisoCarga, setAvisoCarga] = useState('')
  const [reintentando, setReintentando] = useState(false)
  const [reclamando, setReclamando] = useState(false)
  const [avisoReclamo, setAvisoReclamo] = useState('')

  // Guarda contra setState tras desmontar: reintentar() y reclamar() son promesas que pueden
  // resolver después de que el jugador navegue fuera de /claim.
  // OJO con este par: la asignación a `true` en el cuerpo del efecto NO es redundante con el
  // valor inicial del ref. Con StrictMode React monta, limpia y vuelve a montar, así que sin
  // ella el cleanup deja el ref en false PARA SIEMPRE y toda continuación asíncrona se cae en
  // silencio: el claim se ejecuta en la cadena y la pantalla nunca se entera. Pasó en uso real.
  const montado = useRef(true)
  useEffect(() => {
    montado.current = true
    return () => { montado.current = false }
  }, [])

  // Nunca "no eres elegible" por un fallo nuestro: eso se lo diría a gente que sí lo es. Y el
  // error nunca es un callejón sin salida: reintentar() repite esta misma llamada.
  function aplicarErrorCarga(e: unknown) {
    setAvisoCarga(e instanceof AirdropError && e.kind === 'unavailable'
      ? 'The airdrop claim is not available right now. Please try again later.'
      : 'Could not check your airdrop right now. Please try again in a moment.')
    setCargaFase('error')
  }

  useEffect(() => {
    if (config.isDevnet || !identityToken) return
    let vivo = true
    fetchAirdrop(identityToken)
      .then((s) => { if (vivo) { setEstado(s); setFirma(s.signature); setCargaFase('listo') } })
      .catch((e) => { if (vivo) aplicarErrorCarga(e) })
    return () => { vivo = false }
  }, [identityToken])

  // El botón "Try again" del estado de error: no depende del efecto (solo repite la
  // llamada con el identityToken actual), así no hace falta esperar a que cambie ese token.
  // No pasa cargaFase a 'cargando': eso reemplazaría toda la vista de error por "Checking your
  // airdrop…" y el botón desaparecería antes de poder mostrarse deshabilitado. En vez de eso se
  // queda en la misma vista y solo el botón cambia, igual que reclamando hace con Claim.
  function reintentar() {
    if (!identityToken) return
    setReintentando(true)
    setAvisoCarga('')
    fetchAirdrop(identityToken)
      .then((s) => { if (montado.current) { setEstado(s); setFirma(s.signature); setCargaFase('listo') } })
      .catch((e) => { if (montado.current) aplicarErrorCarga(e) })
      .finally(() => { if (montado.current) setReintentando(false) })
  }

  if (config.isDevnet) {
    return (
      <Tarjeta>
        <Titulo>$CARDS airdrop</Titulo>
        <Aviso tono="aviso">The $CARDS airdrop only exists on mainnet.</Aviso>
      </Tarjeta>
    )
  }
  if (!identityToken) {
    return (
      <Tarjeta>
        <Titulo>$CARDS airdrop</Titulo>
        <p style={{ margin: '10px 0 0', color: CC.apagado, fontSize: 15, lineHeight: 1.5 }}>
          Log in to check your $CARDS airdrop.
        </p>
      </Tarjeta>
    )
  }
  if (cargaFase === 'cargando') {
    return (
      <Tarjeta>
        <Titulo>$CARDS airdrop</Titulo>
        <p style={{ margin: '10px 0 0', color: CC.apagado, fontSize: 15 }}>Checking your airdrop…</p>
      </Tarjeta>
    )
  }
  if (cargaFase === 'error') {
    return (
      <Tarjeta>
        <Titulo>$CARDS airdrop</Titulo>
        <Aviso tono="aviso">{avisoCarga}</Aviso>
        <Boton onClick={reintentar} disabled={reintentando}>
          {reintentando ? 'Retrying…' : 'Try again'}
        </Boton>
      </Tarjeta>
    )
  }
  if (!estado?.eligible) {
    return (
      <Tarjeta>
        <Titulo>$CARDS airdrop</Titulo>
        <Aviso tono="mal">This wallet is not eligible for the $CARDS airdrop.</Aviso>
      </Tarjeta>
    )
  }

  // Solo la cadena decide si ya se reclamó. `firma` es contabilidad de una tabla local que
  // el backend escribe al MANDAR la transacción, antes de que confirme (no es autoritativa):
  // si se sumara aquí, una fila cuya transacción nunca llegó a cuajar dejaría al jugador
  // viendo "Already claimed" con un enlace a una tx inexistente y sin botón, para siempre.
  const yaEsta = estado.claimed

  async function reclamar() {
    if (!identityToken) return
    setReclamando(true)
    setAvisoReclamo('')
    try {
      const r = await claimAirdrop(identityToken)
      if (!montado.current) return
      setFirma(r.signature)
      setEstado((s) => (s ? { ...s, claimed: true } : s))
    } catch (e) {
      if (!montado.current) return
      // needs_delegation NO es already_claimed: decirle a alguien sin firma delegada que ya
      // reclamó sería mentirle sobre su propio dinero y lo dejaría sin saber qué hacer. Ambos
      // son reintentables (firma o red), así que el botón se queda activo. already_claimed en
      // cambio no lo es: perdimos la carrera contra otra pestaña, así que ya es el estado real
      // y no un fallo nuestro. Dejar el botón activo aquí sería la pantalla contradiciéndose a
      // sí misma (dice "ya reclamado" y debajo invita a clicar Claim otra vez), así que se pasa
      // a la misma vista de "ya reclamado" que produce el GET, con la firma que tengamos (puede
      // no haber ninguna, y la vista ya tolera signature: null).
      if (e instanceof AirdropError && e.kind === 'needs_delegation') {
        setAvisoReclamo('Grant signing access (session signer) so the game can claim for you, then try again. You can revoke it anytime in Privy.')
      } else if (e instanceof AirdropError && e.kind === 'already_claimed') {
        setEstado((s) => (s ? { ...s, claimed: true } : s))
      } else {
        setAvisoReclamo('The claim could not be completed. Please try again in a moment.')
      }
    } finally {
      if (montado.current) setReclamando(false)
    }
  }

  return (
    <Tarjeta>
      <Titulo>$CARDS airdrop</Titulo>
      {yaEsta ? (
        <>
          <Aviso tono="ok">You have already claimed your $CARDS airdrop.</Aviso>
          <Cifra etiqueta="Amount claimed" base={estado.amount} />
          {firma && <EnlaceTx firma={firma} />}
        </>
      ) : (
        <>
          <p style={{ margin: '10px 0 0', color: CC.apagado, fontSize: 15, lineHeight: 1.5 }}>
            Claim it below. We pay the network fee, so you don't need any SOL.
          </p>
          <Cifra etiqueta="Your airdrop" base={estado.amount} />
          {avisoReclamo && <Aviso tono="aviso">{avisoReclamo}</Aviso>}
          <Boton onClick={() => void reclamar()} disabled={reclamando}>
            {reclamando ? 'Claiming…' : 'Claim $CARDS'}
          </Boton>
        </>
      )}
    </Tarjeta>
  )
}
