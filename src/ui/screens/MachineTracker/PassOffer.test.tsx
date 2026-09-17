import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

const mocks = vi.hoisted(() => ({ comprar: vi.fn() }))
vi.mock('../../../onchain/gachaClient', () => ({ buyTrackerPass: mocks.comprar }))

import { PassOffer } from './PassOffer'

beforeEach(() => mocks.comprar.mockReset())

describe('buying the pass, inside the gate', () => {
  it('with no prices configured, NOTHING is offered', () => {
    // Zero means disabled. A disabled button would promise something that does not exist.
    const { container } = render(<PassOffer prices={{}} token="t" onComprado={() => {}} />)
    expect(container.textContent).toBe('')
  })

  it('shows both durations with their price', () => {
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    expect(screen.getByText(/7 days/i)).toBeTruthy()
    expect(screen.getByText(/30 days/i)).toBeTruthy()
  })

  it('states the price PER DAY, which is what lets you compare', () => {
    // 30 for 30 is 1.00/day; 7 for 10 is 1.43/day. Without this figure nobody can see which is the better deal.
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    expect(screen.getByText(/1\.43/)).toBeTruthy()
    expect(screen.getByText(/1\.00/)).toBeTruthy()
  })

  it('warns that there are no refunds BEFORE paying', () => {
    // If you buy 30 days and tomorrow wager 100, you paid for something you already had.
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    expect(screen.getByText(/no refunds/i)).toBeTruthy()
  })

  it('buying notifies whoever mounted it, so it refreshes access', async () => {
    mocks.comprar.mockResolvedValue({ pass_until: 1, days: 7, price_usdc: 10 })
    const onComprado = vi.fn()
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={onComprado} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    await waitFor(() => expect(onComprado).toHaveBeenCalled())
    expect(mocks.comprar).toHaveBeenCalledWith(7, 't')
  })

  it('while charging, it cannot be clicked twice', async () => {
    // A double click on a charge is two charges.
    //
    // The promise is left RESOLVABLE (with its own resolver) and resolved at the end of the
    // test, instead of one that never resolves. Leaving a real request hanging forever makes the
    // next `beforeEach` in this file, which touches this same mock, wait forever along with it:
    // it is not the component, it is how the mock and the hook interact here.
    let resolver!: (v: unknown) => void
    mocks.comprar.mockReturnValue(new Promise((r) => { resolver = r }))
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    const boton = screen.getByRole('button', { name: /7 days/i })
    fireEvent.click(boton)
    fireEvent.click(boton)
    expect(mocks.comprar).toHaveBeenCalledTimes(1)
    await act(async () => { resolver({ pass_until: 1, days: 7, price_usdc: 10 }) })
  })

  it('after a successful charge, clicking again while the gate is still mounted does NOT charge twice', async () => {
    // The real case: `onComprado()` (async, not awaited here) keeps traveling toward the
    // parent, which is the one that really unmounts the gate. This test leaves the gate mounted
    // ON PURPOSE after success, which is exactly the gap that used to let you click twice and
    // pay for two passes: the `finally` re-enabled the buttons as soon as `buyTrackerPass`
    // resolved, without waiting for `onComprado()` to finish.
    mocks.comprar.mockResolvedValueOnce({ pass_until: 1, days: 7, price_usdc: 10 })
    const onComprado = vi.fn()
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={onComprado} />)
    const boton = screen.getByRole('button', { name: /7 days/i })
    fireEvent.click(boton)
    await waitFor(() => expect(onComprado).toHaveBeenCalledTimes(1))
    // The gate stays mounted (nobody unmounted it): a second and third click must not charge again.
    fireEvent.click(boton)
    fireEvent.click(boton)
    expect(mocks.comprar).toHaveBeenCalledTimes(1)
  })

  it('with no balance, it says what needs to be done, not a generic error', async () => {
    mocks.comprar.mockRejectedValueOnce({ status: 402 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/not enough usdc/i)).toBeTruthy()
  })

  it('a charge failure says to retry, and NOT to deposit', async () => {
    // They have the money: asking them to deposit would be insulting and would fix nothing.
    mocks.comprar.mockRejectedValueOnce({ status: 502 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/try again/i)).toBeTruthy()
    expect(screen.queryByText(/deposit/i)).toBeNull()
  })

  // The following four are not in the original spec: this task adds them because the backend
  // can return 409 (with two DIFFERENT reasons), 429 and 503, and each one calls for a different
  // reaction from the player.

  it('a purchase just started asks to wait a few seconds, not to write to support', async () => {
    mocks.comprar.mockRejectedValueOnce({ status: 409, message: 'tracker_pass_pending' })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/wait a few seconds/i)).toBeTruthy()
    expect(screen.queryByText(/contact support/i)).toBeNull()
  })

  it('a stuck purchase asks to write to support, not to wait a few seconds', async () => {
    mocks.comprar.mockRejectedValueOnce({ status: 409, message: 'tracker_pass_pending_stuck' })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/contact support/i)).toBeTruthy()
    expect(screen.queryByText(/wait a few seconds/i)).toBeNull()
  })

  it('too many attempts asks to wait', async () => {
    mocks.comprar.mockRejectedValueOnce({ status: 429 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/too many attempts/i)).toBeTruthy()
  })

  it('with buying turned off or misconfigured, it gives a generic message', async () => {
    // With `pass_prices` empty this block does not even render, so this can only happen in a
    // race right when it gets disabled; a generic message is enough, not one per case.
    mocks.comprar.mockRejectedValueOnce({ status: 503 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/available right now/i)).toBeTruthy()
  })
})
