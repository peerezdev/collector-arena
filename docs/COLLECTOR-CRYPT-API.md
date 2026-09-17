# The Collector Crypt API: what we know

Notes on what we learned using the CC API, including the parts that are **undocumented** and the
parts that are documented but **behave differently**. Everything here was measured against the real
API, not inferred; where it wasn't, we say so.

Official documentation: <https://docs.collectorcrypt.com/>

| | devnet | mainnet |
|---|---|---|
| Gacha | `https://dev-gacha.collectorcrypt.com` | `https://gacha.collectorcrypt.com` |
| NFT metadata | `https://nft-dev.collectorcrypt.com` | `https://nft.collectorcrypt.com` |

The hosts **are not interchangeable**: a devnet memo looked up against the mainnet host does not
show up. That cost us an hour believing the VRF was broken.

An empty `gacha_base_url` disables the whole gacha. It is the kill switch.

---

## The most important thing: `altPlayerAddress` is NOT just delivery

The documentation presents it as "where to send the card". In practice **it also takes the points
and the VRF attribution**. As far as CC is concerned, the `altPlayerAddress` is who made the pull.

Consequences that bit us:

- **Gacha points go to the escrow.** In Pack Battle and Royale the escrow is the
  `altPlayerAddress`, so the points from every battle pull were accumulated by the escrow, not the
  player. On devnet that left **3,069,133 spendable points spread across 57 escrows**. Split up
  like that, only **6 wallets** reach the 100,000 needed for a free pull on the cheapest machine:
  about 19 recoverable pulls out of 3 million points. On the expensive machines, none reach it.

  **And they cannot be moved to another wallet.** `transferBonusPoints` only transfers points
  received by transfer, not points earned by pulling: see its section below. Those points are
  spent where they are or not at all.
- **CC's public feed attributes the pull to the escrow.** A player looking at their history on CC
  does not see their battle pulls. The escrow does.
- **So does the VRF.** `GET /api/vrf/verify` returns the escrow wallet, not the player's.

Real attribution happens through the `x-api-key` header, which identifies the integrator. We don't
send one (devnet is keyless), so we have no way to tell CC "this pull belongs to this user" through
the API.

**So how do we prove a pull belongs to a player?** Through the chain. See the memo section below.
It is the only proof that doesn't require trusting either our database or CC's.

---

## Documented endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/status` | `code → status`; `open` = machine available. We read it **fail-open**: if it fails, the machine is assumed available. |
| GET | `/api/machines` | machine catalog |
| GET | `/api/getNfts` | a machine's cards; value comes **only** from `insuredValue` |
| POST | `/api/generatePack` | returns `memo` + an unsigned `transaction` |
| POST | `/api/generateYoloPacks` | several packs at once |
| POST | `/api/submitTransaction` | CC sends it and **pays the fee** |
| POST | `/api/openPack` | opens by `memo`; `WAITING_FOR_WEBHOOK` = retry |
| GET | `/api/buyback/available`, POST `/api/buyback` | buyback |
| GET | `/api/getAllWinners` | public feed |
| GET | `/api/vrf/verify?memo=` | see below |

### The flow of a pull

```
generatePack       →  unsigned transaction
                      signed by the PLAYER (the key never leaves)
submitTransaction  →  CC sends it on-chain and pays the fee
openPack(memo)     →  the card
```

We only sign; **CC pays the pull's fee**, not our operator.

### `/api/vrf/verify`

Two traps, both cost time:

1. The memo you pass goes **without the `:open` suffix**. The on-chain memo is `cc-<uuid>:open`;
   the endpoint takes `cc-<uuid>`.
2. The host has to be the one for the network where the pull happened.

Even getting both right, the response attributes the pull to the `altPlayerAddress`.

---

## Undocumented endpoints

Found by watching the network tab of their own website. **They can change or disappear without
notice**: everything we read from them uses `.get` with a default, never indexing.

### `GET /api/freeSpins?wallet=`

| field | what it is |
|---|---|
| `points` | accumulated points |
| `usedPoints` | already spent on free pulls → **what's spendable is the difference** |
| (difference) | that difference is what's **spendable on pulls**, and it is NOT what's transferable: see `transferBonusPoints` |
| `freeSpinsLeftToday` | remaining daily cap |
| `freeSpinsLeft`, `pointsPerSpin`, `pointsUntilNextSpin` | **see the warning below** |

**It belongs to the WALLET, not the machine.** It accepts `wallet` and nothing else: we passed it
`packType`, `machine` and `code`, and the response didn't change.

**Careful with `freeSpinsLeft` and `pointsPerSpin`.** They look like the answer to "how many free
pulls do I have?", but they are **always calculated against a $50 machine**. A free pull doesn't
cost the same everywhere: it costs 100,000 points on the $50 machine and **scales with price**, so
on the $5,000 machine it is 10 million. Reading them as-is advertised three free pulls on a machine
where there wasn't enough for one.

The formula, exactly as their own website uses it:

```js
required   = Math.round(100_000 * (price / 50))
available  = points - usedPoints
pulls      = Math.floor(available / required)
remainder  = available % required
untilNext  = (remainder === 0 && available > 0) ? 0 : required - remainder
```

Checked against a real wallet with 364,060 points: the API says `freeSpinsLeft: 3` and
`pointsUntilNextSpin: 35,940`, which is exactly what the formula gives **for the base price**. On
the $250 machine those same points give none.

We implement it twice on purpose: `tiradas_gratis()` in `app/services/gacha.py` (the gate) and
`tiradasGratis()` in `src/ui/screens/gacha/freeSpins.ts` (what gets rendered).

### Which machines offer free pulls

Two conditions, and neither depends on the player:

- **`machine.freeSpins`**, in `/api/machines`. Many machines don't offer them: 3 of 9 on devnet, 16
  of 43 on mainnet. Requesting one where it's off → `400 "Invalid pack type"`.
- **`freePacksStatus`**, in `/api/status`. A **global** CC switch: when `closed`, there are no free
  pulls on any machine.

The validation order of `freePack`, measured: **nonce** → machine type → signature → points.

### `POST /api/generateFreePack` + `POST /api/freePack`

**CC hardened this redemption without notice, and it broke ours completely** (2026-08-13). Any
signed transaction used to be enough; now there is a two-step challenge. With the old format it
responds `400 {"error":"Missing or invalid nonce"}`, and no free pull can be redeemed.

**And ONLY ON MAINNET.** Devnet still runs the old contract: there `/api/generateFreePack` **doesn't
exist** (404) and `/api/freePack` accepts the old format. Also confirmed in their frontend, which on
devnet sends the body without `nonce` and never calls `generateFreePack`: their deployment there
simply lags behind.

Requiring the nonce on both networks left devnet with a 502 and no free pulls, so the code decides
at runtime: `generate_free_pack` returns `None` when the endpoint gives a 404, and the proof falls
back to the usual `build_memo_tx`. The 404 is distinguished from other failures with
`GachaEndpointMissing`; any other error propagates, because treating a CC outage as "this is the
old network" would send the old format to mainnet and the player would see the nonce error.

When devnet is updated nothing needs to change: as soon as `generateFreePack` stops returning 404,
redemption moves to the new flow on its own.

```
POST /api/generateFreePack  {publicKey, packType}   →  {nonce, expiry}   (minutes)
POST /api/freePack          {publicKey, packType, turbo, transactionSignature, nonce}  →  {memo}
```

The `nonce` travels **two ways at once**, and CC checks both:

1. In the body of `/api/freePack`.
2. **Inside the signed transaction**, as the content of a memo instruction that also lists the
   wallet in its accounts **marked as a signer**. That flag is what binds the nonce to the wallet:
   without it, the memo would just be text anyone could have written.

The transaction mirrors the one from their website: a 0-lamport transfer from the wallet to
itself, plus the memo with the nonce. It is built by `build_free_pack_proof_tx` in
`app/services/solana_tx.py`; `build_memo_tx`, with its fixed text, **no longer works for this**.

The validation order, measured: **the nonce is checked before the signature** (with a garbage
signature and no nonce, the error is the nonce one).

Everything else is unchanged:

- **`transactionSignature` is not sent on-chain.** It acts as proof of ownership, not payment.
- **`altPlayerAddress` is accepted in the body but ignored.** The card **always** goes to
  `publicKey`. Confirmed on-chain. That's why escrow points can't be turned into cards for a
  player: the free pack is received by the escrow.
- Not every machine supports it.

### `GET /api/user/bonusTransfers?wallet=`

Point transfer history.

### `POST /api/user/transferBonusPoints` (and `/prepare`)

Moves points from one wallet to another. **It works, and we know how to use it from the server**,
but it can't rescue the points stuck in escrows, for the reason at the end of this section.

This section used to say `/prepare` returned 401 and there was no way around it. That diagnosis was
wrong: the 401 came from sending a token with another network's `aud`.

**The validation order**, measured: sending to yourself → minimum amount → authorization. Since
authorization is checked last, a 401 here guarantees the body was valid.

- **Minimum 1,000 points** per transfer.
- **You can't send to yourself.**

#### Authorization: a Privy JWT we mint ourselves

The header is `Authorization: Bearer <JWT>`, a Privy token for **CC's app**, and CC binds it to the
`fromWallet`: with another wallet's token it returns 401 even if the body is correct.

Nobody needs to log in by hand. The token is minted server-side through Privy's
Sign-In-With-Solana login, signing the message with the wallet:

```
POST auth.privy.io/api/v1/siws/init          {address}                → nonce
     sign the message with the wallet (Privy: signMessage, base64)   → signature
POST auth.privy.io/api/v1/siws/authenticate  {message, signature, …}  → token (24 h)
```

Three things that make the login fail if missing:

- An `origin` header with CC's host, or Privy responds `403 missing_origin`.
- A browser `User-Agent`: without it, a Cloudflare 403. It is the same stumble we already
  documented in `privy_signer._wallet`.
- The **exact text** of the message. Any variation gives `invalid_data`. It's the one from CC's
  website:

```
<host> wants you to sign in with your Solana account:
<address>

You are proving you own <address>.

URI: https://<host>
Version: 1
Chain ID: mainnet
Nonce: <nonce>
Issued At: <ISO-8601>
Resources:
- https://privy.io
```

**Each CC network has its own Privy app**, and mixing them up is exactly the 401 behind the
earlier wrong diagnosis:

| network | `privy-app-id` |
|---|---|
| mainnet | `cmdgt21w400lgky0mkn069jui` |
| devnet | `cmcwv1wi201tnjm0mmexyzxyi` |

**It doesn't work on devnet**: that app has an allowlist and the login responds
`401 allowlist_rejected` for any of our wallets. This is **mainnet only**.

#### The flow

```
prepare(fromWallet, toWallet, amount)   →  {nonce, expiry (~5 min), transferable}
sign a memo with the wallet             →  proof of ownership
transferBonusPoints(… nonce, signedTransaction)  →  {transferred, newBonusPoints, newPointsRemaining}
```

The `signedTransaction` is a signed memo that isn't sent on-chain, built with the usual
`build_memo_tx` and its fixed text. **Note that the two proofs of ownership are no longer the
same**: `freePack` now requires the nonce inside the memo (see its section) and this one doesn't.
Confirmed on 2026-08-13: sending points still accepts the simple memo.

#### The "bonus" bucket is an ALLOWANCE, and it's what limits how much can be sent

`transferable` **is not** `points - usedPoints`, nor is it "what was received by transfer", which is
what this section used to say. The real model, measured:

- Each wallet has a **sending allowance** (`newBonusPoints` in the response) that **is created by
  RECEIVED transfers** and **goes down by exactly what is sent**.
- What can be sent is capped by that allowance, but **the points themselves can come from pulls**:
  with enough allowance, points earned by playing can be sent.

The numbers, from three real sends from `8QDBKx8…`:

| | sent | allowance after |
|---|---|---|
| ids 6127 + 6129 | 1,000 + 44,534 | 347,773 → 302,239 |
| id 6239 | 74,153 | 302,239 → 228,086 |

The last one proves you don't need to have received those points: between the second and third
send that wallet **received no transfer at all**, it only earned points by pulling, and it could
still send them. What ran out on the second send was the spendable balance, not the allowance.

The reverse was confirmed too: `2cdajp4Y…` (29,175 points) and `EweRxQsf…` (160,841), which **have
never received a transfer**, have an allowance of 0 and return `transferable: 0` with the explicit
error `you can send up to 0`.

**The consequence doesn't change: the ~3 million points stranded in escrows can't be rescued.**
Escrows have never received a transfer, so their allowance is zero. The question raised in the
`altPlayerAddress` section stays closed in the negative, but because of the allowance, not the
origin of the points.

And for any "send points" feature: what's sendable must be read from `prepare`, never computed with
the `freeSpins` formula.

#### After a send, `points - usedPoints` can go NEGATIVE

Measured: `transferable` (74,153) was higher than `points - usedPoints` (72,299), and after the send
the difference ended up at **-1,854**. So the `freeSpins` formula is not the accounting CC uses for
transfers, and it can drop below zero.

It doesn't break anything of ours because the three places that compute it clamp to zero on their
own (`free_spins()` in `app/services/gacha.py`, and `tiradas_gratis()` / `tiradasGratis()`), but
whoever writes a fourth one needs to know.

### `getPoints`

Careful with the two fields, **they are not the same**:

- `totalPoints` → points earned over the wallet's **entire lifetime**. Not what can be spent.
- `pointsRemaining` → what's spendable.

Mixing them up inflates the number: on devnet the difference was 3,269,133 versus 3,069,133.

---

## The memo, and why it is the only proof

Every pull carries a `memo` (`cc-<uuid>`) that **travels inside the purchase transaction** as an
`spl-memo` instruction, visible in the logs of any explorer. And that transaction **is signed by the
player**.

In other words: on-chain, permanently and without depending on us, the pull's identifier and the
signature of whoever paid for it sit together. That is what proves the pull is theirs, even though
CC attributes it to the escrow.

Since the memo is a UUID, going from a pull to its transaction is **deterministic**, not
archaeology: just search the player's signature history for the one that contains it. That's what
`backend/scripts/backfill_pull_signatures.py` does for pulls made before the
`battle_pulls.tx_signature` column existed.

The only limit: `getSignaturesForAddress` pages backwards 1,000 at a time, so a wallet with a lot of
activity may have the pull further back than we traverse. The proof is still on-chain; we just
haven't located it.

---

## Loose ends that cost time if you don't know them

- **A card's value: `insuredValue` only.** No other field.
- **Mainnet delivers Metaplex CORE NFTs**, not SPL or cNFT, with CC's `PermanentFreezeDelegate`.
  They have no token account, so **there is no ATA rent to recover** (the 2,039,280 lamports only
  apply to SPL).
- **On devnet, USDC is a custom mint** (`Gh9Zw…`), not Circle's.
- `openPack` can return `WAITING_FOR_WEBHOOK`: it's not an error, it means "not yet, retry".
