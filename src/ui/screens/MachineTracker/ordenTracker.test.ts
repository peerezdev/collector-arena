import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import {
  alFinal, guardarOrden, leerOrden, materializar, mostrarAlFinal, mover, ordenar, CLAVE_ORDEN,
} from './ordenTracker'

beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

const filas = (...codes: string[]) => codes.map((machine) => ({ machine }))
const codigos = <T extends { machine: string }>(f: T[]) => f.map((x) => x.machine)

describe('el orden que el usuario le da al tracker', () => {
  it('sin nada guardado manda el servidor', () => {
    // El servidor ordena por edge. Mientras el usuario no coloque nada, esa decisión se respeta.
    expect(codigos(ordenar(filas('a', 'b', 'c'), []))).toEqual(['a', 'b', 'c'])
  })

  it('lo colocado va primero, en su orden', () => {
    expect(codigos(ordenar(filas('a', 'b', 'c'), ['c', 'a']))).toEqual(['c', 'a', 'b'])
  })

  it('lo que NO has colocado va detrás, conservando el orden del servidor', () => {
    // Es la misma lógica que las ocultas: lo que no has decidido, no se toca. Así una máquina
    // nueva de Collector Crypt aparece al final sin colarse en medio de lo que ya colocaste.
    expect(codigos(ordenar(filas('a', 'b', 'c', 'd'), ['d']))).toEqual(['d', 'a', 'b', 'c'])
  })

  it('un código guardado que ya no existe se ignora', () => {
    // Collector Crypt retira máquinas. Un orden viejo no puede dejar huecos ni inventar filas.
    expect(codigos(ordenar(filas('a', 'b'), ['retirada', 'b']))).toEqual(['b', 'a'])
  })

  it('ordenar no muta la lista que recibe', () => {
    const f = filas('a', 'b')
    ordenar(f, ['b'])
    expect(codigos(f)).toEqual(['a', 'b'])
  })

  it('marcar una máquina la manda al final, que es lo que se pidió', () => {
    expect(alFinal(['a', 'b'], 'c')).toEqual(['a', 'b', 'c'])
  })

  it('marcar algo ya colocado no lo duplica: lo mueve al final', () => {
    expect(alFinal(['a', 'b', 'c'], 'a')).toEqual(['b', 'c', 'a'])
  })

  it('marcar en el selector la deja LA ÚLTIMA de la rejilla, no la primera', () => {
    // La trampa del módulo, y un fallo que tuve escrito: lo no colocado se pinta detrás de lo
    // colocado, así que `alFinal` sobre una lista casi vacía manda la máquina al PRINCIPIO de la
    // pantalla. Hay que congelar antes lo que se está viendo.
    const orden = mostrarAlFinal(filas('anime_75'), [], 'pokemon_50')
    expect(orden).toEqual(['anime_75', 'pokemon_50'])
    expect(codigos(ordenar(filas('pokemon_50', 'anime_75'), orden))).toEqual(['anime_75', 'pokemon_50'])
  })

  it('marcar respeta el orden que ya había', () => {
    const orden = mostrarAlFinal(filas('a', 'b'), ['b', 'a'], 'c')
    expect(orden).toEqual(['b', 'a', 'c'])
  })

  it('ocultar y volver a marcar la deja al final, sin limpiar nada por el camino', () => {
    // Un código de una máquina oculta se queda en la lista sin efecto: `ordenar` recibe las filas
    // ya filtradas y no lo encuentra. Aquí 'b' sigue guardada y aun así entra la última.
    const orden = mostrarAlFinal(filas('a', 'c'), ['a', 'b', 'c'], 'b')
    expect(codigos(ordenar(filas('a', 'b', 'c'), orden))).toEqual(['a', 'c', 'b'])
  })

  it('arrastrar mueve una posición a otra', () => {
    expect(mover(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd'])
    expect(mover(['a', 'b', 'c', 'd'], 3, 1)).toEqual(['a', 'd', 'b', 'c'])
  })

  it('arrastrar a la misma posición no cambia nada', () => {
    expect(mover(['a', 'b', 'c'], 1, 1)).toEqual(['a', 'b', 'c'])
  })

  it('un índice fuera de rango devuelve la lista intacta, sin undefined dentro', () => {
    expect(mover(['a', 'b'], 5, 0)).toEqual(['a', 'b'])
    expect(mover(['a', 'b'], 0, 9)).toEqual(['a', 'b'])
    expect(mover(['a', 'b'], -1, 0)).toEqual(['a', 'b'])
  })

  it('mover no muta la lista que recibe', () => {
    const o = ['a', 'b', 'c']
    mover(o, 0, 2)
    expect(o).toEqual(['a', 'b', 'c'])
  })

  it('el primer arrastre congela lo que se está viendo', () => {
    // Una tarjeta que nunca se tocó no tiene posición, así que no se puede mover. Materializar
    // convierte el orden que el usuario TIENE DELANTE en posiciones explícitas.
    expect(materializar(filas('a', 'b', 'c'), [])).toEqual(['a', 'b', 'c'])
  })

  it('materializar respeta lo ya colocado y añade el resto detrás', () => {
    expect(materializar(filas('a', 'b', 'c'), ['c'])).toEqual(['c', 'a', 'b'])
  })

  it('materializar solo cuenta lo visible, no lo oculto', () => {
    // Se le pasan las filas MOSTRADAS. Si colara las ocultas, al volver a mostrar una aparecería
    // en mitad del orden en vez de al final.
    const mostradas = filas('a', 'c')
    expect(materializar(mostradas, [])).toEqual(['a', 'c'])
  })

  it('lo guardado se recupera', () => {
    guardarOrden(['b', 'a'])
    expect(leerOrden()).toEqual(['b', 'a'])
  })

  it('se guarda TAL CUAL, sin ordenar alfabéticamente', () => {
    // Al revés que las ocultas: aquí el orden ES el dato. Ordenarlo lo destruiría.
    guardarOrden(['z', 'a', 'm'])
    expect(localStorage.getItem(CLAVE_ORDEN)).toBe('["z","a","m"]')
  })

  it('un valor corrupto no rompe la pantalla', () => {
    localStorage.setItem(CLAVE_ORDEN, 'esto no es json')
    expect(leerOrden()).toEqual([])
  })

  it('un valor con la forma equivocada tampoco', () => {
    localStorage.setItem(CLAVE_ORDEN, '{"no":"es un array"}')
    expect(leerOrden()).toEqual([])
    localStorage.setItem(CLAVE_ORDEN, '[1,null,"buena",""]')
    expect(leerOrden()).toEqual(['buena'])
  })

  it('un duplicado guardado a mano no duplica la tarjeta en pantalla', () => {
    localStorage.setItem(CLAVE_ORDEN, '["a","a","b"]')
    expect(codigos(ordenar(filas('a', 'b'), leerOrden()))).toEqual(['a', 'b'])
  })

  it('sin localStorage disponible no revienta', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denegado') })
    expect(() => guardarOrden(['x'])).not.toThrow()
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denegado') })
    expect(leerOrden()).toEqual([])
  })
})
