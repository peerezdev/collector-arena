# Rank emblems and user tags

Date: 2026-09-22
Status: approved design, not implemented

## What it is

Two marks that sit next to a player's name, in the lobby chat and on the profile page:

- **Rank emblem.** A symbol for how much the player has wagered in Pack Battles and Battle Royale,
  over their whole history. Earned automatically, never lost.
- **Tags.** Short labels assigned by hand, starting with `TEAM` for the house's own accounts. More
  can be added later (Mod, Partner, Creator) without code changes.

## Decisions

| Topic | Decision |
|---|---|
| Emblem source | Lifetime wager in **settled** Pack Battles and Battle Royale. Gacha does not count |
| Can a rank be lost | No. It is a lifetime total |
| Ranks | Bronze 500 · Silver 2,500 · Gold 10,000 · Platinum 25,000 · Diamond 50,000 · Obsidian 100,000 (USD) |
| Below 500 | No emblem at all |
| Tags | Stored in the database, many per wallet, assigned with a console script. No admin UI |
| Where they show | Lobby chat and profile page only, for now |
| Profile extra | Rank name and a progress bar to the next rank, on **every** profile, not only your own |
| Symbols | "One object per rank" family, drawn as inline SVG (below) |

Why lifetime and not a rolling window: it matches the "Total wagered" figure the profile already
shows, it rewards loyalty, and losing an emblem feels like a punishment.

Why the wager figure must be the existing one: the profile already shows "Total wagered" computed by
`read_user_stats` (`backend/app/services/users.py`). If the emblem used any other sum, a player could
see $9,990 wagered next to a Gold emblem. The rank MUST be derived from the same per-battle function,
`_entry_base_units`, over the same set of battles (`PackBattle.status == "settled"`).

## Architecture

Chosen approach: **batched lookup from the frontend** (option 1 of three considered).

- The chat protocol is not touched. Every chat message already carries the author's `wallet`.
- The frontend asks for emblems and tags by wallet, in batches, and caches them.
- The rank is computed on read, not stored. The frontend only knows the endpoint, so if summing
  wagers ever becomes slow, a stored column can be added behind the same endpoint without touching
  the frontend.

Rejected: putting rank and tags inside each chat websocket message (couples the chat protocol to
ranks, history needs the same work, other screens need another path anyway), and storing the rank
in a `users` column updated at settle (a duplicated figure that can drift, needs a backfill, and is
not needed at the current scale).

## Backend

### `app/services/badges.py` (new)

The single place that knows the ranks.

```python
RANKS = [  # (id, name, threshold in USD), ascending
    ("bronze", "Bronze", 500),
    ("silver", "Silver", 2_500),
    ("gold", "Gold", 10_000),
    ("platinum", "Platinum", 25_000),
    ("diamond", "Diamond", 50_000),
    ("obsidian", "Obsidian", 100_000),
]
```

- `rank_for(wagered_usd) -> id | None`: the highest rank whose threshold is `<=` the wager. `None`
  under 500. Exactly 500.00 is Bronze; 499.99 is nothing.
- `progress_for(wagered_usd) -> dict`: `{"wagered_usd", "rank", "next_rank", "next_threshold_usd"}`.
  At Obsidian, `next_rank` and `next_threshold_usd` are `None`.
- `wagered_by_wallet(session, wallets) -> dict[wallet, float]`: ONE query that loads the settled
  battles joined to their players for all the given wallets, then sums `_entry_base_units` per
  wallet in Python. Wallets with no battles map to `0.0`. `_entry_base_units` moves from
  `users.py` to a place both modules import (or `badges.py` imports it), so there is one definition.
- `read_user_stats` keeps working as today. A test asserts that, for the same wallet,
  `wagered_by_wallet(...)[w]` equals `read_user_stats(...)["totalWageredUsd"]`.

### `user_tags` table (new)

| Column | Type | Notes |
|---|---|---|
| `wallet` | String | part of the primary key |
| `tag` | String | part of the primary key. Stored upper case (`TEAM`) |
| `created_at` | DateTime | |

Primary key `(wallet, tag)`: a wallet can have several tags, never the same one twice. Created by
`init_db` like the other tables. No foreign key to `users`: a house wallet can be tagged before it
ever logs in.

Allowed tag text: `^[A-Z0-9]{2,12}$` after upper-casing. Anything else is rejected by the script.

### `scripts/tags.py` (new)

Same shape as `scripts/flags.py`.

```
python -m scripts.tags add <wallet> TEAM
python -m scripts.tags remove <wallet> TEAM
python -m scripts.tags list [<wallet>]
```

`add` is idempotent (adding an existing tag is not an error). `remove` of a tag that is not there
says so and exits non-zero. It validates the wallet as a Solana address before writing.

### API

**`GET /users/badges?wallets=<w1>,<w2>,…`** (new, public, no auth)

- Registered BEFORE `GET /users/{wallet}` in `main.py`. Otherwise FastAPI matches `badges` as a
  wallet.
- At most 100 wallets per call; more is a 422. Duplicates and empty entries are ignored.
- Response: `{"<wallet>": {"rank": "gold" | null, "tags": ["TEAM"]}, …}`, one entry per requested
  wallet, including wallets with nothing (`rank: null, tags: []`).
- Two queries in total, whatever the number of wallets: one for wagers, one for tags.

**`GET /users/{wallet}`** (extended)

Adds three fields to the existing response, nothing removed:

```json
{
  "rank": "silver",
  "tags": ["TEAM"],
  "rank_progress": {"wagered_usd": 7340.0, "next_rank": "gold", "next_threshold_usd": 10000}
}
```

For a wallet with no user row, the endpoint already returns a default view without persisting
anything (`read_user_view`). The three fields are added there too, since tags can exist without a
`users` row.

Note on `_entry_base_units`: for a Royale it is the full buy-in (`royale_buyin`), not the per-box
price. Reusing it is what keeps Royale wagers right in the rank.

No websocket or chat storage changes.

## Frontend

### `src/ui/badges/` (new)

- **`ranks.ts`**: rank ids in order, display names and colours (below). Pure, tested.
- **`EmblemaRango.tsx`**: `<EmblemaRango rank="gold" size={15} />`. Renders the inline SVG for that
  rank, `role="img"`, `aria-label` = the rank name, and a `title` tooltip with the rank name. Renders
  nothing for `null`.
- **`TagUsuario.tsx`**: the tag pill. Mono, 9.5px, bold, letter-spaced, brand green on a faint
  green fill with a green border, 4px radius. Every tag uses this one style for now.
- **`NombreUsuario.tsx`**: emblem, then name, then tags, in one row with a small gap. Takes
  `wallet`, `name` and an optional `size`. It reads badges through `useBadges`. The name keeps
  whatever element the caller used (a `Link` to the profile in the chat).

### `useBadges(wallets)` (new hook)

- Same pattern as `useAliases` (`src/ui/useAliases.ts`): module-level cache, batched fetch of only
  the wallets not yet cached, re-render when they arrive.
- Cache entries expire after 5 minutes, so a new rank or tag shows up without a reload.
- On a failed request: nothing is cached for those wallets and nothing is shown. The name always
  renders.

### Chat

`Autor` in `src/ui/screens/Hub/ChatDock.tsx` renders `NombreUsuario` for messages that have a
wallet. Messages without a wallet (system messages) stay as they are. Emblem size 15px.

### Profile

In `src/ui/screens/Profile/ProfilePage.tsx`, next to the `<h1>` name:

- the emblem at 28px, then the tags;
- under the name, the rank name in the rank's colour;
- a progress bar to the next rank, with `$<wagered> wagered` on the left and
  `$<threshold> for <Next rank>` plus the next rank's emblem at 14px on the right;
- at Obsidian, no bar: the rank name alone;
- under 500, no emblem, and the bar shows progress towards Bronze.

These values come from the extended `GET /users/{wallet}`, not from `useBadges`.

## The symbols

"One object per rank": each rank is a different object, so ranks can be told apart by shape, not
only by colour, which matters at 15px and for colour-blind players. All share `viewBox="0 0 32 32"`.

| Rank | Object | Base | Light | Dark (stroke) |
|---|---|---|---|---|
| Bronze | Medal: circle with an inner ring | `#c47f45` | `#eab184` | `#6e3f1c` |
| Silver | Five-point star with a small inner star | `#b9c2cc` | `#f1f4f7` | `#5d6773` |
| Gold | Ingot: trapezoid with a lighter top band | `#f0bd3f` | `#ffe38c` | `#8a6112` |
| Platinum | Hexagonal crystal with a lighter top face | `#9fd9d3` | `#e6fbf8` | `#3f7c77` |
| Diamond | Brilliant cut: crown, table and facet lines | `#62c6ff` | `#d6f3ff` | `#1d6aa3` |
| Obsidian | Three dark shards with a violet edge | `#2b2140` | `#b99bff` | `#b99bff` |

The reference drawings, exactly as approved, are the "2 · One object per rank" artboard of the
"Emblemas de rango" design canvas. Their SVG source:

```
Bronze:   <circle cx="16" cy="16" r="12.5" fill=BASE stroke=DARK stroke-width="1.6"/>
          <circle cx="16" cy="16" r="8" fill="none" stroke=LIGHT stroke-width="1.4"/>
Silver:   <polygon points=STAR(16,16.5, r1=14, r2=6.2) fill=BASE stroke=DARK stroke-width="1.5" stroke-linejoin="round"/>
          <polygon points=STAR(16,16.5, r1=6.5, r2=2.9) fill=LIGHT/>
Gold:     <polygon points="9,9 23,9 29,23 3,23" fill=BASE stroke=DARK stroke-width="1.6" stroke-linejoin="round"/>
          <polygon points="10.5,11 21.5,11 23,15 9,15" fill=LIGHT/>
Platinum: <polygon points="16,2 28,9 28,23 16,30 4,23 4,9" fill=BASE stroke=DARK stroke-width="1.6" stroke-linejoin="round"/>
          <polygon points="16,2 28,9 16,16 4,9" fill=LIGHT/>
          <polyline points="16,16 16,30" fill="none" stroke=DARK stroke-width="1"/>
Diamond:  <polygon points="9,5 23,5 30,12 16,29 2,12" fill=BASE stroke=DARK stroke-width="1.6" stroke-linejoin="round"/>
          <polygon points="9,5 23,5 30,12 2,12" fill=LIGHT/>
          <path d="M11 12 L16 29 L21 12" fill="none" stroke=DARK stroke-width="1"/>
          <polyline points="11,12 16,5 21,12" fill="none" stroke=DARK stroke-width="1"/>
Obsidian: <polygon points="16,1 21,12 18,30 13,30 10,12" fill="#2b2140" stroke="#b99bff" stroke-width="1.5" stroke-linejoin="round"/>
          <polygon points="7,9 11,17 10,30 5,30 3,17" fill="#2b2140" stroke="#b99bff" stroke-width="1.3" stroke-linejoin="round"/>
          <polygon points="25,9 29,17 27,30 22,30 21,17" fill="#2b2140" stroke="#b99bff" stroke-width="1.3" stroke-linejoin="round"/>
          <polyline points="16,1 16,30" fill="none" stroke="#b99bff" stroke-width="0.9" opacity="0.7"/>
```

`STAR(cx, cy, r1, r2)` is a five-point star: ten points alternating radius `r1` and `r2`, the first
at the top (angle −90°), each `36°` after the previous. Precompute the point strings in `ranks.ts`;
do not compute them on every render.

The rank name's text colour on the profile is the rank's **light** colour, except Obsidian, which
uses `#b99bff` (its base is too dark to read on the app background).

## Errors

- `GET /users/badges` failing, or slow: chat and profile render exactly as today, with no emblems or
  tags. A name is never held back waiting for its emblem.
- A tag the frontend does not know renders with the same pill style. Tags are plain text.
- A rank id the frontend does not know (a future rank added in the backend first) renders nothing,
  not a broken image.

## Testing

Backend (pytest):

- `rank_for` at the edges: 0, 499.99, 500, 2,499.99, 2,500, 100,000, far above.
- `progress_for` at Obsidian has no next rank.
- `wagered_by_wallet`: only settled battles count; gacha packs do not; refunded or cancelled battles
  do not; a Royale entry counts; the value equals `read_user_stats(...)["totalWageredUsd"]`.
- `GET /users/badges`: several wallets in one call, a wallet with nothing, the 100 wallet limit,
  duplicates, and that `/users/badges` is not captured by `/users/{wallet}`.
- `GET /users/{wallet}` carries `rank`, `tags`, `rank_progress`.
- `scripts/tags.py`: add is idempotent, remove of a missing tag fails, invalid tag text and invalid
  wallet are rejected, tags are stored upper case.

Frontend (vitest):

- `ranks.ts`: order, names, precomputed star points.
- `EmblemaRango`: one SVG per rank with its `aria-label`; nothing for `null` or an unknown id.
- `NombreUsuario`: with rank and tags, with neither, while loading, after a failed fetch.
- `useBadges`: batches wallets, does not refetch cached ones, refetches after expiry.
- Chat: a message from a ranked, tagged wallet shows both; a system message shows neither.
- Profile: progress bar values, Obsidian without a bar, under 500 aiming at Bronze.

## Out of scope

- Emblems in Winners, Leaderboard, battle results and standings. With `NombreUsuario` in place,
  each is a swap of the existing `alias ?? shortWallet` for the component.
- A chat announcement when someone reaches a new rank.
- A web admin panel for tags.
- Per-tag colours.
