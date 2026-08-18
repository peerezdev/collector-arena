/**
 * Cómo se escribe un contador de Gimmighouls.
 *
 * El contador ES DECIMAL desde que el gacha paga 0.01 por dólar: un sobre de 50 $ da medio punto,
 * y con un contador entero esas dos máquinas (25 y 50 $, las más jugadas) no habrían pagado nada.
 * Ver `award_gimmighouls` en el backend.
 *
 * El precio de eso es que el número que llega ya no es redondo, así que hace falta decidir CUÁNTO
 * se enseña. Dos decimales: es lo que hace visible medio punto sin sacar por pantalla el 36.465 de
 * un boost del 10.5%. El backend guarda seis, así que lo que se ve está redondeado y lo que se
 * acumula no. Esto está aquí y no repartido por las pantallas porque son cuatro sitios los que lo
 * pintan (cabecera de escritorio, cabecera de móvil, y dos en el ranking) y basta que uno se quede
 * atrás para enseñar un número que no se parece a los otros tres.
 */
const DECIMALES = 2

/** Recorta a dos decimales y quita los ceros de la cola: 0.5 → "0.5", 36.465 → "36.47", 5 → "5". */
function corta(n: number): number {
  return +n.toFixed(DECIMALES)
}

/** Con separador de miles. Para la cabecera de escritorio y el ranking ancho. */
export function fmtGimmighouls(n: number): string {
  return corta(n).toLocaleString('en-US', { maximumFractionDigits: DECIMALES })
}

/**
 * Abreviado para los sitios estrechos: 1K, 1M, 1B. Debajo de mil se escribe entero, con sus
 * decimales, porque es justo el tramo donde vive medio punto y abreviarlo lo borraría.
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
