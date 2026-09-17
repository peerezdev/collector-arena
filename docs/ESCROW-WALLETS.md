# Escrow wallets

Every game (Pack Battle or Royale) uses an escrow wallet: it receives the buy-ins, is the
`altPlayerAddress` of the pulls, and pays out at the end. They are Privy server wallets, and the
backend signs with them by `wallet_id`.

## What to understand before touching anything

**A Privy wallet is the same on every chain.** Same key pair, same address on devnet and mainnet.
What changes per network is what it **holds**.

That's where the split into two parts comes from:

| | Where it lives | What it stores |
|---|---|---|
| **Identity** | `escrow_inventory`, a **shared** database | address + Privy `wallet_id` |
| **State** | `escrow_wallets`, **each network's** database | free / in use / retained, `battle_id`, `times_used` |

That's why the same wallet can be **busy on devnet and free on mainnet** at the same time without
it being an error: they describe different chains.

Before the split, the pool mixed both in each network's database, with two consequences: mainnet
started empty and created new wallets while 79 unused ones already existed, and the only list of
which wallets are escrows lived in the **devnet** database, a test database that production depended
on.

## How a wallet is requested

`escrow_pool.adquirir()`, in order of preference:

1. A **free one from this network's pool** (`status = "free"`).
2. One **from the shared inventory that this network has never used**.
3. Only then, **a new one in Privy**, which is also registered in the inventory so that the network
   that first uses it doesn't keep it to itself.

## States

- **`free`**: empty and available.
- **`in_use`**: bound to a game.
- **`retained`**: when the game ended, it was found to **still hold something** (USDC or cards) and
  is not returned to the pool. It signals that something wasn't paid out; it isn't a normal state.
  There are 15 like this on devnet.

`liberar()` checks the chain before marking a wallet `free`: a wallet is never returned to the pool
based on what the database says, only on what the wallet actually holds.

## Configuration

```
ESCROW_INVENTORY_URL=sqlite:////absolute/path/escrow_inventory.db
```

**Empty = off**, and everything behaves as before. Step 2 only kicks in once it's configured.

**The path must be ABSOLUTE**: four slashes, `sqlite:////`. It's the same bug that led to
`scripts/_destino.py`: a relative SQLite path resolves against the working directory, so the backend
and a script launched from somewhere else would write to different inventories without any error.
And the damage here is worse than in a script: **two diverging inventories hand the same wallet to
two games**.

## Loading the inventory the first time

```bash
cd backend
PYTHONPATH=. .venv/bin/python3 scripts/seed_escrow_inventory.py --desde sqlite:///battlearena.db
PYTHONPATH=. .venv/bin/python3 scripts/seed_escrow_inventory.py --desde sqlite:///battlearena.db --go
```

Dry run by default, idempotent, and **it doesn't talk to Privy: it creates no wallets**. It copies
only the identity; state stays on each network.

Confirmed that the 79 devnet wallets are **completely clean on mainnet** (0 SOL, 0 token accounts,
0 transactions), so using them there carries nothing over.

Pending: registering the **5 mainnet wallets that predate the pool**.

## What is NOT shared

**Recovery sweeps are never combined.** `recover_escrow_usdc.py` and `sweep_stranded_cards.py` each
run against their own network: different database, different RPC, and `_destino.anunciar` stating
out loud what it's about to write to. Sharing wallet identity doesn't change that.

## Stranded gacha points

Battle pulls accumulate CC points in the escrow, not the player, because the escrow is the
`altPlayerAddress`. On devnet there are 3,069,133 spendable points spread across 57 escrows, and only
6 reach the minimum for a free pull. There is no way to move them: CC's points transfer is capped by
a sending allowance that only received transfers create, and escrows have never received one, so
their allowance is zero. The details are in [COLLECTOR-CRYPT-API.md](COLLECTOR-CRYPT-API.md).
