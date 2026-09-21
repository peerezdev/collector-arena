import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { HelpPage } from './HelpPage'

describe('HelpPage', () => {
  it('renders the modes and the platform-fee disclosure', () => {
    render(<MemoryRouter><HelpPage /></MemoryRouter>)
    expect(screen.getByText('How Collector Arena works')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Pack Battle' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Battle Royale' })).toBeTruthy()
    expect(screen.getByText('Platform fee on battles')).toBeTruthy()
    expect(screen.getByText('Withdrawal fee')).toBeTruthy()
  })

  it('las dos comisiones dicen su porcentaje y sobre qué se aplican', () => {
    // Son las dos cifras por las que alguien puede sentirse engañado si no cuadran, así que se
    // fijan aquí: si cambian en el backend, este test obliga a actualizar también lo que se
    // le promete al jugador.
    render(<MemoryRouter><HelpPage /></MemoryRouter>)
    const batalla = screen.getByText('Platform fee on battles').parentElement?.textContent ?? ''
    expect(batalla).toMatch(/0\.5% per player/)
    expect(batalla).toMatch(/capped at 3%/)
    expect(batalla).toMatch(/buyback value/)      // sobre el botín, no sobre los buy-ins
    expect(batalla).toMatch(/charged to the winner/)

    const retiro = screen.getByText('Withdrawal fee').parentElement?.textContent ?? ''
    expect(retiro).toMatch(/1%/)
    expect(retiro).toMatch(/minimum withdrawal is 1 USDC/)
  })

  it('el royale se anuncia como 5–10, no como 2–10', () => {
    render(<MemoryRouter><HelpPage /></MemoryRouter>)
    expect(screen.getByText(/5–10 PLAYERS/)).toBeTruthy()
    expect(screen.queryByText(/2–10 PLAYERS/)).toBeNull()
  })

  it('el ratio de Gimmighouls dice las dos cifras y coincide con el onboarding', () => {
    // Las cifras están escritas a mano en dos ficheros y el valor real vive en el backend
    // (gimmighoul_per_usdc). Sin nada que las ate, cambiar una y olvidar la otra le promete al
    // jugador dos cosas distintas en la misma app. El backend tiene su propio test recordándolo.
    render(<MemoryRouter><HelpPage /></MemoryRouter>)
    const texto = screen.getByText('Gimmighouls').parentElement?.textContent ?? ''
    expect(texto).toMatch(/0\.5 per dollar in battles/)
    expect(texto).toMatch(/0\.01 per dollar in gacha/)
  })

  it('ya no se anuncia la radio, que está apagada', () => {
    // RADIO_ENABLED = false en AppShell: describir un control que no existe manda al jugador a
    // buscarlo por la barra superior.
    render(<MemoryRouter><HelpPage /></MemoryRouter>)
    expect(screen.queryByText(/The radio/i)).toBeNull()
  })
})
