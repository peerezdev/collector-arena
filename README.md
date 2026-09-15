# Collector Arena

**Competitive games and market intelligence for tokenized trading cards on Solana.**

[Collector Crypt](https://collectorcrypt.com) tokenizes real, graded trading cards as NFTs on Solana
and sells them through gacha packs. Collector Arena is the competitive layer on top: players open
those packs against each other, and see what each gacha machine actually pays out before they pull.

Live on Solana mainnet since August 2026 at **[collectorarena.xyz](https://collectorarena.xyz)**.

---

## Features

### Pack Battles

Two to four players open packs from the same Collector Crypt machine, and the best pull takes the
cards. Every pull comes from Collector Crypt's own verifiable randomness, and settlement happens on
Solana.

### Battle Royale

A larger format for five to ten players, played across several rounds.

### Gacha

Open Collector Crypt packs directly in the app, sell a pulled card back instantly at the machine's
buyback rate, and redeem free packs earned through Collector Crypt points.

### Machine Tracker

Every gacha machine publishes its odds, but not what it has actually been paying out. The Machine
Tracker answers that, per machine: realized return per dollar over the last 48 hours, a confidence
interval, streaks since each rarity last dropped, and a comparison against a model built from the
machine's full card pool.

It is built on data that cannot be reconstructed after the fact. Collector Crypt's public API only
exposes the most recent 200 pulls per machine, so the tracker depends on having listened to the live
winners feed continuously. Access is unlocked by wagering 100 USDC in Pack Battles or Battle Royale
over a rolling seven day window.

### Social and progression

- Live chat with mentions, and USDC tips between players.
- Referral codes that share platform fees with the referrer.
- Gimmighouls loyalty points and a leaderboard.
- Claim and withdrawal of the Collector Crypt `$CARDS` airdrop.

---

## How it works

**No wallet friction.** Players sign in with email or an external wallet, and every account gets a
Privy embedded Solana wallet. An operator wallet pays network fees, so players never need SOL or a
seed phrase to play.

**Values players cannot game.** A card's value always comes from Collector Crypt's insured value,
never from a listing price that a player could influence.

**Composed from existing primitives.** Collector Arena does not reimplement what already exists on
Solana. It builds on Collector Crypt's gacha and NFT APIs, Privy embedded wallets, USDC and Metaplex
Core NFTs.

**Money is handled defensively.** Buy-ins are held in per-battle escrow wallets, available balance
always discounts funds already reserved for an unsettled battle, and on-chain transfers are confirmed
before they are treated as final.

---

## Repository layout

| Path | What it is |
|---|---|
| `src/` | Frontend: React 19, TypeScript, Vite, Tailwind, `@solana/kit`, Privy |
| `backend/` | API and game engine: Python, FastAPI, SQLAlchemy, SQLite |
| `oracle/` | Card pricing service that signs ed25519 value attestations |
| `onchain/` | Anchor program for trustless battles (built, not deployed to mainnet) |
| `deploy/` | Deployment scripts |
| `docs/` | Engineering notes (see below) |

---

## Running locally

The app runs three services against Solana devnet.

| Service | Folder | Port | Start |
|---|---|---|---|
| Oracle | `oracle/` | 8787 | `uvicorn app.main:app --port 8787` |
| Backend | `backend/` | 9090 | `uvicorn app.main:app --port 9090` |
| Frontend | repo root | 5173 | `npm run dev` |

1. Copy `.env.example` to `.env` in the repo root, and `backend/.env.example` to `backend/.env`.
   You will need a Privy app ID and a Solana RPC URL.
2. Install and start each service:

   ```bash
   # Frontend
   npm install
   npm run dev

   # Backend (Python 3.9+)
   cd backend
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt
   PYTHONPATH=. .venv/bin/uvicorn app.main:app --port 9090
   ```

3. The frontend reaches the backend through the Vite dev proxy.

`docs/STARTUP.md` has the full setup, including the oracle and the on-chain program.

---

## Tests

```bash
# Frontend
npm test
npx tsc -b        # type check

# Backend
cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q
```

---

## Documentation

Engineering notes live in `docs/`.

- `docs/COLLECTOR-CRYPT-API.md`: what we learned about the Collector Crypt API, including undocumented
  behavior.
- `docs/ESCROW-WALLETS.md`: how battle escrow wallets work.
- `docs/COMMANDS.md`: every operational command, from the gacha catalog to recovering stuck funds.
- `docs/STARTUP.md`: running every service locally.
- `docs/ONCHAIN.md`: the earlier on-chain mode built around the Anchor program.

`docs/superpowers/` keeps the internal design specs and implementation plans as they were written
during development, in Spanish.

---

## Status

Collector Arena is built by a solo founder. Development started in June 2026, and the product has
been live on Solana mainnet since August 2026.

Because development started before the Colosseum hackathon, commit messages up to September 2026
and many code comments are in Spanish. New work is written in English.
