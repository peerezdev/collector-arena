import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { COLORS, FONTS, RARITY, formatUsd } from '../../theme'
import { useMachineList } from '../../useMachines'
import { useAliases } from '../../useAliases'
import { fetchGachaWinners, fetchRarityGaps, type GachaWinner, type RarityGaps }
  from '../../../onchain/gachaClient'

/** Cuántos ganadores traer. El 200 es el techo de la API de Collector Crypt, no una elección
 *  nuestra: pedirle más devuelve 200 igual, así que ofrecer 500 sería prometer lo que no hay. */
const CANTIDADES = [10, 50, 200] as const
const RAREZAS = ['All', 'Common', 'Uncommon', 'Rare', 'Epic'] as const

const corta = (w: string) => (w.length > 10 ? `${w.slice(0, 4)}…${w.slice(-4)}` : w)

function hace(iso: string | null): string {
  if (!iso) return ''
  const s = Math.max(0, (Date.now() - Date.parse(iso.endsWith('Z') ? iso : `${iso}Z`)) / 1000)
  if (s < 60) return `${Math.floor(s)}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

const colorDe = (r: string | null): string =>
  (RARITY as Record<string, string | undefined>)[(r ?? '').toLowerCase()] ?? COLORS.muted

function Chip({ activo, onClick, children }: {
  activo: boolean; onClick: () => void; children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={activo}
      onClick={onClick}
      style={{
        padding: '7px 13px', borderRadius: 10, cursor: 'pointer',
        fontFamily: FONTS.mono, fontSize: 11, letterSpacing: '.08em',
        border: `1px solid ${activo ? COLORS.green : COLORS.border}`,
        background: activo ? `${COLORS.green}1a` : 'transparent',
        color: activo ? COLORS.green : COLORS.muted,
      }}
    >
      {children}
    </button>
  )
}

const ORDEN_RAREZA = ['Common', 'Uncommon', 'Rare', 'Epic'] as const

/**
 * Tiradas que lleva cada rareza sin salir en la máquina elegida.
 *
 * Solo se enseña con una máquina concreta: mezclando todas, el hueco no significa nada porque las
 * tiradas de máquinas distintas no comparten sorteo.
 *
 * El texto dice "since last" y no "due" a propósito. El gacha usa VRF y cada tirada es
 * independiente: un hueco largo NO hace la rareza más probable, y presentarlo como si tocara sería
 * empujar a la gente a perseguirlo.
 */
function GapsPorRareza({ machine }: { machine: string }) {
  const [datos, setDatos] = useState<RarityGaps | null>(null)

  useEffect(() => {
    let cancelado = false
    setDatos(null)
    fetchRarityGaps(machine)
      .then((d) => { if (!cancelado) setDatos(d) })
      .catch(() => { /* es un extra: si falla, la pantalla sigue */ })
    return () => { cancelado = true }
  }, [machine])

  if (!datos) return null

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
      padding: '12px 14px', borderRadius: 12,
      background: COLORS.panel, border: `1px solid ${COLORS.border}`,
    }}>
      <span style={{ fontFamily: FONTS.mono, fontSize: 10, letterSpacing: '.18em', color: COLORS.muted }}>
        PACKS SINCE LAST
      </span>
      {ORDEN_RAREZA.map((r) => {
        const n = datos.gaps[r]
        return (
          <span key={r} style={{
            display: 'inline-flex', alignItems: 'baseline', gap: 6,
            padding: '5px 10px', borderRadius: 9,
            background: `${colorDe(r)}14`, border: `1px solid ${colorDe(r)}59`,
          }}>
            <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: colorDe(r) }}>{r}</span>
            <span style={{ fontSize: 14, fontWeight: 700, color: COLORS.text }}>
              {/* null = no salió en la muestra. "200+" y no "200": solo se sabe que es mayor. */}
              {n == null ? `${datos.sampled}+` : n}
            </span>
          </span>
        )
      })}
    </div>
  )
}

export function WinnersPage() {
  const { machines } = useMachineList()
  // La máquina vive en la URL, no en estado local. Así el tracker puede enlazar aquí una máquina
  // concreta ("RECENT PULLS" de cada tarjeta), y de paso el filtro se puede compartir y sobrevive
  // a recargar la página. El resto de filtros no: son de esta visita.
  const [params, setParams] = useSearchParams()
  const maquina = params.get('machine') ?? ''
  const setMaquina = (code: string) => {
    setParams((antes) => {
      const p = new URLSearchParams(antes)
      if (code) p.set('machine', code)
      else p.delete('machine')     // sin máquina no se deja `?machine=` colgando en la barra
      return p
    }, { replace: true })
  }
  const [rareza, setRareza] = useState<(typeof RAREZAS)[number]>('All')
  const [cantidad, setCantidad] = useState<(typeof CANTIDADES)[number]>(10)
  const [filas, setFilas] = useState<GachaWinner[] | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelado = false
    setFilas(null); setError(false)
    fetchGachaWinners({
      machine: maquina || undefined,
      rarity: rareza === 'All' ? undefined : rareza,
      count: cantidad,
    })
      .then((r) => { if (!cancelado) setFilas(r) })
      .catch(() => { if (!cancelado) { setError(true); setFilas([]) } })
    return () => { cancelado = true }
  }, [maquina, rareza, cantidad])

  const wallets = useMemo(() => [...new Set((filas ?? []).map((w) => w.wallet))], [filas])
  const alias = useAliases(wallets)
  const nombreDe = (m: string | null) =>
    machines.find((x) => x.code === m)?.shortName ?? machines.find((x) => x.code === m)?.name ?? m

  return (
    <div style={{ padding: '24px clamp(14px,2.4vw,28px) 44px', display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h1 style={{ fontFamily: FONTS.display, fontSize: 26, fontWeight: 800, margin: 0 }}>Recent winners</h1>
        <p style={{ color: COLORS.muted, fontSize: 13.5, margin: '6px 0 0' }}>
          The latest pulls from the Collector Crypt live feed. For what each machine actually pays
          back over time, see the Machine Tracker.
        </p>
      </div>

      {maquina && <GapsPorRareza machine={maquina} />}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: FONTS.mono, fontSize: 10, letterSpacing: '.2em', color: COLORS.muted, width: 62 }}>MACHINE</span>
          <select
            aria-label="Machine"
            value={maquina}
            onChange={(e) => setMaquina(e.target.value)}
            style={{
              padding: '7px 11px', borderRadius: 10, background: COLORS.panel,
              border: `1px solid ${COLORS.border}`, color: COLORS.text,
              fontFamily: FONTS.mono, fontSize: 11.5, cursor: 'pointer',
            }}
          >
            <option value="">All machines</option>
            {[...machines].sort((a, b) => (a.price ?? 0) - (b.price ?? 0)).map((m) => (
              <option key={m.code} value={m.code}>{m.shortName ?? m.name}</option>
            ))}
          </select>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: FONTS.mono, fontSize: 10, letterSpacing: '.2em', color: COLORS.muted, width: 62 }}>RARITY</span>
          {RAREZAS.map((r) => (
            <Chip key={r} activo={rareza === r} onClick={() => setRareza(r)}>{r}</Chip>
          ))}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: FONTS.mono, fontSize: 10, letterSpacing: '.2em', color: COLORS.muted, width: 62 }}>SHOW</span>
          {CANTIDADES.map((n) => (
            <Chip key={n} activo={cantidad === n} onClick={() => setCantidad(n)}>Last {n}</Chip>
          ))}
        </div>
      </div>

      {/* Con rareza distinta de Epic, Collector Crypt no filtra y el recorte se hace después: por
          eso pueden salir menos de los pedidos. Decirlo evita que parezca que faltan datos. */}
      {filas && rareza !== 'All' && rareza !== 'Epic' && filas.length < cantidad && (
        <div style={{ fontFamily: FONTS.mono, fontSize: 11, color: COLORS.muted }}>
          Showing {filas.length}. Collector Crypt only filters Epic upstream, so the rest are
          picked out of the last {cantidad} pulls.
        </div>
      )}

      {filas === null ? (
        <div style={{ fontFamily: FONTS.mono, fontSize: 12, color: COLORS.muted }}>Loading…</div>
      ) : error ? (
        <div style={{ fontFamily: FONTS.mono, fontSize: 12, color: COLORS.muted }}>Couldn’t load winners right now.</div>
      ) : filas.length === 0 ? (
        <div style={{ fontFamily: FONTS.mono, fontSize: 12, color: COLORS.muted }}>No winners match these filters.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {filas.map((w) => (
            <div
              key={`${w.nft_address}-${w.at}`}
              style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '10px 12px',
                borderRadius: 12, background: COLORS.panel,
                border: `1px solid ${COLORS.border}`, borderLeft: `3px solid ${colorDe(w.rarity)}`,
              }}
            >
              {w.images[0]
                ? <img src={w.images[0]} alt="" width={38} height={52}
                       style={{ flex: 'none', objectFit: 'contain', borderRadius: 5, background: COLORS.panel2 }} />
                : <span style={{ flex: 'none', width: 38, height: 52, borderRadius: 5, background: COLORS.panel2 }} />}

              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {w.name ?? '—'}
                </div>
                <div style={{ fontFamily: FONTS.mono, fontSize: 10.5, color: COLORS.muted, marginTop: 2 }}>
                  {alias[w.wallet] ?? corta(w.wallet)} · {nombreDe(w.machine)}
                </div>
              </div>

              <div style={{ flex: 'none', textAlign: 'right' }}>
                <div style={{ fontSize: 14, fontWeight: 700 }}>
                  {w.insured_value != null ? formatUsd(w.insured_value) : '—'}
                </div>
                <div style={{ fontFamily: FONTS.mono, fontSize: 10, color: colorDe(w.rarity), marginTop: 2 }}>
                  {w.rarity ?? '—'}{w.at ? ` · ${hace(w.at)}` : ''}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
