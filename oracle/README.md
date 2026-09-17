# Collector Arena: pricing oracle

An off-chain **Phase 1** service that values cards for combat. It resolves a Collector Crypt NFT
`mint` → gets its **insured value** and **grade** → signs an **ed25519 attestation** in the EXACT
canonical format verified by the Anchor program. The client embeds that signature as an Ed25519
instruction in the `initialize_battle`/`join_battle` transaction, and the contract verifies it through
instruction introspection.

**Status:** MVP. Value source = `insuredValue` only (mock for dev/tests, the real Collector Crypt API
in production). No real money.

## Getting started

```bash
cd oracle
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q                       # 24 tests, fully offline (HTTP mocked)

# server (mock by default):
uvicorn app.main:app --port 8787
# real source:
PRICING_SOURCE=collectorcrypt uvicorn app.main:app --port 8787
```

## Endpoints

- `GET /health` → `{ "status": "ok" }`
- `GET /pubkey` → `{ "oracle_pubkey": "<base58>" }`: the key to register on-chain (the `oracle` field
  of `Battle`).
- `GET /attest?mint=<pubkey>&battle=<pubkey>` →
  ```json
  {
    "mint": "<base58>", "value_usd": 1200, "grade": 9, "grading_company": "PSA",
    "ts": 1700000000, "message_hex": "…", "signature_hex": "…", "oracle_pubkey": "<base58>"
  }
  ```
  - **`battle` is required** (base58, must decode to 32 bytes). The signature binds the attestation to
    a specific battle PDA, preventing reuse in another battle (anti-replay).
  - `409` if the card can't be valued (no `insuredValue` / no grade / mint not found). `422` if `mint`
    or `battle` aren't valid pubkeys (don't decode to 32 bytes).
  - The client builds the transaction's Ed25519 instruction from `message_hex` + `signature_hex` +
    `oracle_pubkey`, with self-referencing `0xFFFF` indexes (as in the contract's litesvm tests), and
    places it BEFORE the program instruction, passing `ed25519_ix_index`.

## Value decision: `insuredValue` only (manipulation resistant)

A card's "power" is defined **solely by its insured value** (Collector Crypt's `insuredValue`), set by
a third party. `listing.price` is **not** used (the player sets it themselves → manipulable), nor the
PSA estimate. A card without `insuredValue` **cannot play** (it's rejected). That's the price of
making sure nobody can assign themselves power in a game with stakes. There is an explicit test
(`test_extract_no_fallback_to_listing_price`) proving that, even when a listing price exists, a card
without `insuredValue` is rejected.

> `insuredValue` is the **v1** source, swappable later for the SPEC's cross-platform pricing engine
> (the "moat") without touching the rest of the service.

## Configuration (env / `.env`)

| Var | Default | What |
|---|---|---|
| `PRICING_SOURCE` | `mock` | `mock` (deterministic, dev/tests) or `collectorcrypt` (real API) |
| `ORACLE_KEY_PATH` | `oracle_key.json` | ed25519 seed (32 bytes hex). Generated if missing (**dev only**). In production, outside the repo. |
| `CC_BASE_URL` | `https://api.collectorcrypt.com` | Collector Crypt API (public, no auth) |
| `PRICING_CACHE_TTL` | `120` | Per-mint cache TTL in seconds (respects CC's WAF) |

The oracle key is **never committed** (`.gitignore` covers `oracle_key.json`, `.env`, `.venv`).

## Data source (Collector Crypt)

`GET {CC_BASE_URL}/marketplace?search={mint}` (public). The item with an exact `nftAddress == mint`
is selected (the search matches substrings) and `insuredValue` + `gradeNum` + `gradingCompany` are
extracted. The field mapping was taken from the real integration in MarketAgg.

## Guarantee against drifting out of sync with the contract

The canonical message is `mint(32) || value_usd(8 LE u64) || grade(1) || ts(8 LE i64) || battle(32)`
= **81 bytes**, identical to the contract's `attestation_msg`. The `battle` field binds the signature
to the specific battle PDA (anti-replay). A **shared equivalence vector**
(`tests/fixtures/attestation_vectors.json`) is verified **at the same time** by the Python test
(`test_shared_vector_matches`) and a Rust test in the contract (`shared_attestation_vector_matches`).
If anyone changes the format on one side, both tests break.

## Architecture

```
oracle/app/
  main.py             # FastAPI: /health, /pubkey, /attest (create_app factory + build_default_app)
  attestation.py      # build_message (canonical) + sign_attestation (ed25519)
  keys.py             # ed25519 keypair (load/generate/persist)
  config.py           # env-based settings
  pricing/
    base.py           # CardValue, PricingSource, parse_insured_value/parse_grade, ValueUnavailable
    mock.py           # MockPricingSource (deterministic)
    collector_crypt.py# CollectorCryptSource (real API, insuredValue only, TTL cache)
```

## Risks / open items (pre-production)

- **Availability = liveness**: if the oracle goes down, battles can't be created. Production will want
  redundancy and key rotation.
- **Per-battle binding**: RESOLVED. The `/attest` endpoint requires the `battle` parameter (the battle
  PDA) and includes it in the signed message, preventing an attestation from being reused in another
  battle.
- **CC schema**: the field mapping comes from MarketAgg; it should be confirmed against a real
  response (the parser tolerates wrapper variants).
