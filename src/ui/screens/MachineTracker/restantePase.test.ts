import { describe, it, expect } from 'vitest'
import { restantePase } from './restantePase'

const MIN = 60
const HORA = 3600
const DIA = 86_400

describe('time left on the pass', () => {
  it('counts in days while there is at least one', () => {
    expect(restantePase(5 * DIA + 3 * HORA)).toBe('5 days left')
    expect(restantePase(DIA)).toBe('1 day left')
  })

  it('switches to hours on the last day', () => {
    expect(restantePase(DIA - 1)).toBe('23 hours left')
    expect(restantePase(HORA)).toBe('1 hour left')
  })

  it('switches to minutes in the last hour, never "0 hours"', () => {
    // "0 hours left" reads as expired with ten minutes still to use.
    expect(restantePase(HORA - 1)).toBe('59 minutes left')
    expect(restantePase(10 * MIN)).toBe('10 minutes left')
    expect(restantePase(MIN)).toBe('1 minute left')
  })

  it('under a minute it does not say "0 minutes"', () => {
    expect(restantePase(59)).toBe('less than a minute left')
    // Already past, but the screen has not asked the backend again yet.
    expect(restantePase(0)).toBe('less than a minute left')
    expect(restantePase(-30)).toBe('less than a minute left')
  })
})
