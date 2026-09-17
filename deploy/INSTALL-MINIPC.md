# Mini PC installation: Collector Arena mainnet

A document meant to be followed **with Claude Code on the mini PC itself**. It installs the mainnet
stack behind a Cloudflare Tunnel, without opening any ports.

```bash
# On the mini PC, with a fresh Debian 12 or Ubuntu 24.04:
sudo apt update && sudo apt install -y git
git clone <REPO-URL> ~/battlearena-deploy
cd ~/battlearena-deploy
claude
```

And tell it: **"Follow `deploy/INSTALL-MINIPC.md` to install this. Go phase by phase and stop at
every check."**

---

## For the agent: rules that must not be broken

1. **Never generate the oracle key on this machine.** `oracle/app/keys.py` auto-generates it if it
   doesn't exist; a new key invalidates the `VITE_ORACLE_PUBKEY` the frontend was built with, and the
   game rejects **every** attestation. The key is copied from the user's machine.
2. **A single uvicorn process per service, never `--workers`.** The backend keeps in-memory state
   (rate limits, the `asyncio.Lock` that serializes buy-ins, the chat's set of WebSockets, background
   tasks). Two processes = a double settlement of real USDC.
3. **`DEV_ENDPOINTS_ENABLED` stays `false`.** `/pack-battles/{id}/join-bot` moves USDC without
   authentication.
4. **Don't make up `.env` values.** If a secret is missing, **stop and ask the user for it**. A
   made-up value here turns into money sent to the wrong place, not a red test.
5. **Don't restart the backend with battles in progress.** Check first with
   `sqlite3 backend/battlearena.mainnet.db "select id,status from pack_battles where status='running'"`.
6. **Don't commit anything you create here.** The `.env` files, the oracle key and the tunnel
   credentials are in `.gitignore` for a reason.

## What to have at hand before starting

Ask the user for all of it at once at the start, not one at a time:

- [ ] The repository URL **in SSH format** (`git@github.com:user/repo.git`) and access to add a
      *deploy key* to it (Settings → Deploy keys)
- [ ] The domain to use (e.g. `battlearena.tld`) and a **Cloudflare account** with that domain added
- [ ] The `oracle_key.json` file from the current machine
- [ ] The contents of `backend/.env` (Privy secrets, server Helius RPC, operator wallet, fee wallet)
- [ ] The contents of the frontend's `.env` and `.env.mainnet`
- [ ] A Backblaze B2 (or similar) account for backups
- [ ] Confirmation that the **operator wallet is funded with mainnet SOL**
- [ ] A decision on whether the referral revenue share will be active (it needs
      `REFERRAL_PAYOUT_WALLET_ID` and `REFERRAL_PAYOUT_ADDRESS`; empty → the claim responds 503)

---

## Phase 0: Repository access

**Only if the repo is private, which is the usual case.** It goes before everything else because
without it phase 1 aborts halfway through.

The clone and every later deploy are done by the `battlearena` system user, not yours: `deploy.sh`
runs `sudo -u battlearena -H git pull`. That user doesn't inherit your credentials, so it needs its
own. Having the repo cloned in your home does NOT help.

```bash
# The user (same as the bootstrap does; idempotent)
sudo adduser --system --group --home /srv/battlearena battlearena

# Its own key, in ITS home
sudo install -d -o battlearena -g battlearena -m 700 /srv/battlearena/.ssh
sudo -u battlearena -H ssh-keygen -t ed25519 -N '' \
     -f /srv/battlearena/.ssh/id_ed25519 -C 'battlearena-minipc-deploy'
sudo -u battlearena -H bash -c 'ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts'

sudo cat /srv/battlearena/.ssh/id_ed25519.pub
```

That public key is added to the repo as a **deploy key with "Allow write access" UNCHECKED**:
read-only, this repo only, and revocable without touching the account.

**Check: don't continue until this is green:**

```bash
sudo -u battlearena -H ssh -T git@github.com    # "Hi ...! You've successfully authenticated"
```

> The `-H` in `sudo` isn't optional: without it `HOME` stays `/root` and `ssh` would look for the key
> in `/root/.ssh`. Both scripts include it, which is why the key lives in `/srv/battlearena/.ssh`.

---

## Phase 1: System base

```bash
sudo REPO_URL=git@github.com:user/repo.git ~/battlearena-deploy/deploy/bootstrap-minipc.sh
```

It clones `master`, not the remote's default branch (`BRANCH=other` if ever needed). This isn't a
detail: if the repo's default is another branch, it may not even include `deploy/`, and `deploy.sh`
would keep running `pull --ff-only` on that branch forever. Check both things:

```bash
sudo -u battlearena -H git -C /srv/battlearena branch --show-current   # "master"
sudo -u battlearena -H git -C /srv/battlearena pull --ff-only          # "Already up to date."
```

It installs packages, Node 20, Caddy and `cloudflared`, creates the `battlearena` user, clones the
repo into `/srv/battlearena`, sets up both Python venvs, adds swap if needed, puts the tunnel
Caddyfile in place and installs the systemd units **without starting them**. It's idempotent.

**Check:** it finishes by printing a "what's left to do" block, and `caddy validate` passes with no
errors.

## Phase 2: Oracle key

From the **user's machine**, not from here:

```bash
scp oracle/oracle_key.json user@mini-pc:/tmp/oracle_key.json
```

And on the mini PC:

```bash
sudo mv /tmp/oracle_key.json /var/lib/battlearena/oracle_key.json
sudo chown battlearena:battlearena /var/lib/battlearena/oracle_key.json
sudo chmod 600 /var/lib/battlearena/oracle_key.json
```

It goes in `/var/lib` and not `/etc` on purpose: `keys.py` runs `chmod` on every load and
`ProtectSystem=strict` mounts `/etc` read-only → the service wouldn't start.

**Check:** the user must have a copy of this key **off this machine** (a password manager). Ask them
explicitly before continuing.

## Phase 3: Environment variables

Three files, all by hand, all `chmod 600` and owned by `battlearena`:

**`/srv/battlearena/backend/.env`**: a copy of the current one, reviewing:

```ini
CORS_ORIGINS=["https://battlearena.tld"]
DEV_ENDPOINTS_ENABLED=false
SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=<SERVER key>
PRIVY_APP_ID=...
PRIVY_APP_SECRET=...
PRIVY_OPERATOR_WALLET_ID=...
PRIVY_OPERATOR_ADDRESS=...
FEE_WALLET_ADDRESS=...
```

**`/srv/battlearena/.env`**: the frontend's. `VITE_ORACLE_PUBKEY` must be the one for the key from
phase 2.

**`/srv/battlearena/.env.mainnet`**: what changes compared to the development one:

```ini
VITE_BACKEND_URL=https://battlearena.tld
VITE_ORACLE_URL=https://battlearena.tld
```

> Everything starting with `VITE_` ends up in the browser bundle. The frontend's Helius key is
> effectively public: have the user restrict it by domain, and use a different one (without `VITE_`)
> in the backend.

```bash
sudo chown battlearena:battlearena /srv/battlearena/{.env,.env.mainnet,backend/.env}
sudo chmod 600 /srv/battlearena/{.env,.env.mainnet,backend/.env}
```

## Phase 4: Cloudflare Tunnel

`cloudflared tunnel login` opens a URL. If the machine has no desktop, **copy it to the user so they
open it in their browser** and authorize the domain.

```bash
sudo cloudflared tunnel login
sudo cloudflared tunnel create battlearena          # prints the UUID
sudo cloudflared tunnel route dns battlearena battlearena.tld   # creates the CNAME by itself

sudo cp /srv/battlearena/deploy/cloudflared/config.yml.example /etc/cloudflared/config.yml
# edit config.yml: replace TUNNEL_ID_AQUI (twice) and the hostname
sudo mv /root/.cloudflared/<UUID>.json /etc/cloudflared/
sudo chmod 600 /etc/cloudflared/<UUID>.json

sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

**Check:** `systemctl status cloudflared` is green and `cloudflared tunnel info battlearena` shows an
active connection.

In the Cloudflare dashboard, keep the record **in proxy mode (orange cloud)**: with a tunnel it's
mandatory, not optional.

## Phase 5: Start and build

```bash
sudo systemctl start battlearena-oracle battlearena-backend
sudo DOMAIN=battlearena.tld /srv/battlearena/deploy/deploy.sh
```

`DOMAIN` is mandatory: the final healthcheck queries it over HTTPS, and without it the script aborts
instead of giving you a "Deploy OK" that hasn't checked your installation. If the backup doesn't have
an rclone remote yet, add `RCLONE_REMOTE=` (empty) so it keeps a local copy.

`deploy.sh` runs a backup, `git pull`, dependencies, a frontend build into `dist.new` with an atomic
swap, restarts the services and runs a healthcheck.

**Check:** it prints `Deploy OK` and the oracle pubkey. That pubkey must match the
`VITE_ORACLE_PUBKEY` from phase 3; otherwise the frontend will reject every attestation.

## Phase 6: Full verification

```bash
DOMAIN=battlearena.tld sudo /srv/battlearena/deploy/verify.sh
```

It checks secret permissions, `DEV_ENDPOINTS_ENABLED`, CORS, the operator wallet, that there's only
one backend process, local health of both services, that the oracle pubkey matches the built one,
the DB, the backup cron, the rclone remote and, through the tunnel, that `/health`, `/pubkey`, the SPA
and the `Accept` matcher on `/leaderboard` work.

**Don't continue until everything is green.**

## Phase 6.5: Configuration the mainnet database does NOT come with

The mainnet database starts empty, and there are values that are set on devnet and not here. Go
over them with the user before opening up:

| What | Why it matters |
|---|---|
| `FEE_WALLET_ADDRESS` in `backend/.env` | Its default is hardcoded in `config.py` and is **the same on devnet and mainnet**. Without setting it here, real revenue lands in the wallet used for testing. |
| `BATTLE_FEE_PCT_PER_PLAYER` | This is the REAL fee switch (0 = don't charge). Emptying `FEE_WALLET_ADDRESS` does NOT turn it off: it sends the fee to the operator. |
| Operator SOL | Pays gas and rent for ALL operations. Without balance, games are voided when the lobby fills up. |
| `backend/scripts/machines.py hide` | No machine is disabled in this database. |
| `backend/scripts/flags.py on auto_royale` | The house lobby starts disabled. The value is `machine[:seats]`, from 5 to 10, and **the seat count changes the entry price**: `pokemon_25` is 70 USDC with 5 and 135 with 10. |

Both are run from `/srv/battlearena/backend` like this:

```bash
sudo -u battlearena -H bash -c 'cd /srv/battlearena/backend && \
  APP_NETWORK=mainnet PYTHONPATH=. ./.venv/bin/python3 scripts/flags.py list'
```

## Phase 7: Backups and alerts

```bash
sudo rclone config                     # create the B2 remote
sudo crontab -e
#   0 * * * * RCLONE_REMOTE=b2:battlearena-backups /srv/battlearena/deploy/backup.sh >> /var/log/battlearena-backup.log 2>&1
```

Run it once by hand and confirm with the user that the `.db.gz` shows up in the bucket.

Add external monitoring (healthchecks.io or UptimeRobot) against `https://battlearena.tld/health`. On
a machine at home this isn't optional: it's the only way to find out about an outage when you're not
in front of it.

## Phase 8: Physical resilience

- **BIOS: "Restore on AC power loss" = On.** Without it, after a power cut the service doesn't come
  back until someone presses the button. Remind the user: it requires rebooting into the BIOS.
- **A small UPS.** Even if it only gives 10 minutes, it avoids a dirty shutdown while SQLite is
  writing.
- If the user wants disk encryption, warn them about the conflict: with LUKS the machine sits waiting
  for the password after a power cut and doesn't come back on its own. It's their decision, not an
  oversight.

## Phase 9: Manual test before opening up

In this order, and with the user present:

1. Log in with Privy **from a phone**, not just the laptop.
2. Chat open: it must connect over `wss://` with no mixed-content errors in the console.
3. Open a card from the pool → it requests `/attest` from the oracle. If it fails, the pubkey doesn't
   match.
4. A small gacha pull with real money: check that the NFT reaches the wallet.
5. Only then, open up battles.

The Privy dashboard must have `https://battlearena.tld` in its allowed domains and *identity tokens*
enabled (User management → Authentication → Advanced), which is what the chat uses.

---

## Typical failures and what they mean

| Symptom | Cause |
|---|---|
| The frontend rejects every attestation | `VITE_ORACLE_PUBKEY` doesn't match `/pubkey`. Rebuild after fixing it (a restart isn't enough) |
| `/leaderboard` returns JSON on reload | The Caddyfile's `not header Accept *text/html*` matcher is wrong |
| The oracle returns 429 as soon as people show up | `--proxy-headers` is missing: every request looks like it comes from the same IP |
| The oracle service won't start | The key is in `/etc`; with `ProtectSystem=strict`, `keys.py`'s `chmod` fails |
| The SPA responds 403 but `/health` doesn't | Caddy can't traverse `/srv/battlearena` (750 from `adduser --system`). Fixed by `setfacl -m u:caddy:x /srv/battlearena`, which the bootstrap does |
| Caddy serves its welcome page | The Caddyfile was copied but the service wasn't reloaded: `systemctl reload-or-restart caddy` |
| `deploy.sh` dies at the first step with `didn't find section in config file` | `backup.sh` points to an rclone remote that doesn't exist. Configure it or pass an empty `RCLONE_REMOTE=` |
| Battles are voided when the lobby fills up | Operator wallet has no SOL |
| The chat drops after a minute and a half | Check `connectTimeout` in `/etc/cloudflared/config.yml` |
| `npm run build` dies with no message | Out of RAM. `bootstrap-minipc.sh` only adds swap if it detects less than 4 GB |

## Day to day

```bash
sudo DOMAIN=battlearena.tld /srv/battlearena/deploy/deploy.sh   # deploy the latest version
journalctl -u battlearena-backend -f      # backend logs
journalctl -u cloudflared -f              # tunnel logs
sudo DOMAIN=battlearena.tld /srv/battlearena/deploy/verify.sh   # after any big change
```

On startup, the backend runs `_resume_orphaned_battles`: it finishes, or voids and refunds, the
battles left `running`. A restart doesn't break the accounting, but **check first whether there are
battles in progress**.
