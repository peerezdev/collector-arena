import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import {
  alFinal, guardarOrden, leerOrden, materializar, mostrarAlFinal, mover, ordenar, CLAVE_ORDEN,
} from './ordenTracker'

beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

const filas = (...codes: string[]) => codes.map((machine) => ({ machine }))
const codigos = <T extends { machine: string }>(f: T[]) => f.map((x) => x.machine)

describe('the order the user gives the tracker', () => {
  it('with nothing saved, the server rules', () => {
    // The server orders by edge. As long as the user has not placed anything, that decision is respected.
    expect(codigos(ordenar(filas('a', 'b', 'c'), []))).toEqual(['a', 'b', 'c'])
  })

  it('what\'s placed goes first, in its order', () => {
    expect(codigos(ordenar(filas('a', 'b', 'c'), ['c', 'a']))).toEqual(['c', 'a', 'b'])
  })

  it('what you have NOT placed goes behind, keeping the server\'s order', () => {
    // It is the same logic as the hidden ones: what you have not decided is not touched. This
    // way a new machine from Collector Crypt appears at the end without sneaking into the middle
    // of what you already placed.
    expect(codigos(ordenar(filas('a', 'b', 'c', 'd'), ['d']))).toEqual(['d', 'a', 'b', 'c'])
  })

  it('a saved code that no longer exists is ignored', () => {
    // Collector Crypt removes machines. An old order cannot leave gaps or invent rows.
    expect(codigos(ordenar(filas('a', 'b'), ['retirada', 'b']))).toEqual(['b', 'a'])
  })

  it('sorting does not mutate the list it receives', () => {
    const f = filas('a', 'b')
    ordenar(f, ['b'])
    expect(codigos(f)).toEqual(['a', 'b'])
  })

  it('marking a machine sends it to the end, which is what was asked for', () => {
    expect(alFinal(['a', 'b'], 'c')).toEqual(['a', 'b', 'c'])
  })

  it('marking something already placed does not duplicate it: it moves it to the end', () => {
    expect(alFinal(['a', 'b', 'c'], 'a')).toEqual(['b', 'c', 'a'])
  })

  it('marking it in the selector leaves it LAST in the grid, not first', () => {
    // The trap in this module, and a bug I once had written: what is unplaced is painted after
    // what is placed, so `alFinal` on an almost empty list sends the machine to the BEGINNING of
    // the screen. What is currently being viewed has to be frozen first.
    const orden = mostrarAlFinal(filas('anime_75'), [], 'pokemon_50')
    expect(orden).toEqual(['anime_75', 'pokemon_50'])
    expect(codigos(ordenar(filas('pokemon_50', 'anime_75'), orden))).toEqual(['anime_75', 'pokemon_50'])
  })

  it('marking respects the order that was already there', () => {
    const orden = mostrarAlFinal(filas('a', 'b'), ['b', 'a'], 'c')
    expect(orden).toEqual(['b', 'a', 'c'])
  })

  it('hiding and marking it again leaves it at the end, without clearing anything along the way', () => {
    // A code for a hidden machine stays in the list with no effect: `ordenar` receives the rows
    // already filtered and does not find it. Here 'b' is still saved and still enters last.
    const orden = mostrarAlFinal(filas('a', 'c'), ['a', 'b', 'c'], 'b')
    expect(codigos(ordenar(filas('a', 'b', 'c'), orden))).toEqual(['a', 'c', 'b'])
  })

  it('dragging moves one position to another', () => {
    expect(mover(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd'])
    expect(mover(['a', 'b', 'c', 'd'], 3, 1)).toEqual(['a', 'd', 'b', 'c'])
  })

  it('dragging to the same position changes nothing', () => {
    expect(mover(['a', 'b', 'c'], 1, 1)).toEqual(['a', 'b', 'c'])
  })

  it('an out-of-range index returns the list intact, with no undefined inside', () => {
    expect(mover(['a', 'b'], 5, 0)).toEqual(['a', 'b'])
    expect(mover(['a', 'b'], 0, 9)).toEqual(['a', 'b'])
    expect(mover(['a', 'b'], -1, 0)).toEqual(['a', 'b'])
  })

  it('moving does not mutate the list it receives', () => {
    const o = ['a', 'b', 'c']
    mover(o, 0, 2)
    expect(o).toEqual(['a', 'b', 'c'])
  })

  it('the first drag freezes what is currently being viewed', () => {
    // A card that was never touched has no position, so it cannot be moved. Materializar
    // converts the order the user CURRENTLY HAS IN FRONT OF THEM into explicit positions.
    expect(materializar(filas('a', 'b', 'c'), [])).toEqual(['a', 'b', 'c'])
  })

  it('materializing respects what\'s already placed and adds the rest behind', () => {
    expect(materializar(filas('a', 'b', 'c'), ['c'])).toEqual(['c', 'a', 'b'])
  })

  it('materializing only counts what\'s visible, not what\'s hidden', () => {
    // It is passed the SHOWN rows. If the hidden ones slipped in, showing one again would appear
    // in the middle of the order instead of at the end.
    const mostradas = filas('a', 'c')
    expect(materializar(mostradas, [])).toEqual(['a', 'c'])
  })

  it('what\'s saved is retrieved', () => {
    guardarOrden(['b', 'a'])
    expect(leerOrden()).toEqual(['b', 'a'])
  })

  it('it\'s saved AS IS, without alphabetical sorting', () => {
    // The opposite of the hidden ones: here the order IS the data. Sorting it would destroy it.
    guardarOrden(['z', 'a', 'm'])
    expect(localStorage.getItem(CLAVE_ORDEN)).toBe('["z","a","m"]')
  })

  it('a corrupt value does not break the screen', () => {
    localStorage.setItem(CLAVE_ORDEN, 'esto no es json')
    expect(leerOrden()).toEqual([])
  })

  it('neither does a value with the wrong shape', () => {
    localStorage.setItem(CLAVE_ORDEN, '{"no":"es un array"}')
    expect(leerOrden()).toEqual([])
    localStorage.setItem(CLAVE_ORDEN, '[1,null,"buena",""]')
    expect(leerOrden()).toEqual(['buena'])
  })

  it('a duplicate saved by hand does not duplicate the card on screen', () => {
    localStorage.setItem(CLAVE_ORDEN, '["a","a","b"]')
    expect(codigos(ordenar(filas('a', 'b'), leerOrden()))).toEqual(['a', 'b'])
  })

  it('with no localStorage available, it does not blow up', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denegado') })
    expect(() => guardarOrden(['x'])).not.toThrow()
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denegado') })
    expect(leerOrden()).toEqual([])
  })
})
