import { useEffect, useRef, useState } from 'react'
import { useIdentityToken } from '@privy-io/react-auth'
import { isBalanceHeld, useBalanceHeld } from './balanceHold'
import { config } from '../onchain/config'

// ─── Hook ────────────────────────────────────────────────────────────────────
//
// On-chain token balance of the caller's embedded wallet, read through the backend rather
// than directly from the RPC. The public mainnet RPC (api.mainnet-beta.solana.com) returns 403
// to browser Origins, so a browser cannot query token balances there at all. The backend has no
// Origin header and already holds the per-network rpc_url + mint, so it reads the balance
// reliably — identical behavior on devnet and mainnet, and no RPC provider key exposed in the
// client.
//
// Parameterised by endpoint + response field so USDC and CARDS (and any future SPL balance we
// expose the same way) share one polling/freeze implementation instead of two copies.
//
// Se refresca cada 30s SOLO con la pestaña a la vista, y se refresca una vez al volver a ella.
// El motivo está junto al código, abajo: cada consulta cuesta una llamada al RPC.

function useTokenBalance(endpoint: string, field: string, label: string): { balance: number | null; loading: boolean } {
  // Al soltar la congelación hay que repintar en cuanto se pueda: si no, el saldo real tardaría
  // hasta 30s en aparecer y el usuario vería un número que ya no es el suyo.
  const held = useBalanceHeld()
  const { identityToken } = useIdentityToken()
  const [balance, setBalance] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const unmountedRef = useRef(false)

  useEffect(() => {
    unmountedRef.current = false
    return () => {
      unmountedRef.current = true
    }
  }, [])

  useEffect(() => {
    if (!identityToken) {
      setBalance(null)
      setLoading(false)
      return
    }
    const token = identityToken // narrowed to string for the async closure below

    let intervalId: ReturnType<typeof setInterval> | null = null

    async function fetchBalance() {
      try {
        const resp = await fetch(`${config.backendUrl}${endpoint}`, {
          headers: {
            Authorization: `Bearer ${token}`,
            'ngrok-skip-browser-warning': 'true',
          },
        })
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const data = (await resp.json()) as Record<string, unknown>
        if (!unmountedRef.current) {
          // Con una tirada sin revelar, el saldo se queda como estaba: el auto-buyback del turbo
          // lo sube en cuanto CC abre el sobre por dentro, y verlo subir destripa el resultado
          // antes del reveal. Se sigue consultando (así al soltar ya está fresco), pero no se pinta.
          if (!isBalanceHeld()) setBalance(typeof data[field] === 'number' ? (data[field] as number) : 0)
          setLoading(false)
        }
      } catch (err) {
        if (import.meta.env.DEV) {
          console.warn(`[${label}] balance error:`, err)
        }
        if (!unmountedRef.current) {
          setLoading(false)
        }
      }
    }

    // Solo se pregunta mientras la pestaña se VE. Cada consulta es una llamada al RPC por usuario
    // conectado (la hace el backend en su nombre), así que una pestaña olvidada abierta toda la
    // noche gastaba unas 2.880 sin que nadie mirara el saldo.
    //
    // Al volver se refresca ANTES de reanudar el ciclo. Sin eso, lo primero que vería quien vuelve
    // sería el número congelado de hace horas, que es peor que no haber parado nunca.
    //
    // "Visible" no es "con el foco": dos ventanas lado a lado cuentan las dos como visibles, y es
    // lo correcto, porque se están viendo.
    const isVisible = () => typeof document === 'undefined' || document.visibilityState !== 'hidden'

    const startPolling = () => {
      if (intervalId === null) intervalId = setInterval(fetchBalance, 30_000)
    }
    const stopPolling = () => {
      if (intervalId !== null) {
        clearInterval(intervalId)
        intervalId = null
      }
    }
    const onVisibilityChange = () => {
      if (isVisible()) {
        fetchBalance()
        startPolling()
      } else {
        stopPolling()
      }
    }

    setLoading(true)
    fetchBalance()
    if (isVisible()) startPolling()
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      stopPolling()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [identityToken, held, endpoint, field, label])

  return { balance, loading }
}

export function useUsdcBalance(): { usdc: number | null; loading: boolean } {
  const { balance, loading } = useTokenBalance('/users/me/usdc', 'usdc', 'useUsdcBalance')
  return { usdc: balance, loading }
}

// Saldo on-chain de $CARDS (airdrop de Collector Crypt) de la wallet embebida, mismo mecanismo
// que useUsdcBalance pero contra /users/me/cards. Devuelve null tanto sin sesión como si el
// backend responde 503 (mint sin configurar) — el consumidor no distingue ambos casos, igual
// que useUsdcBalance no distingue "sin sesión" de "error de red".
export function useCardsBalance(): { cards: number | null; loading: boolean } {
  const { balance, loading } = useTokenBalance('/users/me/cards', 'cards', 'useCardsBalance')
  return { cards: balance, loading }
}
