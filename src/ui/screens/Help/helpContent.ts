import { COLORS } from '../../theme'

export interface HelpMode { id: string; name: string; tag: string; accent: string; desc: string; steps: string[]; iconPaths: string }
export interface HelpFeature { title: string; body: string; accent: string; iconPaths: string }

export const HELP_MODES: HelpMode[] = [
  { id: 'pack', name: 'Pack Battle', tag: '2–4 PLAYERS · WINNER TAKES ALL', accent: COLORS.green,
    desc: 'Two to four players put up the same buy-in and open the same packs at the same time, one pack or several each. Every card adds to your total, and the highest total takes every card on the table.',
    steps: ['Pick a machine (or a bundle of several packs), choose 2 to 4 seats, and create or join.', 'Everyone opens at the same time, pack by pack.', 'The highest accumulated insured value takes all the cards.'],
    iconPaths: '<polyline points="14.5 17.5 3 6 3 3 6 3 17.5 14.5"/><line x1="13" x2="19" y1="19" y2="13"/><line x1="16" x2="20" y1="16" y2="20"/><line x1="19" x2="21" y1="21" y2="19"/><polyline points="14.5 6.5 18 3 21 3 21 6 17.5 9.5"/><line x1="5" x2="9" y1="14" y2="18"/><line x1="7" x2="4" y1="17" y2="20"/><line x1="3" x2="5" y1="19" y2="21"/>' },
  { id: 'royale', name: 'Battle Royale', tag: '5–10 PLAYERS · LAST ONE STANDING', accent: '#ff6bb5',
    desc: 'A lobby of 5 to 10 players opens packs in synchronized rounds. After each round the lowest accumulated value is eliminated. The last player standing takes every card and whatever USDC is left in the pot.',
    steps: ['Join a lobby and wait for the seats to fill. Your buy-in covers your share of every round, not just your first pull.', 'Each round the survivors open a pack; the lowest running total drops out.', 'Outlast everyone to take the whole pot.'],
    iconPaths: '<path d="M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519l-2.834 10.246a1 1 0 0 1-.956.734H5.81a1 1 0 0 1-.957-.734L2.02 6.02a.5.5 0 0 1 .798-.52l4.276 3.664a1 1 0 0 0 1.516-.294z"/><path d="M5 21h14"/>' },
  { id: 'gacha', name: 'Gacha', tag: 'SOLO · PULL → PLAY', accent: '#a98bff',
    desc: 'Open Collector Crypt packs on your own to build your collection. Every card you pull is a real graded NFT you own, and you can take it straight into a battle. No platform fee on solo pulls.',
    steps: ['Choose a machine by set and price tier.', 'Pull your card, with a provably-fair reveal.', 'Keep it, or jump straight into a battle.'],
    iconPaths: '<rect x="3" y="3" width="12" height="17" rx="1.2"/><path d="M3 9h12M3 15h12M7 9v6M11 9v6"/><path d="M5.5 5.5h7M5.5 7h7"/><path d="M15 11h2v3h-2"/><circle cx="19.5" cy="6" r="2"/><path d="M19.5 8v3"/>' },
]

export const HELP_FEATURES: HelpFeature[] = [
  { title: 'Your wallet', accent: COLORS.green, body: 'A Solana wallet is created for you when you sign in. No seed phrase to write down and nothing to install. You deposit USDC into it, and you can withdraw to any Solana address whenever you are not in a battle.', iconPaths: '<rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" x2="22" y1="10" y2="10"/>' },
  { title: 'Available vs. reserved', accent: COLORS.green, body: 'Your balance splits in two. Reserved is what is committed to lobbies you have joined; available is what is left to spend or withdraw. Joining a battle moves money from one to the other, and it comes back if the battle is cancelled.', iconPaths: '<circle cx="12" cy="12" r="9"/><path d="M12 3v9l6 3"/>' },
  { title: 'Platform fee on battles', accent: '#4ea8ff', body: 'Battles charge 0.5% per player, capped at 3%. Two players is 1%, four is 2%, six or more is 3%. It applies to the buyback value of the whole pot, not to the buy-ins, and it is charged to the winner in USDC once the payout lands. Solo gacha pulls pay nothing.', iconPaths: '<circle cx="12" cy="12" r="9"/><path d="M15 9.5a3 3 0 0 0-3-1.5c-1.7 0-3 1-3 2.2 0 2.8 6 1.4 6 4.1 0 1.3-1.3 2.2-3 2.2a3 3 0 0 1-3-1.5"/><path d="M12 6v12"/>' },
  { title: 'Withdrawal fee', accent: '#4ea8ff', body: 'Sending USDC out costs 1% of the amount, taken from what you withdraw, so the destination receives the rest. The minimum withdrawal is 1 USDC, and withdrawals are closed while you are in a lobby or a battle that has not finished.', iconPaths: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 20h16"/>' },
  { title: 'Where the money sits', accent: '#4ea8ff', body: 'Buy-ins and every card pulled in a battle are held in a wallet dedicated to that battle while it runs, and released automatically when it ends: the pot and the cards to the winner, or a refund to everyone if it is cancelled. Cards are real NFTs and land in your wallet, yours to keep, sell back or withdraw.', iconPaths: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>' },
  { title: 'Gimmighouls', accent: '#f5c542', body: 'Points you earn just by playing: 0.5 per dollar in battles and 0.01 per dollar in gacha. They are not a currency and they do not affect your odds. They track how much you have played and feed the ranking.', iconPaths: '<circle cx="12" cy="12" r="8"/><path d="M12 8v8M9 12h6"/>' },
  { title: 'Provably fair', accent: COLORS.green, body: 'Every pull is verifiable, and so is any tie: when two players finish level, the draw is decided by a seed committed before the battle started. The card edge comes from insured value, not from prices anyone can move.', iconPaths: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>' },
  { title: 'Recent drops', accent: COLORS.green, body: 'A live feed of cards pulled across the arena. Your own drops are highlighted so you can spot them at a glance.', iconPaths: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>' },
]
