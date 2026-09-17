import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fetchAirdrop, claimAirdrop, AirdropError } from './airdropClient'

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
const ko = (status: number) => ({ ok: false, status, json: async () => ({}) })

beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })

describe('airdropClient', () => {
  it('lee el estado del airdrop', async () => {
    vi.mocked(fetch).mockResolvedValue(
      ok({ eligible: true, amount: 1483000000, claimed: false, signature: null }) as never)
    const s = await fetchAirdrop('tok')
    expect(s).toEqual({ eligible: true, amount: 1483000000, claimed: false, signature: null })
  })

  it('manda el token en la cabecera', async () => {
    vi.mocked(fetch).mockResolvedValue(ok({ eligible: false, amount: 0, claimed: false, signature: null }) as never)
    await fetchAirdrop('tok-123')
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok-123')
  })

  it('traduce el 503 a unavailable y NO a no elegible', async () => {
    // La distinción es la del backend: no saber no es saber que no.
    vi.mocked(fetch).mockResolvedValue(ko(503) as never)
    await expect(fetchAirdrop('tok')).rejects.toMatchObject({ kind: 'unavailable' })
  })

  it('traduce el 409 a already_claimed', async () => {
    vi.mocked(fetch).mockResolvedValue(ko(409) as never)
    await expect(claimAirdrop('tok')).rejects.toMatchObject({ kind: 'already_claimed' })
  })

  it('traduce el 403 a not_eligible', async () => {
    vi.mocked(fetch).mockResolvedValue(ko(403) as never)
    await expect(claimAirdrop('tok')).rejects.toMatchObject({ kind: 'not_eligible' })
  })

  it('cualquier otro fallo es failed', async () => {
    vi.mocked(fetch).mockResolvedValue(ko(500) as never)
    await expect(claimAirdrop('tok')).rejects.toBeInstanceOf(AirdropError)
  })

  it('devuelve la firma al reclamar', async () => {
    vi.mocked(fetch).mockResolvedValue(ok({ signature: 'sig-1', amount: 1483000000 }) as never)
    expect(await claimAirdrop('tok')).toEqual({ signature: 'sig-1', amount: 1483000000 })
  })

  it('traduce el 409 con needs_delegation a needs_delegation', async () => {
    // Dos razones producen 409: ya reclamó, o no autorizó firma. El cliente tiene que
    // diferenciarlas para contar historias completamente diferentes: una es "tu dinero se movió",
    // la otra es "nos diste permiso para Pack, ahora dámelo también para Airdrop".
    vi.mocked(fetch).mockReturnValue(Promise.resolve({
      ok: false, status: 409, json: async () => ({ detail: 'needs_delegation' })
    }) as never)
    await expect(claimAirdrop('tok')).rejects.toMatchObject({ kind: 'needs_delegation' })
  })

  it('409 con otro detail es still already_claimed', async () => {
    vi.mocked(fetch).mockReturnValue(Promise.resolve({
      ok: false, status: 409, json: async () => ({ detail: 'algo_más' })
    }) as never)
    await expect(claimAirdrop('tok')).rejects.toMatchObject({ kind: 'already_claimed' })
  })

  it('409 sin body parseable sigue siendo already_claimed', async () => {
    vi.mocked(fetch).mockReturnValue(Promise.resolve({
      ok: false, status: 409, json: async () => { throw new Error('unparseable') }
    }) as never)
    await expect(claimAirdrop('tok')).rejects.toMatchObject({ kind: 'already_claimed' })
  })

  it('traduce el 503 a unavailable en claim también', async () => {
    vi.mocked(fetch).mockResolvedValue(ko(503) as never)
    await expect(claimAirdrop('tok')).rejects.toMatchObject({ kind: 'unavailable' })
  })
})
