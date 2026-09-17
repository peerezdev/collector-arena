# Collector Arena: starting the services

How to run each Collector Arena service locally (devnet). Three services that run, plus an on-chain
program that is only compiled and deployed.

## Service map

| Service  | Folder     | Port | Start                                      | Environment      |
|-----------|------------|--------|--------------------------------------------|------------------|
| Oracle    | `oracle/`  | 8787   | `uvicorn app.main:app --port 8787`         | `oracle/.env`    |
| Backend   | `backend/` | 9090   | `uvicorn app.main:app --port 9090`         | `backend/.env`   |
| Frontend  | repo root  | 5173   | `npm run dev`                              | `.env` (root)    |
| On-chain program | `onchain/` | n/a | `cargo build-sbf` / `anchor deploy` (devnet) | `onchain/Anchor.toml` |

**Recommended start order:** Oracle → Backend → Frontend. The on-chain program is not a running
service: it's compiled and deployed once, and the frontend and backend point to it by its Program ID.

## Requirements

- **Node** ≥ 20 (tested with v24) and **npm**.
- **Python 3.9+** (the repo's venvs use 3.9.6).
- **Solana/Anchor toolchain** (only to compile or deploy the program): `solana`, `anchor`,
  `cargo build-sbf` (tested with solana-cargo-build-sbf 3.1.10).
- The venvs are expected at `backend/.venv` and `oracle/.venv`. If they don't exist, create them (see
  below).

## Initial setup (once)

### 1. Environment variables

Each service reads its own `.env` (gitignored). Copy the example and fill it in:

```bash
cp .env.example .env                 # frontend (root)
cp backend/.env.example backend/.env
cp oracle/.env.example oracle/.env
```

- **`.env` (root, frontend):** `VITE_PRIVY_APP_ID`, `VITE_SOLANA_RPC`, `VITE_PROGRAM_ID`,
  `VITE_ORACLE_URL=http://localhost:8787`, `VITE_BACKEND_URL=http://localhost:9090`,
  `VITE_STAKE_MINT`, `VITE_TREASURY`, `VITE_ORACLE_PUBKEY`, `VITE_CC_COLLECTION_MINT` (optional),
  `VITE_DAS_RPC` (optional, a DAS-enabled RPC for the inventory).
- **`backend/.env`:** `DATABASE_URL` (local SQLite by default), `CHAIN_SOURCE`, `SOLANA_RPC_URL`,
  `PROGRAM_ID`, `ELO_START`, `ELO_K`, `SESSION_TTL`, `CORS_ORIGINS` (e.g.
  `["http://localhost:5173"]`), `GACHA_BASE_URL` (empty ⇒ gacha disabled), `GACHA_API_KEY` (optional;
  only if the environment requires it, e.g. mainnet with a key), `PRIVY_APP_ID`, `PRIVY_JWKS_URL`.
- **`oracle/.env`:** `PRICING_SOURCE` (`mock` by default, or `collectorcrypt`), `ORACLE_KEY_PATH`
  (defaults to `oracle_key.json`), `CC_BASE_URL`, `PRICING_CACHE_TTL`, `RATE_LIMIT_PER_MIN`,
  `CORS_ORIGINS`.

> The frontend's `VITE_ORACLE_PUBKEY` **must** match the oracle's public key (the frontend rejects
> attestations from unknown oracles). See `oracle/README.md` to get the oracle's pubkey and copy it
> into `.env`.

### 2. Dependencies

```bash
# Frontend (root)
npm install

# Backend
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt

# Oracle
python3 -m venv oracle/.venv && oracle/.venv/bin/pip install -r oracle/requirements.txt
```

(If the venvs already exist, skip this step.)

## Starting each service

### Oracle (port 8787)

Signs ed25519 attestations of the cards' `insured_value`. In `mock` mode it's deterministic (no API
key needed). The ed25519 key is auto-generated in `oracle_key.json` if it doesn't exist (**dev
only**; it's never committed).

```bash
cd oracle
.venv/bin/uvicorn app.main:app --port 8787
# Real pricing source (when a CC key is available):
# PRICING_SOURCE=collectorcrypt .venv/bin/uvicorn app.main:app --port 8787
```

Healthcheck: `curl -s http://localhost:8787/health` (or see `oracle/README.md` for the pubkey
endpoint).

### Backend (port 9090)

FastAPI + SQLAlchemy (local SQLite). It initializes the database on startup. It serves the
lobby and ELO, the gacha proxy and the lobby chat over WebSocket. Auth is via the Privy identity token.

```bash
cd backend
.venv/bin/uvicorn app.main:app --port 9090
```

> **Important:** start it **from `backend/`**, because `config.py` reads `.env` with a relative path.

Healthcheck: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9090/health` → `200`.

Notes:
- **Gacha:** enabled without an API key on devnet (keyless). The `/gacha/*` endpoints work with only
  `GACHA_BASE_URL` configured. Set `GACHA_API_KEY` only if a future environment requires it (e.g.
  mainnet). To disable the gacha entirely, leave `GACHA_BASE_URL` empty (the endpoints will respond
  `503`).
- **Privy:** without `PRIVY_APP_ID`, authenticated endpoints respond `503`. With it, `401` if the
  token is missing (as expected).

### Frontend (port 5173)

Vite + React.

```bash
npm run dev          # http://localhost:5173
# others:
npm run build        # production build (tsc -b && vite build)
npm test             # tests (vitest)
```

> Test it on **`http://localhost:5173`**, not through an https tunnel: the chat connects to
> `ws://localhost:9090` and the browser would block mixed content from https.

### On-chain program (`onchain/`, not a service)

Anchor. Current Program ID: `89qGDjXGcV9zi3968DtRLNzBn5KXhYmSGJkjKntksCdk`.

```bash
cd onchain
cargo build-sbf            # builds the .so
cargo test                 # in-process LiteSVM tests (no validator needed)
# Deploy to devnet (requires a wallet with devnet SOL):
# solana config set --url devnet && anchor deploy
```

Deployment and IDL details in `docs/ONCHAIN.md`.

## Quick start (everything at once)

Three terminals (foreground, so logs are easy to watch and Ctrl-C stops them):

```bash
# Terminal 1: Oracle
cd oracle && .venv/bin/uvicorn app.main:app --port 8787

# Terminal 2: Backend
cd backend && .venv/bin/uvicorn app.main:app --port 9090

# Terminal 3: Frontend
npm run dev
```

Quick check:
```bash
curl -s http://localhost:8787/health        # oracle
curl -s http://localhost:9090/health        # backend → {"status":"ok"}
# open http://localhost:5173 in the browser
```

## Troubleshooting

- **Port in use:** `lsof -ti tcp:9090 | xargs kill` (change the port for each service).
- **The backend doesn't read `.env`:** make sure you start it from `backend/` (relative path).
- **The chat won't let you type:** enable *identity tokens* in the Privy dashboard (User management →
  Authentication → Advanced → "Return user data in an identity token") and test on
  `http://localhost:5173` (not through an https tunnel).
- **The embedded wallet doesn't appear / the balance shown is Phantom's:** log out and back in so
  Privy provisions the embedded wallet (config `createOnLogin: 'all-users'`).
- **Empty inventory (profile):** on devnet it needs `VITE_CC_COLLECTION_MINT` (CC's collection mint
  on devnet) and a `VITE_DAS_RPC` with DAS support (e.g. Helius); the public devnet RPC has no DAS and
  the inventory comes back empty (in a controlled way).
- **Gacha responds 503:** only happens if `GACHA_BASE_URL` is empty (the kill switch). On devnet
  `GACHA_API_KEY` isn't required; the gacha works without it.

## Security (reminder)

- `oracle_key.json`, the `.env` files and the `.venv` folders are in `.gitignore` and are **never**
  committed.
- `PRIVY_APP_SECRET` lives only in `backend/.env` (server-side), never in the frontend.
- In production: `https`/`wss` (not `http`), the oracle key outside the repo, and review the pending
  hardening before mainnet.
