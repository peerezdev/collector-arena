# Collector Arena: on-chain program (Anchor / Solana)

The Anchor program for **Phase 1** of the SPEC: the trustless "automatic referee" that holds the USDC
stake in custody, runs the anti-cheat commit-reveal, resolves the battle deterministically (a faithful
port of the Phase 0 engine) and pays the winner, with no human intervention.

**Status:** validated on localnet/devnet with play money (a test SPL mint). **No real money, no audit,
no mainnet.** An audit is required before any deployment with real funds.

## Toolchain

The tools aren't on the PATH by default. At the start of each session:

```bash
export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$PATH"
```

Versions: rustc/cargo 1.96 (the workspace pins 1.89 via `rust-toolchain.toml`), solana-cli 3.1.10
(Agave), anchor-cli 1.0.2.

## Build and test

```bash
cd onchain
anchor build            # builds the program -> target/deploy/battle_arena.so + IDL

# tests: fast, in-process (litesvm), no validator
cd programs/battle_arena
cargo test              # 47 tests: pure logic + integration + equivalence + settlement + rejections
```

> Note: `anchor test` tries to use `surfpool` by default (not installed). If you need it, use
> `anchor test --validator legacy`. To iterate, `cargo test` is faster and needs no validator: the
> integration tests use **litesvm** (with the `precompiles` feature, needed to verify the oracle's
> ed25519 signature).

## Instruction flow

```
initialize_battle (A deposits) → join_battle (B deposits)
   → [per round] commit (×2) → reveal (×2) → resolve_round
   → ... until 2 wins or the round cap → settle (pays the winner / refunds on a draw)
claim_timeout: anti-grief forfeit if someone doesn't commit/reveal before the deadline
```

- **Accounts:** `Battle` PDA (`[b"battle", player_a, nonce_le]`), `escrow_vault` token PDA
  (`[b"vault", battle]`, authority = Battle PDA).
- **States:** `Created → Committing → Revealing → RoundResolved → Settled → Closed`.
- **Settle** binds destinations to on-chain identities: it pays only `player_a`/`player_b` (token
  accounts with a verified `owner`), sends the rake only to the `treasury` set in `initialize`, and
  transitions to `Closed` (anti-replay). The NFT is **never** transferred or held in custody: only
  ownership is verified (`amount ≥ 1`, correct owner).

## Two design decisions

1. **Integer edge (no floats).** The card value bonus is computed with integer comparisons
   (`compute_edge`): `+1` if `V_high ≥ V_low·2`, `+2` if `·8`, `+3` if `·32`, `+4` if `·128` (capped
   at 4). It's equivalent to `min(4, round(0.5·log2(ratio)))` but deterministic on-chain.
2. **Round cap.** A fully tied round → void round; `max_rounds` (default 5) prevents the escrow from
   getting stuck. If it runs out without 2 wins and the wins are tied → the battle is a draw, and each
   player gets their deposit back (no rake).

## Value oracle (signed attestation)

The value `(nft_mint, value_usd, grade, ts, battle)` is signed by an oracle with **ed25519**. The
program does **not** verify the signature in its own code: it introspects the `Instructions` sysvar and
confirms the transaction includes an instruction from the native Ed25519 program signing exactly that
message with the registered oracle pubkey (`oracle.rs`). The Ed25519 layout's instruction indexes are
required to be **self-referencing (`0xFFFF`)** to prevent the redirection attack (verifying a
legitimate signature from *another* instruction while comparing forged bytes). Attestations with a
stale `ts` (> 5 min) are rejected. The message is 81 bytes and includes the battle PDA, so an
attestation can't be reused in another battle.

## Faithful port guarantee

The rules engine (`edge.rs`, `rules.rs`, `hashing.rs`) is the Rust rewrite of the Phase 0 TypeScript
engine. Equivalence is proven with **vectors generated from the TS engine itself**
(`scripts/gen-vectors.ts` → `tests/fixtures/vectors.json`) that are replayed on-chain and must produce
the **same winner** (including the SPEC's §2.6 example battle, where the cheap card wins). All
resolution is integer; only `compute_edge` starts from a ratio and rounds to an integer.

## Layout

```
onchain/
  programs/battle_arena/src/
    lib.rs            # #[program]: declares the 7 instructions
    state.rs          # Battle (account), Phase, Allocation, MatchConfig, constants
    error.rs          # ErrorCode
    edge.rs           # compute_edge (integer)
    rules.rs          # resolve_front / resolve_round / solidez: endurance from the NFT grade, used as a tie-break (pure)
    hashing.rs        # commit_hash (canonical sha256, identical to the TS engine)
    oracle.rs         # ed25519 verification via introspection
    instructions/     # initialize, join, commit, reveal, resolve, settle, timeout
  programs/battle_arena/tests/
    common/mod.rs     # reusable litesvm harness
    integration.rs    # end-to-end happy path
    equivalence.rs    # replay of the TS engine vectors
    settlement.rs     # payouts, rake, draw, double settle
    rejections.rs     # access control and validation (incl. payout theft)
  scripts/gen-vectors.ts
```

## Residual risks (resolve BEFORE mainnet / real funds)

Acceptable for a localnet MVP, but recorded explicitly because they matter with real money (they came
out of the final review):

1. **Locked rent:** the `Battle` and `escrow_vault` accounts aren't closed after `settle`. The escrow
   USDC always leaves, but the SOL rent stays trapped per battle. A terminal close is missing (`close`
   in `settle` or a `close_battle` instruction) to return the rent to the payer.
2. ~~**Oracle attestation without a nonce bound to the battle.**~~ **RESOLVED**: the signed message now
   includes the battle PDA (81 bytes, see `attestation_msg` in `oracle.rs`), so an attestation can't be
   reused in another battle. Key rotation or a multi-signature oracle is still worth considering.
3. **NFT not locked:** only ownership (`amount ≥ 1`) is checked at that moment; the same NFT can back
   several simultaneous battles. Review whether scarcity should be enforced.
4. **Permissionless cranking:** `resolve_round`/`settle`/`claim_timeout` don't require a signer
   (correct and deterministic, but with no keeper or rate-limit model). Decide and document for
   mainnet.
5. **Mandatory security audit** (the escrow holds USDC) and legal review before any deployment with
   real funds.

## Next steps (other cycles)

A real oracle service (TCG Pricing Intelligence), backend/ELO/matchmaking, frontend + wallet adapter,
slug integration.
