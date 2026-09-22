import type { ReactNode } from 'react'
import { EmblemaRango } from './EmblemaRango'
import { TagUsuario } from './TagUsuario'
import { useBadges } from './useBadges'

/**
 * A player's name with their rank emblem before it and their tags after it.
 *
 * The name itself is the caller's element (a link to the profile in the chat), passed as
 * children and left untouched: the emblem and tags sit outside it, so a link's accessible name
 * stays the bare name. Until badges arrive, or if they fail, only the name shows.
 */
export function NombreUsuario({ wallet, size, children }: { wallet: string; size: number; children: ReactNode }) {
  const b = useBadges([wallet])[wallet]
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', verticalAlign: 'middle', gap: 5, minWidth: 0 }}>
      <EmblemaRango rank={b?.rank ?? null} size={size} />
      {children}
      {b?.tags.map((t) => <TagUsuario key={t} tag={t} />)}
    </span>
  )
}
