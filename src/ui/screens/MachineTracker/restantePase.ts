/**
 * How long the tracker pass has left, as the label reads it.
 *
 * The unit shrinks as the end gets close: days, then hours on the last day, then minutes in the
 * last hour. Each switch is there so the figure never reaches zero while there is still time left:
 * "0 days left" and "0 hours left" both read as expired when there is an afternoon, or ten
 * minutes, still to use.
 *
 * Always rounded DOWN, so it never promises time that is not there.
 */
export function restantePase(quedanSeg: number): string {
  const dias = Math.floor(quedanSeg / 86_400)
  if (dias >= 1) return `${dias} day${dias === 1 ? '' : 's'} left`
  const horas = Math.floor(quedanSeg / 3600)
  if (horas >= 1) return `${horas} hour${horas === 1 ? '' : 's'} left`
  const minutos = Math.floor(quedanSeg / 60)
  if (minutos >= 1) return `${minutos} minute${minutos === 1 ? '' : 's'} left`
  // Also when it is already past: the label stays until the screen asks the backend again, and
  // "expired" there would contradict the panel still being open.
  return 'less than a minute left'
}
