# Deployment: Collector Arena mainnet

**Two variants, same stack.** Pick one:

| | Where | TLS | Caddyfile | Guide |
|---|---|---|---|---|
| **Mini PC at home** | your machine, no public IP | Cloudflare Tunnel | `Caddyfile.tunnel` | [INSTALL-MINIPC.md](INSTALL-MINIPC.md) ← start here |
| **VPS** | cloud provider | Caddy + Let's Encrypt | `Caddyfile` | this document |

Files: `bootstrap-minipc.sh` (prepares the machine, idempotent) · `deploy.sh` (deploy) ·
`verify.sh` (full check before opening up) · `backup.sh` (DB copy to offsite storage) ·
`systemd/` (the two services) · `cloudflared/` (tunnel config).

A single server with root, Debian 12 or Ubuntu 24.04, **in US East**. Any KVM VPS works; the guide
doesn't depend on the provider.

**Real requirements:** 2 vCPU and **2 GB of RAM are enough at runtime** (two uvicorn processes +
Caddy + SQLite come to about 500 MB). The 4 GB usually recommended is only for the frontend's
`npm run build` (three.js + Vite). With 2 GB, **add 2 GB of swap** before the first deploy and the
build still passes, just more slowly:

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

**Why US East and not Europe:** the backend talks a lot to services based in the US (Collector
Crypt's gacha API, Helius, the Solana RPC) and confirms transactions with up to 20 retries of 1.5 s.
That latency weighs more on the experience than the player's latency to the server.

> Hetzner raised prices for its US locations on 2026-06-15 (CPX21 Ashburn: $37.49/month). It's no
> longer the cheap option in America; it still is in Germany/Finland if you accept the latency to the
> APIs.

```
Caddy :443 ──┬─ backend routes (Accept ≠ text/html) ──→ 127.0.0.1:9190  backend
             ├─ /ws  (WebSocket)                     ──→ 127.0.0.1:9190
             ├─ /attest /pubkey                      ──→ 127.0.0.1:8787  oracle
             └─ everything else                      ──→ /srv/battlearena/dist  (SPA)
```

## Rules that must not be broken

1. **A single uvicorn process, no `--workers`.** The backend keeps in-memory state per process:
   rate limits, the `asyncio.Lock` that serializes buy-ins, the set of WebSockets and background
   tasks. Two workers = a double settle of real USDC.
2. **The DB is a file and there are no migrations.** Persistent disk and offsite backups.
3. **`DEV_ENDPOINTS_ENABLED` must stay `false`.** `/pack-battles/{id}/join-bot` moves USDC without
   authentication.
4. **The oracle key is never regenerated.** If it changes, the frontend rejects every attestation (it
   compares against `VITE_ORACLE_PUBKEY`, fixed at build time).

---

# Step 0: Server and DNS

First of all, because Caddy needs the domain to resolve before it can obtain the certificate.

```bash
# 1. Create the CPX21 in Ashburn with Debian 12 and your SSH key. Log in as root.
# 2. Point the DNS (Cloudflare, grey / DNS-only for now):
#      A     battlearena.tld    -> <server IP>
#      AAAA  battlearena.tld    -> <server IPv6>
# 3. Check from your machine that it resolves BEFORE continuing:
dig +short battlearena.tld
```

Base system:

```bash
apt update && apt upgrade -y
apt install -y git curl ufw sqlite3 python3-venv rclone
curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt install -y nodejs

ufw allow 22,80,443/tcp && ufw --force enable
adduser --system --group --home /srv/battlearena battlearena
```

Ports 9190 and 8787 are **not** opened: they only listen on loopback.

Code and virtual environments:

```bash
git clone <your-repo> /srv/battlearena
chown -R battlearena:battlearena /srv/battlearena

sudo -u battlearena python3 -m venv /srv/battlearena/backend/.venv
sudo -u battlearena /srv/battlearena/backend/.venv/bin/pip install -r /srv/battlearena/backend/requirements.txt

sudo -u battlearena python3 -m venv /srv/battlearena/oracle/.venv
sudo -u battlearena /srv/battlearena/oracle/.venv/bin/pip install -r /srv/battlearena/oracle/requirements.txt
```

**Check:** `ls /srv/battlearena/{backend,oracle}/.venv/bin/uvicorn` returns both paths.

---

# Step 1: Oracle (`:8787`)

Signs ed25519 attestations of each card's value. It goes first because the frontend is built with
its pubkey inside.

### 1.1 Upload the key (don't generate it on the server)

`keys.py` auto-generates the key if it doesn't exist, and a new key invalidates the
`VITE_ORACLE_PUBKEY` of every build. Upload the one you already use.

```bash
# on the server
mkdir -p /var/lib/battlearena
# from your machine
scp oracle/oracle_key.json root@battlearena.tld:/var/lib/battlearena/oracle_key.json
# back on the server
chown -R battlearena:battlearena /var/lib/battlearena
chmod 600 /var/lib/battlearena/oracle_key.json
```

Also keep a copy **off the server** (a password manager). If you lose it, there is no way to recover
it.

> It goes in `/var/lib` and not `/etc` on purpose: `keys.py` runs `chmod 600` on every load and
> `ProtectSystem=strict` mounts `/etc` read-only → the service wouldn't start.

### 1.2 Start the service

```bash
cp /srv/battlearena/deploy/systemd/battlearena-oracle.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now battlearena-oracle
```

### 1.3 Check

```bash
curl -s 127.0.0.1:8787/health     # {"status":"ok"}
curl -s 127.0.0.1:8787/pubkey     # {"oracle_pubkey":"..."}
```

**Write down that pubkey**: it has to match `VITE_ORACLE_PUBKEY` in step 3. If it doesn't start:
`journalctl -u battlearena-oracle -n 50`.

---

# Step 2: Backend (`:9190`)

### 2.1 Environment variables

`/srv/battlearena/backend/.env` (by hand, gitignored). Copy the one from your machine and review it:

```ini
CORS_ORIGINS=["https://battlearena.tld"]
DEV_ENDPOINTS_ENABLED=false
SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=<server key>
PRIVY_APP_ID=...
PRIVY_APP_SECRET=...          # only here, never in the frontend
PRIVY_OPERATOR_WALLET_ID=...
PRIVY_OPERATOR_ADDRESS=...
FEE_WALLET_ADDRESS=...
```

```bash
chown battlearena:battlearena /srv/battlearena/backend/.env
chmod 600 /srv/battlearena/backend/.env
```

`backend/.env.mainnet` **isn't in the repo**: `.gitignore` excludes `.env.*`, so it has to be created
on the server. It only holds network overrides (RPC, CC gacha, USDC mint), no secrets; those stay in
`.env`. `APP_NETWORK=mainnet` loads it on top of `.env`.

> If it's missing, the backend starts with the **devnet** configuration without warning: same
> database, same gacha. Check it before the first deploy.

> Use a **different** Helius key from the frontend's: this one is truly server-side (no `VITE_`
> prefix, it never reaches the browser), so it can have a higher quota.

### 2.2 Start the service

```bash
cp /srv/battlearena/deploy/systemd/battlearena-backend.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now battlearena-backend
```

The `battlearena.mainnet.db` database is created automatically on startup (`init_db`).

### 2.3 Check

```bash
curl -s 127.0.0.1:9190/health                  # {"status":"ok"}
ls -l /srv/battlearena/backend/battlearena.mainnet.db
journalctl -u battlearena-backend -n 30        # no tracebacks
```

### 2.4 Before accepting real money

- **Fund the operator wallet** (`PRIVY_OPERATOR_ADDRESS`) with mainnet SOL: it pays gas and the rent
  of the escrow accounts. Without funds, Pack Battles and Royales are voided when the lobby fills up.
- **In the Privy dashboard**, add `https://battlearena.tld` to the allowed domains and keep
  *identity tokens* enabled (User management → Authentication → Advanced), which is what the chat
  uses.

---

# Step 3: Frontend (static build)

It isn't a process: it's built and Caddy serves the output.

### 3.1 Environment variables

Vite loads `.env` and then `.env.mainnet` on top (via `--mode mainnet`). Both go in the root
`/srv/battlearena/`, by hand, gitignored.

In `.env.mainnet`, what changes compared to your machine:

```ini
VITE_BACKEND_URL=https://battlearena.tld     # NOT localhost: same origin
VITE_ORACLE_URL=https://battlearena.tld      # Caddy proxies /attest and /pubkey
```

In `.env`, `VITE_ORACLE_PUBKEY` **must be the pubkey printed in step 1.3**.

> ⚠️ Everything starting with `VITE_` ends up in the browser bundle. The frontend's Helius key is
> effectively public: restrict it by domain in the Helius dashboard.

### 3.2 Build

```bash
sudo -u battlearena -H npm --prefix /srv/battlearena ci
cd /srv/battlearena && sudo -u battlearena -H npm run build -- --mode mainnet
```

### 3.3 Check

```bash
ls /srv/battlearena/dist/index.html
grep -o 'battlearena.tld' /srv/battlearena/dist/assets/*.js | head -1   # the domain got baked in
```

---

# Step 4: Caddy (ties the three together)

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  > /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy

cp /srv/battlearena/deploy/Caddyfile /etc/caddy/Caddyfile
# edit the domain and the Let's Encrypt email
caddy validate --config /etc/caddy/Caddyfile     # ALWAYS before reloading
systemctl reload caddy
```

### Check (from your machine, not the server)

```bash
curl -s https://battlearena.tld/health                    # {"status":"ok"}   → backend
curl -s https://battlearena.tld/pubkey                    # {"oracle_pubkey"} → oracle
curl -s -o /dev/null -w '%{http_code}\n' https://battlearena.tld/    # 200    → SPA
curl -s -H 'Accept: text/html' https://battlearena.tld/leaderboard | head -c 40   # HTML, not JSON
```

That last one validates the `Accept` trick: navigating to `/leaderboard` must serve the app, and a
`fetch` from the app must reach the backend.

---

# Step 5: Backups and alerts

```bash
chmod +x /srv/battlearena/deploy/{deploy,backup}.sh
rclone config          # create the remote (Backblaze B2 ≈ $1/month, 10 GB free)

crontab -e
#   0 * * * * RCLONE_REMOTE=b2:battlearena-backups /srv/battlearena/deploy/backup.sh >> /var/log/battlearena-backup.log 2>&1
```

Check: run `RCLONE_REMOTE=b2:battlearena-backups /srv/battlearena/deploy/backup.sh` by hand once and
confirm the `.db.gz` shows up in the bucket.

External monitoring against `https://battlearena.tld/health` (healthchecks.io or UptimeRobot): if the
server goes down, you need to find out before your players do.

---

# Step 6: End-to-end test before opening up

1. Log in with Privy from a phone (not just your laptop).
2. Open the chat: it has to connect over `wss://` with no mixed-content errors in the console.
3. Open a card from the pool → the modal requests `/attest` from the oracle. If you get an attestation
   error, `VITE_ORACLE_PUBKEY` doesn't match step 1.3.
4. Make a small gacha pull with real money and check the NFT reaches the wallet.
5. Only then, open up battles.

---

# Day to day

```bash
sudo /srv/battlearena/deploy/deploy.sh          # deploy master (backup → pull → build → restart → healthcheck)
journalctl -u battlearena-backend -f            # backend logs
journalctl -u battlearena-oracle -f             # oracle logs
systemctl restart battlearena-backend           # manual restart
```

On startup, the backend runs `_resume_orphaned_battles`: it finishes, or voids and refunds, the
battles left `running`, and sweeps the `voided` ones with pending reconciliation. A restart doesn't
break the accounting, but **avoid restarting with battles in progress**: check `/pack-battles` before
deploying.

# If you ever need to scale

Don't add replicas: the code assumes a single process. The right order is (1) Postgres + Alembic,
(2) rate limits and the `_buyin_lock` to Redis, (3) only then, multiple instances or a PaaS.
