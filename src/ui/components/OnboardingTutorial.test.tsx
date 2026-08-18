import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { OnboardingTutorial } from './OnboardingTutorial'

describe('OnboardingTutorial', () => {
  it('abre por la WALLET: es lo primero que hay que entender', () => {
    // Aquí se juega con dinero real. Antes se abría con un saludo y los packs, y lo de la wallet
    // no aparecía en ningún paso.
    render(<OnboardingTutorial onClose={vi.fn()} />)
    expect(screen.getByText('A wallet, without the wallet part')).toBeTruthy()
    expect(screen.getByText('1 / 5')).toBeTruthy()
  })

  it('el orden es wallet, royale, pack, gacha, gimmighouls', () => {
    render(<OnboardingTutorial onClose={vi.fn()} />)
    const titulos = ['A wallet, without the wallet part', 'Outlast 9 other collectors',
                     'Same packs, everyone at once', 'Or just open packs', 'Points for showing up']
    titulos.forEach((t, i) => {
      expect(screen.getByText(t)).toBeTruthy()
      if (i < titulos.length - 1) fireEvent.click(screen.getByText('Next'))
    })
    expect(screen.getByText("Let's go")).toBeTruthy()   // último paso
    expect(screen.queryByText('Next')).toBeNull()
  })

  it('the last-step CTA closes the tour', () => {
    const onClose = vi.fn()
    render(<OnboardingTutorial onClose={onClose} />)
    for (let i = 0; i < 4; i++) fireEvent.click(screen.getByText('Next'))
    fireEvent.click(screen.getByText("Let's go"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('el paso de Gimmighouls dice el mismo ratio que el help', () => {
    render(<OnboardingTutorial onClose={vi.fn()} />)
    for (let i = 0; i < 4; i++) fireEvent.click(screen.getByText('Next'))
    expect(screen.getByText(/0\.5 per dollar in battles/)).toBeTruthy()
    expect(screen.getByText(/0\.01 in gacha/)).toBeTruthy()
  })

  it('SKIP TOUR closes the tour immediately', () => {
    const onClose = vi.fn()
    render(<OnboardingTutorial onClose={onClose} />)
    fireEvent.click(screen.getByText('SKIP TOUR'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
