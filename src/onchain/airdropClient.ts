// Cliente del claim del airdrop $CARDS. La wallet NUNCA viaja en la petición: el backend
// la saca del identity token, y eso es lo que impide reclamar lo de otro.
import { config } from './config'

export type AirdropErrorKind =
  | 'not_eligible'       // 403: esta wallet no está en la lista
  | 'already_claimed'    // 409: la PDA de ClaimStatus ya existe
  | 'needs_delegation'   // 409: no autorizó firma delegada por Privy
  | 'unavailable'        // 503: airdrop apagado, sin operador o sin fichero
  | 'chain'              // 502: RPC o Privy no contestan. REINTENTABLE
  | 'failed'

export class AirdropError extends Error {
  kind: AirdropErrorKind

  constructor(kind: AirdropErrorKind) {
    super(kind)
    this.kind = kind
  }
}

export interface AirdropStatus {
  eligible: boolean
  amount: number          // unidades base de CARDS (6 decimales)
  claimed: boolean
  signature: string | null
}

export interface AirdropClaimResult {
  signature: string
  amount: number
}

const BY_STATUS: Record<number, AirdropErrorKind> = {
  403: 'not_eligible', 409: 'already_claimed', 503: 'unavailable', 502: 'chain',
}

function headers(token: string): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
    'ngrok-skip-browser-warning': 'true',
  }
}

async function pedir<T>(path: string, token: string, method: 'GET' | 'POST'): Promise<T> {
  const r = await fetch(`${config.backendUrl}${path}`, { method, headers: headers(token) })
  if (!r.ok) {
    // Los 409s llevan un detail que el cliente necesita distinguir: "ya reclamaste" vs.
    // "no autorizaste firma". Leer defensivamente: si el body no se parsea, asumir already_claimed.
    if (r.status === 409) {
      try {
        const body = (await r.json()) as { detail?: string }
        if (body.detail === 'needs_delegation') {
          throw new AirdropError('needs_delegation')
        }
      } catch (e) {
        // Si la lectura falla, seguimos con already_claimed (no lanzamos parse error)
        if (e instanceof AirdropError) throw e
      }
    }
    throw new AirdropError(BY_STATUS[r.status] ?? 'failed')
  }
  return (await r.json()) as T
}

export function fetchAirdrop(token: string): Promise<AirdropStatus> {
  return pedir<AirdropStatus>('/users/me/airdrop/cards', token, 'GET')
}

export function claimAirdrop(token: string): Promise<AirdropClaimResult> {
  return pedir<AirdropClaimResult>('/users/me/airdrop/cards/claim', token, 'POST')
}
