import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

const mocks = vi.hoisted(() => ({ comprar: vi.fn(), saldo: vi.fn() }))
vi.mock('../../../onchain/gachaClient', () => ({ buyTrackerPass: mocks.comprar }))
vi.mock('../../../wallet/useUsdcBalance', () => ({ useUsdcBalance: () => mocks.saldo() }))

import { PassOffer } from './PassOffer'

beforeEach(() => {
  mocks.comprar.mockReset()
  mocks.saldo.mockReturnValue({ usdc: 50, loading: false })
})

/** Buy the way a person does: pick the duration, then confirm. */
function comprar(dias: 7 | 30) {
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`${dias} days`, 'i') }))
  fireEvent.click(screen.getByRole('button', { name: /confirm and pay/i }))
}

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
    comprar(7)
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
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    const confirmar = screen.getByRole('button', { name: /confirm and pay/i })
    fireEvent.click(confirmar)
    fireEvent.click(confirmar)
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
    comprar(7)
    await waitFor(() => expect(onComprado).toHaveBeenCalledTimes(1))
    // The gate stays mounted (nobody unmounted it), so everything that can still be clicked is
    // clicked again: the duration button (which now reads "Paying…") and the one in the dialog.
    screen.getAllByRole('button').forEach((b) => fireEvent.click(b))
    expect(mocks.comprar).toHaveBeenCalledTimes(1)
  })

  it('with no balance, it says what needs to be done, not a generic error', async () => {
    mocks.comprar.mockRejectedValueOnce({ status: 402 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    comprar(7)
    expect(await screen.findByText(/not enough usdc/i)).toBeTruthy()
  })

  it('a charge failure says to retry, and NOT to deposit', async () => {
    // They have the money: asking them to deposit would be insulting and would fix nothing.
    mocks.comprar.mockRejectedValueOnce({ status: 502 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    comprar(7)
    expect(await screen.findByText(/try again/i)).toBeTruthy()
    expect(screen.queryByText(/deposit/i)).toBeNull()
  })

  // The following four are not in the original spec: this task adds them because the backend
  // can return 409 (with two DIFFERENT reasons), 429 and 503, and each one calls for a different
  // reaction from the player.

  it('a purchase just started asks to wait a few seconds, not to write to support', async () => {
    mocks.comprar.mockRejectedValueOnce({ status: 409, message: 'tracker_pass_pending' })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    comprar(7)
    expect(await screen.findByText(/wait a few seconds/i)).toBeTruthy()
    expect(screen.queryByText(/contact support/i)).toBeNull()
  })

  it('a stuck purchase asks to write to support, not to wait a few seconds', async () => {
    mocks.comprar.mockRejectedValueOnce({ status: 409, message: 'tracker_pass_pending_stuck' })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    comprar(7)
    expect(await screen.findByText(/contact support/i)).toBeTruthy()
    expect(screen.queryByText(/wait a few seconds/i)).toBeNull()
  })

  it('too many attempts asks to wait', async () => {
    mocks.comprar.mockRejectedValueOnce({ status: 429 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    comprar(7)
    expect(await screen.findByText(/too many attempts/i)).toBeTruthy()
  })

  it('with buying turned off or misconfigured, it gives a generic message', async () => {
    // With `pass_prices` empty this block does not even render, so this can only happen in a
    // race right when it gets disabled; a generic message is enough, not one per case.
    mocks.comprar.mockRejectedValueOnce({ status: 503 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    comprar(7)
    expect(await screen.findByText(/available right now/i)).toBeTruthy()
  })
})

describe('nothing gets charged without confirming first', () => {
  it('picking a duration opens the confirmation and charges NOTHING', () => {
    // Money left the wallet on a single click, with no way back. The duration button now only
    // asks; the charge needs a second, deliberate act.
    render(<PassOffer prices={{ '7': 6.99, '30': 19.99 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))

    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(mocks.comprar).not.toHaveBeenCalled()
  })

  it('the 30 day one also asks, and confirming charges THAT one', async () => {
    mocks.comprar.mockResolvedValue({ pass_until: 1, days: 30, price_usdc: 19.99 })
    render(<PassOffer prices={{ '7': 6.99, '30': 19.99 }} token="t" onComprado={() => {}} />)
    comprar(30)
    await waitFor(() => expect(mocks.comprar).toHaveBeenCalledWith(30, 't'))
  })

  it('the confirmation says what is charged, from where, and until when', () => {
    // Everything you need to decide, without leaving the dialog. The date is the one thing you
    // cannot work out in your head.
    render(<PassOffer prices={{ '7': 6.99, '30': 19.99 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))

    const dialogo = screen.getByRole('dialog')
    expect(dialogo.textContent).toMatch(/6\.99/)
    expect(dialogo.textContent).toMatch(/7 days/i)
    expect(dialogo.textContent).toMatch(/no refunds/i)
    const hasta = new Date(Date.now() + 7 * 86400_000).toLocaleDateString()
    expect(dialogo.textContent).toContain(hasta)
  })

  it('it shows the balance, which is the question you ask yourself before confirming', () => {
    mocks.saldo.mockReturnValue({ usdc: 12.5, loading: false })
    render(<PassOffer prices={{ '7': 6.99, '30': 19.99 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(screen.getByRole('dialog').textContent).toMatch(/12\.5/)
  })

  it('with not enough balance it says so and does not let you pay', () => {
    // Better here than as a 402 from the backend after a round trip.
    mocks.saldo.mockReturnValue({ usdc: 2, loading: false })
    render(<PassOffer prices={{ '7': 6.99, '30': 19.99 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))

    expect(screen.getByRole('dialog').textContent).toMatch(/not enough usdc/i)
    expect(screen.getByRole('button', { name: /confirm and pay/i }).hasAttribute('disabled')).toBe(true)
    expect(mocks.comprar).not.toHaveBeenCalled()
  })

  it('cancelling closes it and charges nothing', () => {
    render(<PassOffer prices={{ '7': 6.99, '30': 19.99 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(mocks.comprar).not.toHaveBeenCalled()
  })

  it('Escape closes it too, as long as nothing is being charged', () => {
    render(<PassOffer prices={{ '7': 6.99, '30': 19.99 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(mocks.comprar).not.toHaveBeenCalled()
  })

  it('once the charge is in flight, Escape does NOT close it', async () => {
    // Closing mid-charge leaves you not knowing whether you paid, which is the worst moment to
    // be left without a screen to read.
    let resolver!: (v: unknown) => void
    mocks.comprar.mockReturnValue(new Promise((r) => { resolver = r }))
    render(<PassOffer prices={{ '7': 6.99, '30': 19.99 }} token="t" onComprado={() => {}} />)
    comprar(7)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('dialog')).toBeTruthy()

    await act(async () => { resolver({ pass_until: 1, days: 7, price_usdc: 6.99 }) })
  })
})
