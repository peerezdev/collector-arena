import { describe, it, expect } from 'vitest'
import { fmtGimmighouls, fmtGimmighoulsCompacto } from './gimmighouls'

describe('cómo se escribe un contador de Gimmighouls', () => {
  it('medio punto se ve, que es todo el motivo de que el contador sea decimal', () => {
    // Un sobre de 50 $ a 0.01 por dólar. Si esto sale "0" o "1", la ayuda miente.
    expect(fmtGimmighouls(0.5)).toBe('0.5')
    expect(fmtGimmighoulsCompacto(0.5)).toBe('0.5')
  })

  it('no saca la cola de decimales de un boost', () => {
    // 33 $ con un boost del 10.5% dan 36.465 exactos. En pantalla, dos decimales.
    expect(fmtGimmighouls(36.465)).toBe('36.47')
    expect(fmtGimmighoulsCompacto(36.465)).toBe('36.47')
  })

  it('ni la basura binaria que el backend ya recorta, por si algún día llega', () => {
    expect(fmtGimmighouls(110.00000000000001)).toBe('110')
    expect(fmtGimmighoulsCompacto(110.00000000000001)).toBe('110')
  })

  it('un entero se sigue escribiendo entero, sin ",00" de relleno', () => {
    expect(fmtGimmighouls(5)).toBe('5')
    expect(fmtGimmighouls(1500)).toBe('1,500')
  })

  it('el abreviado solo entra a partir del millar, que es donde estorba el número largo', () => {
    expect(fmtGimmighoulsCompacto(999.5)).toBe('999.5')
    expect(fmtGimmighoulsCompacto(1500)).toBe('1.5K')
    expect(fmtGimmighoulsCompacto(2_500_000)).toBe('2.5M')
  })

  it('el signo sobrevive, porque el ranking pinta diferencias', () => {
    expect(fmtGimmighoulsCompacto(-0.5)).toBe('-0.5')
    expect(fmtGimmighoulsCompacto(-1500)).toBe('-1.5K')
  })
})
