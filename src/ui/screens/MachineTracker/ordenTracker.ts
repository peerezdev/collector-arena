/**
 * En qué orden quiere ver el usuario las máquinas del tracker.
 *
 * En el navegador y no en el backend, por lo mismo que [[hiddenMachines]]: el tracker no pide
 * sesión, así que una preferencia por wallet dejaría fuera a los visitantes anónimos.
 *
 * LO QUE NO SE HA COLOCADO VA DETRÁS, EN EL ORDEN DEL SERVIDOR. Es la decisión que sostiene el
 * módulo entero, y es la misma que en las ocultas: lo que el usuario no ha decidido, no se toca.
 * Guardando solo las posiciones que él ha fijado, una máquina que Collector Crypt añada mañana
 * aparece al final sin colarse en mitad de lo que ya había colocado, y una que retire desaparece
 * sin dejar hueco.
 *
 * De paso arregla algo que no se pidió pero molestaba: el servidor ordena por edge, así que hoy la
 * rejilla se recoloca sola en cada refresco de 10 segundos. Con un orden propio deja de moverse.
 */
export const CLAVE_ORDEN = 'ba.evTracker.orden'

/** Los códigos que el usuario ha colocado, en su orden. Vacío ante cualquier dato inválido. */
export function leerOrden(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(CLAVE_ORDEN) ?? 'null')
    if (!Array.isArray(v)) return []
    const limpio = v.filter((x): x is string => typeof x === 'string' && x.length > 0)
    // Sin duplicados: uno solo pintaría la misma tarjeta dos veces.
    return [...new Set(limpio)]
  } catch {
    // localStorage puede fallar entero (Safari en privado) y el JSON puede estar corrupto.
    // Ninguna de las dos cosas debe tumbar la pantalla por una preferencia.
    return []
  }
}

export function guardarOrden(orden: string[]): void {
  try {
    // TAL CUAL, sin ordenar. Al revés que en las ocultas: aquí el orden ES el dato.
    localStorage.setItem(CLAVE_ORDEN, JSON.stringify(orden))
  } catch {
    /* sin almacenamiento la preferencia dura lo que la pestaña; no es motivo para romper nada */
  }
}

/** Coloca las filas: primero las que el usuario ordenó, detrás el resto como venga del servidor. */
export function ordenar<T extends { machine: string }>(filas: T[], orden: string[]): T[] {
  if (orden.length === 0) return filas
  const porCodigo = new Map(filas.map((f) => [f.machine, f]))
  const colocadas: T[] = []
  const yaPuestas = new Set<string>()
  for (const code of orden) {
    const f = porCodigo.get(code)
    // Un código guardado que ya no existe se ignora: Collector Crypt retira máquinas.
    if (f && !yaPuestas.has(code)) {
      colocadas.push(f)
      yaPuestas.add(code)
    }
  }
  return [...colocadas, ...filas.filter((f) => !yaPuestas.has(f.machine))]
}

/** Al marcar una máquina en el selector: entra al final. Si ya estaba, se mueve al final. */
export function alFinal(orden: string[], code: string): string[] {
  return [...orden.filter((c) => c !== code), code]
}

/**
 * Marcar una máquina en el selector: aparece LA ÚLTIMA de la rejilla.
 *
 * No basta con `alFinal`, y es la trampa de este módulo. Lo no colocado se pinta DETRÁS de lo
 * colocado, así que meter un código en una lista casi vacía lo manda al principio de la pantalla,
 * justo lo contrario de lo que el usuario acaba de pedir. Hay que congelar antes lo que está
 * viendo; entonces ya no queda nada "sin colocar" que pueda adelantarlo.
 *
 * `mostradas` son las visibles ANTES de marcar esta, que todavía está oculta.
 *
 * Al DESmarcar no hace falta tocar nada: un código de una máquina oculta se queda en la lista sin
 * efecto, porque `ordenar` recibe las filas ya filtradas y no lo encuentra. Hubo una función para
 * limpiarlo y se quitó al comprobar, mutando el código, que ningún test notaba su ausencia.
 */
export function mostrarAlFinal<T extends { machine: string }>(
  mostradas: T[], orden: string[], code: string,
): string[] {
  return alFinal(materializar(mostradas, orden), code)
}

/** Arrastrar: saca el elemento de `desde` y lo mete en `hasta`. */
export function mover(orden: string[], desde: number, hasta: number): string[] {
  const fuera = (i: number) => i < 0 || i >= orden.length
  if (fuera(desde) || fuera(hasta) || desde === hasta) return orden
  const copia = [...orden]
  const [pieza] = copia.splice(desde, 1)
  copia.splice(hasta, 0, pieza)
  return copia
}

/**
 * Convierte en posiciones explícitas el orden que el usuario tiene delante.
 *
 * Hace falta antes del PRIMER arrastre: una tarjeta que nunca se tocó no tiene posición, así que
 * no hay nada que mover. Se le pasan las filas MOSTRADAS, no todas: si colara las ocultas, al
 * volver a mostrar una aparecería en mitad del orden en vez de al final.
 */
export function materializar<T extends { machine: string }>(mostradas: T[], orden: string[]): string[] {
  return ordenar(mostradas, orden).map((f) => f.machine)
}
