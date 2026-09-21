/**
 * The order in which the user wants to see the tracker's machines.
 *
 * In the browser and not in the backend, for the same reason as [[hiddenMachines]]: the tracker
 * does not require a session, so a per-wallet preference would leave anonymous visitors out.
 *
 * WHATEVER HAS NOT BEEN PLACED GOES AFTER, IN THE SERVER'S ORDER. This is the decision that
 * holds up the whole module, and it is the same one as in the hidden ones: what the user has not
 * decided is not touched. By saving only the positions the user has fixed, a machine that
 * Collector Crypt adds tomorrow appears at the end without sneaking into the middle of what was
 * already placed, and one that it removes disappears without leaving a gap.
 *
 * Along the way this fixes something nobody asked for but that was annoying: the server orders
 * by edge, so today the grid repositions itself on every 10 second refresh. With its own order it
 * stops moving.
 */
export const CLAVE_ORDEN = 'ba.evTracker.orden'

/** The codes the user has placed, in their order. Empty on any invalid data. */
export function leerOrden(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(CLAVE_ORDEN) ?? 'null')
    if (!Array.isArray(v)) return []
    const limpio = v.filter((x): x is string => typeof x === 'string' && x.length > 0)
    // No duplicates: a single one would paint the same card twice.
    return [...new Set(limpio)]
  } catch {
    // localStorage can fail entirely (Safari in private mode) and the JSON can be corrupted.
    // Neither of these should crash the screen over a preference.
    return []
  }
}

export function guardarOrden(orden: string[]): void {
  try {
    // AS IS, unsorted. The opposite of the hidden ones: here the order IS the data.
    localStorage.setItem(CLAVE_ORDEN, JSON.stringify(orden))
  } catch {
    /* without storage the preference lasts only as long as the tab; that's no reason to break anything */
  }
}

/** Places the rows: first the ones the user ordered, then the rest as they come from the server. */
export function ordenar<T extends { machine: string }>(filas: T[], orden: string[]): T[] {
  if (orden.length === 0) return filas
  const porCodigo = new Map(filas.map((f) => [f.machine, f]))
  const colocadas: T[] = []
  const yaPuestas = new Set<string>()
  for (const code of orden) {
    const f = porCodigo.get(code)
    // A saved code that no longer exists is ignored: Collector Crypt removes machines.
    if (f && !yaPuestas.has(code)) {
      colocadas.push(f)
      yaPuestas.add(code)
    }
  }
  return [...colocadas, ...filas.filter((f) => !yaPuestas.has(f.machine))]
}

/** When checking a machine in the selector: it enters at the end. If it was already there, it moves to the end. */
export function alFinal(orden: string[], code: string): string[] {
  return [...orden.filter((c) => c !== code), code]
}

/**
 * Checking a machine in the selector: it appears LAST in the grid.
 *
 * `alFinal` alone is not enough, and that is the trap in this module. What has not been placed
 * is painted AFTER what has been placed, so putting a code into an almost empty list sends it to
 * the beginning of the screen, exactly the opposite of what the user just asked for. What is
 * currently visible has to be frozen first; then there is nothing "unplaced" left that could get
 * ahead of it.
 *
 * `mostradas` are the ones visible BEFORE checking this one, which is still hidden.
 *
 * When UNchecking, nothing needs to be touched: a code for a hidden machine stays in the list
 * with no effect, because `ordenar` receives the already filtered rows and does not find it.
 * There used to be a function to clean it up, and it was removed after checking, by mutating the
 * code, that no test noticed its absence.
 */
export function mostrarAlFinal<T extends { machine: string }>(
  mostradas: T[], orden: string[], code: string,
): string[] {
  return alFinal(materializar(mostradas, orden), code)
}

/** Drag: takes the element out of `desde` and puts it into `hasta`. */
export function mover(orden: string[], desde: number, hasta: number): string[] {
  const fuera = (i: number) => i < 0 || i >= orden.length
  if (fuera(desde) || fuera(hasta) || desde === hasta) return orden
  const copia = [...orden]
  const [pieza] = copia.splice(desde, 1)
  copia.splice(hasta, 0, pieza)
  return copia
}

/**
 * Converts into explicit positions the order the user currently has in front of them.
 *
 * Needed before the FIRST drag: a card that was never touched has no position, so there is
 * nothing to move. It is passed the SHOWN rows, not all of them: if the hidden ones slipped in,
 * showing one again would place it in the middle of the order instead of at the end.
 */
export function materializar<T extends { machine: string }>(mostradas: T[], orden: string[]): string[] {
  return ordenar(mostradas, orden).map((f) => f.machine)
}
