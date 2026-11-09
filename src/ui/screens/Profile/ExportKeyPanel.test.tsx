import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const EMBEDDED = 'FzRt4Pnh6tBpavXqkwQH1WVByeotDSefyyACKXC5kGHZ'
const PHANTOM = '3zdSWZAVfD6t9VHCjfiApjMnitnVUvFRtAR9yASjMi6x'
const ESCROW = '8z9AmkTAaQnVPFmpwqb1P9GoQX3E8TKxwc28dBk7vLbw'

const privy = vi.hoisted(() => ({
  state: { ready: true, authenticated: true, accounts: [] as unknown[] },
  exportWallet: vi.fn(),
}))
vi.mock('@privy-io/react-auth', () => ({
  usePrivy: () => ({
    ready: privy.state.ready,
    authenticated: privy.state.authenticated,
    user: { linkedAccounts: privy.state.accounts },
  }),
}))
vi.mock('@privy-io/react-auth/solana', () => ({
  useExportWallet: () => ({ exportWallet: privy.exportWallet }),
}))
const toast = vi.hoisted(() => vi.fn())
vi.mock('../../toastBus', () => ({ showToast: toast }))

import { ExportKeyPanel } from './ExportKeyPanel'

/** The embedded wallet Privy made for this player: the only exportable one. */
const embedded = (address = EMBEDDED) =>
  ({ type: 'wallet', walletClientType: 'privy', chainType: 'solana', address })

/** A Phantom the player linked at login. Privy did not make it, so it is not ours to export. */
const external = (address = PHANTOM) =>
  ({ type: 'wallet', walletClientType: 'phantom', chainType: 'solana', address })

// La etiqueta cambia a "Opening…" mientras el diálogo está abierto, así que el selector
// tiene que reconocer el botón en ambos estados.
const boton = () => screen.getByRole('button', { name: /export private key|opening/i })

beforeEach(() => {
  privy.state = { ready: true, authenticated: true, accounts: [embedded()] }
  privy.exportWallet.mockReset().mockResolvedValue(undefined)
  toast.mockReset()
})

describe('ExportKeyPanel', () => {
  it('exporta nombrando la dirección embedded, no la de índice 0 por defecto', async () => {
    render(<ExportKeyPanel />)
    fireEvent.click(boton())
    await waitFor(() => expect(privy.exportWallet).toHaveBeenCalledWith({ address: EMBEDDED }))
  })

  it('con una Phantom vinculada sigue exportando la embedded, nunca la externa', async () => {
    // El orden importa: la externa va primera para que un `find` descuidado la elija.
    privy.state.accounts = [external(), embedded()]
    render(<ExportKeyPanel />)
    fireEvent.click(boton())
    await waitFor(() => expect(privy.exportWallet).toHaveBeenCalledWith({ address: EMBEDDED }))
  })

  it('no ofrece exportar si la única wallet es externa', () => {
    privy.state.accounts = [external()]
    render(<ExportKeyPanel />)
    expect(boton()).toHaveProperty('disabled', true)
    fireEvent.click(boton())
    expect(privy.exportWallet).not.toHaveBeenCalled()
    expect(screen.getByText(/no embedded wallet/i)).toBeTruthy()
  })

  it('ignora una embedded de otra cadena', () => {
    privy.state.accounts = [{ type: 'wallet', walletClientType: 'privy', chainType: 'ethereum', address: '0xabc' }]
    render(<ExportKeyPanel />)
    expect(boton()).toHaveProperty('disabled', true)
  })

  it('no exporta un escrow del quórum colado como wallet externa', () => {
    // Los escrows los posee nuestro quórum: aquí llegarían con walletClientType ajeno a 'privy'.
    privy.state.accounts = [{ type: 'wallet', walletClientType: 'unknown', chainType: 'solana', address: ESCROW }]
    render(<ExportKeyPanel />)
    expect(boton()).toHaveProperty('disabled', true)
    fireEvent.click(boton())
    expect(privy.exportWallet).not.toHaveBeenCalled()
  })

  it('pide iniciar sesión si no está autenticado', () => {
    privy.state.authenticated = false
    render(<ExportKeyPanel />)
    expect(boton()).toHaveProperty('disabled', true)
    expect(screen.getByText(/log in to export/i)).toBeTruthy()
  })

  it('espera mientras Privy no está listo', () => {
    privy.state.ready = false
    render(<ExportKeyPanel />)
    expect(boton()).toHaveProperty('disabled', true)
    expect(screen.getByText(/loading/i)).toBeTruthy()
  })

  it('un fallo al abrir el diálogo avisa y deja el botón usable', async () => {
    privy.exportWallet.mockRejectedValue(new Error('MFA required'))
    render(<ExportKeyPanel />)
    fireEvent.click(boton())
    await waitFor(() => expect(toast).toHaveBeenCalledWith('MFA required', 'error'))
    await waitFor(() => expect(boton()).toHaveProperty('disabled', false))
  })

  it('no lanza dos exportaciones si se pulsa dos veces seguidas', async () => {
    let suelta: () => void = () => {}
    privy.exportWallet.mockReturnValue(new Promise<void>((r) => { suelta = () => r() }))
    render(<ExportKeyPanel />)
    fireEvent.click(boton())
    fireEvent.click(boton())
    expect(privy.exportWallet).toHaveBeenCalledTimes(1)
    suelta()
    await waitFor(() => expect(boton()).toHaveProperty('disabled', false))
  })

  it('avisa de que exportar no corta la firma delegada', () => {
    render(<ExportKeyPanel />)
    expect(screen.getByText(/does not end battle signing access/i)).toBeTruthy()
  })
})
