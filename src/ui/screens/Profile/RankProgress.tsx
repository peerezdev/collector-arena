import { COLORS, FONTS, formatUsd } from '../../theme'
import { EmblemaRango } from '../../badges/EmblemaRango'
import { rankInfo, type RankId } from '../../badges/ranks'

export interface RankProgressData {
  wageredUsd: number
  nextRank: RankId | null
  nextThresholdUsd: number | null
}

/**
 * The rank's name and how far the next one is. Shown on every profile, not only your own: the
 * total wagered is already public in the profile stats.
 */
export function RankProgress({ rank, progress }: { rank: RankId | null; progress: RankProgressData | null }) {
  if (!progress) return null
  const { wageredUsd, nextRank, nextThresholdUsd } = progress
  const pct = nextThresholdUsd ? Math.min(100, Math.floor((wageredUsd / nextThresholdUsd) * 100)) : 100
  return (
    <div style={{ maxWidth: 420, marginBottom: 14 }}>
      {rank && (
        <div style={{ fontSize: 13, fontWeight: 600, color: rankInfo(rank).text }}>{rankInfo(rank).name}</div>
      )}
      {nextRank && nextThresholdUsd != null && (
        <>
          <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
            aria-label={`Progress to ${rankInfo(nextRank).name}`}
            style={{ height: 8, borderRadius: 99, background: '#ffffff12', marginTop: 8, overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, height: '100%', background: rankInfo(nextRank).base }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginTop: 6,
                        fontFamily: FONTS.mono, fontSize: 11, color: COLORS.muted }}>
            <span>{formatUsd(wageredUsd)} wagered</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {formatUsd(nextThresholdUsd)} for {rankInfo(nextRank).name}
              <EmblemaRango rank={nextRank} size={14} />
            </span>
          </div>
        </>
      )}
    </div>
  )
}
