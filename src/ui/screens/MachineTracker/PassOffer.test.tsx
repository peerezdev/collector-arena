import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

const mocks = vi.hoisted(() => ({ comprar: vi.fn() }))
vi.mock('../../../onchain/gachaClient', () => ({ buyTrackerPass: mocks.comprar }))

import { PassOffer } from './PassOffer'

beforeEach(() => mocks.comprar.mockReset())

describe('la compra del pase, dentro de la puerta', () => {
  it('sin precios configurados NO se ofrece nada', () => {
    // Cero significa apagado. Un botón deshabilitado prometería algo que no existe.
    const { container } = render(<PassOffer prices={{}} token="t" onComprado={() => {}} />)
    expect(container.textContent).toBe('')
  })

  it('enseña las dos duraciones con su precio', () => {
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    expect(screen.getByText(/7 days/i)).toBeTruthy()
    expect(screen.getByText(/30 days/i)).toBeTruthy()
  })

  it('dice el precio POR DÍA, que es lo que deja comparar', () => {
    // 30 a 30 son 1.00/día; 7 a 10 son 1.43/día. Sin esta cifra nadie ve cuál sale mejor.
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    expect(screen.getByText(/1\.43/)).toBeTruthy()
    expect(screen.getByText(/1\.00/)).toBeTruthy()
  })

  it('avisa de que no hay devoluciones ANTES de pagar', () => {
    // Si compras 30 días y mañana apuestas 100, has pagado por algo que ya tenías.
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    expect(screen.getByText(/no refunds/i)).toBeTruthy()
  })

  it('comprar avisa a quien lo montó, para que refresque el acceso', async () => {
    mocks.comprar.mockResolvedValue({ pass_until: 1, days: 7, price_usdc: 10 })
    const onComprado = vi.fn()
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={onComprado} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    await waitFor(() => expect(onComprado).toHaveBeenCalled())
    expect(mocks.comprar).toHaveBeenCalledWith(7, 't')
  })

  it('mientras se cobra no se puede pulsar dos veces', async () => {
    // Un doble clic sobre un cobro son dos cobros.
    //
    // La promesa se deja RESOLVIBLE (con su propio resolver) y se resuelve al final del test, en
    // vez de una que no se resuelve nunca. Dejar una petición de verdad colgada para siempre hace
    // que el siguiente `beforeEach` de este archivo, que toca este mismo mock, se quede esperando
    // para siempre con ella: no es el componente, es cómo interactúan aquí el mock y el hook.
    let resolver!: (v: unknown) => void
    mocks.comprar.mockReturnValue(new Promise((r) => { resolver = r }))
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    const boton = screen.getByRole('button', { name: /7 days/i })
    fireEvent.click(boton)
    fireEvent.click(boton)
    expect(mocks.comprar).toHaveBeenCalledTimes(1)
    await act(async () => { resolver({ pass_until: 1, days: 7, price_usdc: 10 }) })
  })

  it('sin saldo lo dice con lo que hay que hacer, no con un error genérico', async () => {
    mocks.comprar.mockRejectedValueOnce({ status: 402 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/not enough usdc/i)).toBeTruthy()
  })

  it('un fallo de cobro dice que se reintente, y NO que deposite', async () => {
    // Tiene el dinero: pedirle depositar sería insultante y no arreglaría nada.
    mocks.comprar.mockRejectedValueOnce({ status: 502 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/try again/i)).toBeTruthy()
    expect(screen.queryByText(/deposit/i)).toBeNull()
  })

  // Los siguientes cuatro no vienen en la especificación original: los añade esta tarea porque
  // el backend puede devolver 409 (con dos motivos DISTINTOS), 429 y 503, y cada uno pide una
  // reacción distinta del jugador.

  it('una compra recién empezada pide esperar unos segundos, no escribir a soporte', async () => {
    mocks.comprar.mockRejectedValueOnce({ status: 409, message: 'tracker_pass_pending' })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/wait a few seconds/i)).toBeTruthy()
    expect(screen.queryByText(/contact support/i)).toBeNull()
  })

  it('una compra encallada pide escribir a soporte, no esperar unos segundos', async () => {
    mocks.comprar.mockRejectedValueOnce({ status: 409, message: 'tracker_pass_pending_stuck' })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/contact support/i)).toBeTruthy()
    expect(screen.queryByText(/wait a few seconds/i)).toBeNull()
  })

  it('demasiados intentos pide esperar', async () => {
    mocks.comprar.mockRejectedValueOnce({ status: 429 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/too many attempts/i)).toBeTruthy()
  })

  it('con la compra apagada o mal configurada da un mensaje genérico', async () => {
    // Con `pass_prices` vacío este bloque ni se renderiza, así que esto solo puede pasar en una
    // carrera justo cuando se apaga; basta con un mensaje genérico, no uno por caso.
    mocks.comprar.mockRejectedValueOnce({ status: 503 })
    render(<PassOffer prices={{ '7': 10, '30': 30 }} token="t" onComprado={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /7 days/i }))
    expect(await screen.findByText(/available right now/i)).toBeTruthy()
  })
})
