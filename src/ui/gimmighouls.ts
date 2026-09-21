/**
 * How a Gimmighouls counter is written.
 *
 * The counter IS DECIMAL ever since the gacha pays 0.01 per dollar: a 50 dollar pack gives half
 * a point, and with an integer counter those two machines (25 and 50 dollars, the most played
 * ones) would have paid nothing. See `award_gimmighouls` in the backend.
 *
 * The price of that is that the number that arrives is no longer round, so it is necessary to
 * decide HOW MUCH gets shown. Two decimals: that is what makes half a point visible without
 * showing on screen the 36.465 of a 10.5% boost. The backend stores six, so what is shown is
 * rounded and what accumulates is not. This lives here instead of spread across the screens
 * because there are four places that render it (desktop header, mobile header, and two in the
 * ranking) and it is enough for one to fall behind to show a number that does not match the
 * other three.
 */
const DECIMALES = 2

/** Trims to two decimals and drops trailing zeros: 0.5 → "0.5", 36.465 → "36.47", 5 → "5". */
function corta(n: number): number {
  return +n.toFixed(DECIMALES)
}

/** With a thousands separator. For the desktop header and the wide ranking. */
export function fmtGimmighouls(n: number): string {
  return corta(n).toLocaleString('en-US', { maximumFractionDigits: DECIMALES })
}

/**
 * Abbreviated for narrow spots: 1K, 1M, 1B. Below a thousand it is written in full, with its
 * decimals, because that is exactly the range where half a point lives and abbreviating it would
 * erase it.
 */
export function fmtGimmighoulsCompacto(n: number): string {
  const signo = n < 0 ? '-' : ''
  const a = Math.abs(n)
  const trim = (x: number) => String(+x.toFixed(1))
  if (a >= 1e9) return `${signo}${trim(a / 1e9)}B`
  if (a >= 1e6) return `${signo}${trim(a / 1e6)}M`
  if (a >= 1e3) return `${signo}${trim(a / 1e3)}K`
  return `${signo}${corta(a)}`
}
