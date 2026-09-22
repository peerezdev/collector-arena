import { isRankId, rankInfo, SILVER_STAR_INNER, SILVER_STAR_OUTER, type RankId } from './ranks'

/**
 * A player's rank emblem. Each rank is a different OBJECT (medal, star, ingot, crystal, diamond,
 * obsidian shards), so ranks read by shape and not only by colour: at 15 px in the chat, and for
 * colour-blind players, colour alone is not enough.
 *
 * Draws nothing for no rank or an id it does not know (a rank added in the backend first).
 */
export function EmblemaRango({ rank, size }: { rank: RankId | string | null | undefined; size: number }) {
  if (!isRankId(rank)) return null
  const r = rankInfo(rank)
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label={r.name}
      style={{ flexShrink: 0, overflow: 'visible' }}>
      <title>{r.name}</title>
      <Shape id={rank} base={r.base} light={r.light} dark={r.dark} />
    </svg>
  )
}

function Shape({ id, base, light, dark }: { id: RankId; base: string; light: string; dark: string }) {
  switch (id) {
    case 'bronze':
      return (<>
        <circle cx="16" cy="16" r="12.5" fill={base} stroke={dark} strokeWidth="1.6" />
        <circle cx="16" cy="16" r="8" fill="none" stroke={light} strokeWidth="1.4" />
      </>)
    case 'silver':
      return (<>
        <polygon points={SILVER_STAR_OUTER} fill={base} stroke={dark} strokeWidth="1.5" strokeLinejoin="round" />
        <polygon points={SILVER_STAR_INNER} fill={light} />
      </>)
    case 'gold':
      return (<>
        <polygon points="9,9 23,9 29,23 3,23" fill={base} stroke={dark} strokeWidth="1.6" strokeLinejoin="round" />
        <polygon points="10.5,11 21.5,11 23,15 9,15" fill={light} />
      </>)
    case 'platinum':
      return (<>
        <polygon points="16,2 28,9 28,23 16,30 4,23 4,9" fill={base} stroke={dark} strokeWidth="1.6" strokeLinejoin="round" />
        <polygon points="16,2 28,9 16,16 4,9" fill={light} />
        <polyline points="16,16 16,30" fill="none" stroke={dark} strokeWidth="1" />
      </>)
    case 'diamond':
      return (<>
        <polygon points="9,5 23,5 30,12 16,29 2,12" fill={base} stroke={dark} strokeWidth="1.6" strokeLinejoin="round" />
        <polygon points="9,5 23,5 30,12 2,12" fill={light} />
        <path d="M11 12 L16 29 L21 12" fill="none" stroke={dark} strokeWidth="1" />
        <polyline points="11,12 16,5 21,12" fill="none" stroke={dark} strokeWidth="1" />
      </>)
    case 'obsidian':
      return (<>
        <polygon points="16,1 21,12 18,30 13,30 10,12" fill={base} stroke={light} strokeWidth="1.5" strokeLinejoin="round" />
        <polygon points="7,9 11,17 10,30 5,30 3,17" fill={base} stroke={light} strokeWidth="1.3" strokeLinejoin="round" />
        <polygon points="25,9 29,17 27,30 22,30 21,17" fill={base} stroke={light} strokeWidth="1.3" strokeLinejoin="round" />
        <polyline points="16,1 16,30" fill="none" stroke={light} strokeWidth="0.9" opacity="0.7" />
      </>)
  }
}
