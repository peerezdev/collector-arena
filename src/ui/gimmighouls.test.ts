import { describe, it, expect } from 'vitest'
import { fmtGimmighouls, fmtGimmighoulsCompacto } from './gimmighouls'

describe('how a Gimmighouls counter gets written', () => {
  it('half a point shows, which is the whole reason the counter is decimal', () => {
    // A 50 dollar pack at 0.01 per dollar. If this comes out as "0" or "1", the Help copy lies.
    expect(fmtGimmighouls(0.5)).toBe('0.5')
    expect(fmtGimmighoulsCompacto(0.5)).toBe('0.5')
  })

  it('does not drag out the decimal tail of a boost', () => {
    // 33 dollars with a 10.5% boost gives exactly 36.465. On screen, two decimals.
    expect(fmtGimmighouls(36.465)).toBe('36.47')
    expect(fmtGimmighoulsCompacto(36.465)).toBe('36.47')
  })

  it('nor the binary garbage the backend already trims, in case it ever arrives', () => {
    expect(fmtGimmighouls(110.00000000000001)).toBe('110')
    expect(fmtGimmighoulsCompacto(110.00000000000001)).toBe('110')
  })

  it('an integer is still written whole, with no ",00" padding', () => {
    expect(fmtGimmighouls(5)).toBe('5')
    expect(fmtGimmighouls(1500)).toBe('1,500')
  })

  it('the abbreviated form only kicks in from the thousand mark, which is where the long number gets in the way', () => {
    expect(fmtGimmighoulsCompacto(999.5)).toBe('999.5')
    expect(fmtGimmighoulsCompacto(1500)).toBe('1.5K')
    expect(fmtGimmighoulsCompacto(2_500_000)).toBe('2.5M')
  })

  it('the sign survives, because the ranking paints differences', () => {
    expect(fmtGimmighoulsCompacto(-0.5)).toBe('-0.5')
    expect(fmtGimmighoulsCompacto(-1500)).toBe('-1.5K')
  })
})
