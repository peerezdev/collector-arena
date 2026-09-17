# Collector Arena: on-chain mode (devnet)

> **Historical note.** This guide documents the earlier on-chain mode, built around the Anchor
> program in `onchain/` and its pricing oracle. That program is **built but not deployed to
> mainnet**. The product live on mainnet today runs through the backend, Privy embedded wallets and
> Collector Crypt's APIs, as described in the [README](../README.md).

A guide to running and verifying the frontend's **On-chain (devnet)** mode: connect a wallet (Reown
AppKit / WalletConnect), view the collection with oracle values, create and join games in the lobby,
and play a real battle (commit-reveal + settlement) against the Anchor program on devnet.

The **Practice (offline)** mode (the Phase 0 mock engine) remains available without a wallet, to play
and validate fun.

> **Status:** the on-chain SDK is tested (PDAs, the Ed25519 instruction pinned to the shared test
> vector, builders, clients). The React layer compiles and is ready, but **you verify the real
> wallet + devnet flow** with the checklist below: browser signing can't be automated. Along the way,
> this walkthrough validates the pending skeletons (the backend chain reader and the real Collector
> Crypt resolver).

## On-chain frontend architecture

```
src/onchain/   Pure SDK (tested): pdas, attestation (Ed25519), instructions, oracle/backend clients, config, types
src/wallet/    Reown AppKit (Solana devnet) + useWallet hook (publicKey, connect, signAndSendTransaction, signMessage)
src/ui/screens/onchain/   ConnectScreen, CollectionScreen, LobbyScreen, OnchainBattleScreen
src/mode/ModeSelect.tsx    Practice (offline) | On-chain (devnet)
```

Transactions are signed and sent **from the client** (the devnet RPC accepts `sendTransaction`). On
mainnet this would move to server-side broadcast (as in MarketAgg), and the attestation would be bound
to the battle by nonce.

## Environment variables (`.env`)

```
VITE_SOLANA_RPC=https://api.devnet.solana.com
VITE_PROGRAM_ID=89qGDjXGcV9zi3968DtRLNzBn5KXhYmSGJkjKntksCdk
VITE_ORACLE_URL=http://localhost:8787
VITE_BACKEND_URL=http://localhost:5173   # same origin; routed by the Vite proxy
VITE_REOWN_PROJECT_ID=<your Reown/WalletConnect Cloud project id>
VITE_STAKE_MINT=<SPL mint for the stake; on devnet, a test USDC you control>
VITE_TREASURY=<SPL token account of the treasury for the rake>
```

## Starting the services

```bash
# Oracle (signs value attestations)
cd oracle && source .venv/bin/activate && PRICING_SOURCE=collectorcrypt uvicorn app.main:app --port 8787

# Backend (ELO + lobby)
cd backend && source .venv/bin/activate && uvicorn app.main:app --port 9090

# Frontend
npm run dev   # http://localhost:5173
```

## Gacha (devnet)

The full flow for acquiring cards on-chain through the Gacha module (surprise machines):

1. **Get a `GACHA_API_KEY`:** request one on the Collector Crypt Discord and store it in
   `backend/.env`:
   ```env
   GACHA_API_KEY=<your-key>
   ```
   Without this key, the `/gacha/*` endpoints respond `503 gacha_disabled`. The Gacha API is consumed
   through the backend proxy, and **the `x-api-key` is never exposed to the browser**.

2. **Devnet USDC:** get test funds from the faucet:
   - URL: https://spl-token-faucet.com/?token-name=USDC-Dev
   - Mint (USDC-Dev): `Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr`
   - Minimum for gacha: 50 USDC per pack.

3. **In the app:**
   - App → **On-chain (devnet)** mode → connect wallet → go to **Collection**.
   - **«🎰 Gacha»** button → pick a machine.
   - **«Open pack»** (50 USDC) → sign in the wallet → cinematic card reveal.

4. **Use the card in battle:**
   - **«Create challenge with this card»** button → the new card appears in your Collection.
   - Oracle attestation: OK (the oracle validates the new card automatically).
   - Create a battle and play it normally (against another player).

> **Architecture note:** the browser never sees the `x-api-key`. On-chain transactions (USDC payment,
> NFT mint) are signed in the wallet; the card reveal is off-chain (JSON).

## Devnet verification checklist (you run it)

1. **Toolchain + funds:**
   ```bash
   export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$PATH"
   solana config set --url devnet
   solana airdrop 2          # devnet SOL to deploy and sign
   ```
2. **Deploy the program to devnet:**
   ```bash
   cd onchain && anchor build && anchor deploy --provider.cluster devnet
   # check that the deployed program id == VITE_PROGRAM_ID (89qGDjX…ksCdk).
   # if it differs, anchor keys sync + redeploy, and update VITE_PROGRAM_ID.
   ```
3. **Oracle:** `GET http://localhost:8787/pubkey` → note the `oracle_pubkey`. It's passed as `oracle`
   when creating the battle (the contract verifies the signature against it).
4. **Test stake mint:** create an SPL mint on devnet (or use a test USDC you control), mint balance to
   your two test wallets, and put its address in `VITE_STAKE_MINT`. Create the treasury token account
   and put it in `VITE_TREASURY`.
5. **NFTs:** have at least one Collector Crypt card (or a test mint) in each wallet whose `mint` the
   oracle can value (with `insuredValue`). If the oracle runs in `mock`, any mint returns a value; in
   `collectorcrypt`, it must be a real NFT with `insuredValue`.
6. **Play (2 wallets):**
   - Wallet A: `npm run dev` → **On-chain (devnet)** → connect Phantom → authenticate → Collection:
     paste the mint and "Value" → Lobby → **Create** (stake + optional ELO limits) → sign
     `initialize_battle`.
   - Wallet B: connect → Lobby → the game appears with your **ELO difference** and `joinable` →
     **Join** → sign `join_battle`.
   - Both: each round, allocate energy → **Commit** (sign) → **Reveal** (sign) → **Resolve** (anyone).
     Once decided, **Settle** pays the winner.
   - Check the updated ELO: `GET http://localhost:9090/elo/compare?a=<A>&b=<B>`.

## What remains to validate against real data (when running the above)

- **Backend chain reader** (`SolanaChainSource`): currently a skeleton; when running devnet,
  implement and validate decoding of the `Battle` account so `sync` derives the real result (or, in the
  meantime, keep the backend on `CHAIN_SOURCE=mock` seeding states).
- **Real Collector Crypt resolver** (oracle): validate the field mapping against a real API response
  with a real mint.
- **Exact Reown AppKit API** and the signing flow: confirm connection, `signMessage` (auth) and
  `signAndSendTransaction` with a real Phantom.

## Before mainnet (reminder)

- Server-side transaction broadcast (the public mainnet RPC returns 403 on `sendTransaction`).
- ~~Bind the oracle attestation to the battle to prevent reuse within the freshness window.~~
  **RESOLVED**: the signed message includes the battle PDA (81 bytes), the `/attest?battle=<pubkey>`
  endpoint is mandatory, and the contract checks that the battle embedded in the signature matches the
  instruction's account.
- Contract audit + legal review (already noted in the program and oracle risk sections).
