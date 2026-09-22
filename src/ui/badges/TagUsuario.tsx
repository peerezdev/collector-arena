import { COLORS, FONTS } from '../theme'

/** A hand-assigned tag next to a name (TEAM, MOD…). Every tag uses this one style for now. */
export function TagUsuario({ tag }: { tag: string }) {
  return (
    <span style={{
      fontFamily: FONTS.mono, fontSize: 9.5, fontWeight: 700, letterSpacing: '.1em',
      color: COLORS.green, background: `${COLORS.green}1a`, border: `1px solid ${COLORS.green}55`,
      borderRadius: 4, padding: '1px 5px', lineHeight: 1.3, flexShrink: 0,
    }}>
      {tag}
    </span>
  )
}
