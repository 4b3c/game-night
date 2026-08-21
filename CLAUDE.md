# CLAUDE.md

Guidance for Claude Code working in this repo.

## What this is

**Gifs Against Humanity** — Cards Against Humanity where the answers are GIFs. Flask +
Flask-SocketIO, vanilla JS, no database, no bundler. Everyone plays on their phone at
`/<CODE>`; an optional TV at `/<CODE>/tv` is the shared stage.

The repo and folder are called `game-night` — the original working title. The product is
not. Don't put "Game Night" in anything a player sees.

`README.md` is the full tour (rules, curator, deck model, deployment); `DEPLOY.md` is the
VPS walkthrough. This file is the part that matters while editing code.

## Commands

```bash
source .venv/bin/activate

python -m pytest                 # 77 tests, ~0.5s.  NOT bare `pytest` — see below
python run.py                    # localhost:5050, prints the LAN address for phones
python scripts/simulate_game.py --players 8   # bots play a real game over real websockets
python scripts/playtest_bots.py --bots 3      # bots fill a lobby you're sitting in
python scripts/rehydrate_gifs.py [--check|--prune]   # rebuild app/static/gifs/ from library.json
python scripts/curate_gifs.py    # the deck curator on :5099 (needs GIPHY_API_KEY)
```

**Bare `pytest` fails** with `ModuleNotFoundError: No module named 'app'`. There is no
`conftest.py` at the root, so only `python -m pytest` (which puts the CWD on `sys.path`)
collects. Always run it that way.

`package.json`/`tailwind.config.js` are leftovers from the starter template. Tailwind is
not used and there is no build step — don't add one.

## Architecture

```
app/
  __init__.py               create_app: the `code` URL converter, ?v= asset fingerprints, ProxyFix
  routes.py                 /healthz + 301s from the old /gah/* URLs
  identity.py               the session cookie: one random pid, nothing else
  games/gah/
    engine.py               PURE state machine — phases, rules, redacted views
    decks.py                prompt + GIF decks, re-read from disk when the file changes
    rooms.py                in-memory room store, 4-letter codes, one RLock, idle reaper
    events.py               Socket.IO handlers: who is talking -> one engine call -> broadcast
    ticker.py               one 250ms loop for every room: deadlines, away players, reaping
  templates/gah/*.html      landing, play, tv
  static/css/theme.css      every colour/radius/shadow/duration as a custom property
  static/js/gah-play.js     the phone     gah-tv.js the TV     gn-socket.js shared plumbing
curation_store.py           the only writer of curation/*.json (flock + threading lock)
```

Three invariants. Breaking one is a bug even if the game still works:

1. **The engine is pure.** No Flask, no Socket.IO, no I/O, no clock beyond `time.time()`.
   Hand it an action; it mutates state or raises `ActionError`. New rules go here, not in
   `events.py` and never in a template or in JS.
2. **The server is the only authority.** Every action is re-validated in the engine (right
   phase, right actor, card actually in that hand). The client renders `state` and sends
   taps; it decides nothing.
3. **Redaction happens in the engine.** `view_for(pid)` / `view_for_tv()` build a payload
   per recipient; a hidden card ships as `{slot, revealed: false}` with no gif id and no
   author. Never widen a view to make the client simpler — the tests assert no view leaks
   another player's hand or an unrevealed card's author.

State lives in this process's memory (`rooms`), so a restart ends games in progress. That
is the accepted trade. All access goes through `rooms.lock`; `events.py` holds it while
building views.

## Things that will bite you

- **One worker, no reloader.** Rooms are in process memory, so a second gunicorn worker or
  Flask's reloader would serve the same code two different games. `run.py` passes
  `use_reloader=False`; the Docker image runs exactly one gevent worker.
- **Async mode.** Locally threading (works on 3.13+, no monkey-patching). In Docker,
  `wsgi.py` monkey-patches gevent *before any other import* — that is what makes
  `rooms.py`'s `threading.RLock` and the ticker safe. Don't reorder those imports.
- **Port 5050**, not 5000: macOS AirPlay Receiver owns 5000.
- **`/<code:code>` is a 4-letter converter.** A bare `/<code>` at the site root would
  swallow `/healthz` and `/static`.
- **Static URLs carry `?v=<fingerprint>`** via `@app.url_defaults`. It's what makes a
  deploy reach players through Cloudflare's cache. Directories are skipped deliberately —
  the GIF base path is a prefix the client concatenates onto.
- **`GN_COOKIE_SECURE=1` over plain http means nobody can join.** Same for
  `CURATOR_COOKIE_SECURE`. Turn them on only after TLS works.
- **Decks reload on mtime.** `curation/prompts.json` and `app/static/gifs/manifest.json`
  are re-read when they change, so the curator can edit the deck while a game runs.
- **Only `curation_store.py` writes `curation/*.json`.** Two processes (game and curator,
  separate containers) write counters, so every write takes the flock and re-reads first.
  Recording round stats is failure-tolerant by design: a read-only curation folder costs
  statistics, never a game.
- **The GIF files are not in git** and this repo is public. `curation/library.json` is the
  deck; `rehydrate_gifs.py` rebuilds the folder from it. Never commit `.gif` files —
  `curation/originals/` is the one exception, for links that expire (Discord), and is meant
  to stay a handful of files.

## Conventions

- **Comments explain why, not what.** The existing ones are load-bearing history — the
  reason a rule is written the odd way it is. Match that density and keep them accurate
  when you change the code beneath them.
- **Tuning knobs are constants at the top of the file they govern** (`engine.py`: hand
  size, player limits, grace windows; `decks.py`: sets).
- **All design values live in `theme.css`.** No hardcoded colours or radii in
  `base.css`/`game.css`/`tv.css`.
- **JS has a DOM contract** documented at the top of `gah-play.js` (ids and `data-*`
  hooks). Restyle freely; keep those hooks. Untrusted text goes through `GN.esc()` before
  it goes near `innerHTML`.
- **No new dependencies** without a reason worth writing down. Three requirements files:
  `requirements.txt` (runtime), `-dev` (tests, Pillow), `-prod` (gunicorn + gevent).
- **Never trigger a browser dialog** in tests or scripts; the game has none.

## Commit messages

Sentence-style subject, lowercase after the first word, describing what changed for a
person: *"Pack the answers into columns, and rebuild the top bar"*, *"Let the mouse wheel
scroll the page again"*. No `feat:`/`fix:` prefixes. The body is prose that explains the
reasoning, the trade-off, and how it was verified — measurements and counts where they
exist. Read `git log` before writing one.

## Prompts and licensing

The shipped deck is 100 original prompts (`source: "original"`), 50 normal and 50 18+,
written as plain statements — no `___`, no trailing punctuation, no question marks. The
GIFs are overwhelmingly reaction GIFs, so a prompt has to be something to react *to*
("I'm pregnant", "The wifi is out"), stated vaguely enough that half a dozen different
faces are all funny answers. `blanks: 0` is normal here; the engine still counts blanks
from the text, so a `___` prompt keeps working if you write one.

Real Cards Against Humanity black cards are an import, not part of the deck:
`scripts/import_cah_prompts.py` pulls them into the 18+ pile under CC BY-NC-SA 4.0. If you
run it, the licence's three conditions — attribution, non-commercial, share-alike — are in
`LICENSE-PROMPTS.md`, every imported row carries `source` and `pack`, and that metadata
has to stay intact and the game free. Official packs and single-blank cards only, and
re-import rather than pasting rows by hand.
