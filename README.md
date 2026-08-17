# 🃏 Gifs Against Humanity

Cards Against Humanity where the answers are GIFs. Everyone plays on their own phone; an
optional TV in the room is the shared stage. No accounts, no downloads, no database — open
the site, type a 4-letter code, pick a nickname.

Live at **[gifs-against-humanity.com](https://gifs-against-humanity.com)**. (The repo and
the folder are still called `game-night` — that was the original working title.)

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

python scripts/scan_gifs.py     # once: builds the GIF manifest from the folder
python run.py
```

(The 80 filler GIFs are committed; the manifest that describes them is generated, since
the curator rewrites it. `python scripts/make_placeholder_gifs.py` regenerates the filler
itself and needs the dev requirements for Pillow.)

It prints your LAN address. Everyone in the room opens that on their phone:

```
  🃏 Gifs Against Humanity
      phones on the wifi: http://192.168.1.42:5050
```

Create a game, read out the 4-letter code, and put the TV view up on a screen if you have
one: `http://192.168.1.42:5050/ABCD/tv`

## How a round works

1. One player is the **judge**. They tap "I'm ready" — nothing moves until they do, so the
   game never runs away from the room.
2. They pick one of three prompts (10s, then it picks for them).
3. Everyone else plays the funniest GIF from their hand of 7 (90s, then it plays for them).
   Playing a card immediately draws a replacement. The judge sees only face-down cards.
4. The judge flips the cards one at a time — no timer, this is the fun part — then crowns a
   winner. That player gets a point; only the winning card's author is revealed.
5. First to the target score (default 5) wins. The host can start a rematch in the same room.

4–8 players (2 with **test mode**, for playing with bots). The host picks a **mode** —
Normal, 18+ or Millennial, each dealing from its own pile of GIFs — and everything else
(judge rotation, points to win, timers, test mode) lives behind *More settings*.

## Routes

| Route | What it is |
|---|---|
| `/` | The landing page: join a code, or create a game |
| `/<CODE>` | The phone — join, lobby, play |
| `/<CODE>/tv` | The TV / spectator stage (read-only, never shows who played what) |
| `/healthz` | Room and player counts, for monitoring |

`/<CODE>` only matches four letters (a URL converter enforces it), so it can't swallow
`/healthz` or `/static`. The old `/gah/...` URLs redirect permanently to the new ones.

While a TV is connected, phones collapse to "watch the TV" during the watch-only phases and
keep only your hand and the judge's controls. Unplug the TV and the phones fill back in.

## Playtesting on your own

```bash
python run.py                                # terminal 1
python scripts/playtest_bots.py --bots 3     # terminal 2 — prints a code, bots fill the lobby
python scripts/playtest_bots.py --bots 1 --start-at 2   # you + one bot, you judge every other round
```

The bots wait for you to join, start the game, and play their part.

## Adjusting things

**The look** — `app/static/css/theme.css` holds every colour, radius, outline, shadow, font
and timing as a CSS custom property. Change a value, reload. No build step, no npm.

**The prompts** — `app/data/prompts.json`. One object per card: `{"id", "text", "blanks"}`,
with `___` marking the blank. Add or delete freely; ids only need to be unique.

**The GIFs** — use the curator below, or drop your own `.gif` files into
`app/static/gifs/` and run `python scripts/scan_gifs.py` to rebuild the manifest (it keeps
the mode tags of files it already knows). Aim for 56+ per mode so eight hands of seven don't
recycle constantly. `scripts/make_placeholder_gifs.py --count 200 --clean` regenerates
filler instead.

## Curating real GIFs

Three modes — **Normal**, **18+** and **Millennial** — each deal from their own pile of
cards. You fill those piles by swiping:

```bash
export GIPHY_API_KEY=xxxxxxxx        # developers.giphy.com -> Create an App -> API
python scripts/curate_gifs.py        # then open http://127.0.0.1:5099
```

`←` `→` scroll through GIFs; **N**, **E**, **M** toggle membership of Normal, Eighteen+ and
Millennial. A GIF can be in several sets at once, so a cursed one can be both Normal and
18+. `X` removes it from everything, and tagging is reversible — untagging deletes the file
again. Tagged GIFs land in `app/static/gifs/` and the manifest immediately, so they're in
the game next time it starts. Scrolling past something remembers it as seen, so it won't
come round again tomorrow (`curation/decisions.json`).

**Finding the weird stuff.** Type anything in the search box (`nuke`, `deep fried`,
`cursed`) and the queue switches to it. Press **R** on a GIF you like for related tags —
Giphy's suggestions where it has them, the GIF's own title words where it doesn't — and
click one to keep pulling that thread. Starter terms are on screen from the start; the
built-in packs are `reactions`, `cursed`, `millennial` and `chaos` (`--pack cursed`).

**Your own GIFs** — drag files onto the page. The ones you already collected in Discord get
copied in and tagged like anything else.

**Near-stills are filtered out** before you see them: Giphy reports a frame count and
anything under `--min-frames` (default 10) never appears.

```bash
python scripts/curate_gifs.py --source tenor              # Tenor instead of Giphy
python scripts/curate_gifs.py --rating r                  # let the edgier stuff through
python scripts/curate_gifs.py --queries "facepalm,oops"   # your own search terms
```

A mode needs **56 cards** before eight people can play it (28 for four); the lobby greys out
modes that aren't ready and the curator shows a progress meter for each. There's a button to
delete the 80 placeholders once you've got real ones.

Curated GIFs are gitignored on purpose — they came from someone else's API and this repo is
public. To get them onto the server:

```bash
GAH_HOST=root@your-server ./scripts/push_gifs.sh
```

(One-time on the server: a `docker-compose.override.yml` mounting `./app/static/gifs`, so a
push doesn't need an image rebuild. See the script's comments and DEPLOY.md.)

**The rules** — the knobs are constants at the top of `app/games/gah/engine.py`: hand size,
prompt choices, player limits, score range, and the away-grace windows.

## How it's built

Flask + Flask-SocketIO (threading mode + `simple-websocket`, so no eventlet/gevent and it
runs on Python 3.13+). Vanilla JS on the client, no framework, no bundler.

```
app/
  routes.py                 health + redirects from the old /gah/ URLs
  identity.py               the session cookie (a random id, nothing else)
  games/gah/
    engine.py               PURE game state machine: phases, rules, redacted views
    decks.py                prompt + GIF decks (draw, discard, recycle)
    rooms.py                in-memory room store, 4-letter codes, idle reaper
    events.py               Socket.IO handlers -> one engine call -> broadcast
    ticker.py               one 250ms loop: deadlines, away-players, reaping
  static/css/theme.css      all design tokens
  static/js/gah-play.js     the phone
  static/js/gah-tv.js       the TV
```

Three ideas hold it together:

- **The engine is pure Python.** No Flask, no Socket.IO, no I/O — hand it an action, it
  mutates state or raises `ActionError`. That's why the rules are testable without a browser.
- **The server is the only authority.** Every action is re-validated (right phase, right
  actor, card actually in your hand). A stale or hostile phone can't corrupt a game.
- **Redaction happens in the engine, not the templates.** `view_for(pid)` builds a payload
  per recipient. A hidden card is sent as `{slot, revealed: false}` — no GIF id, no author —
  so there is nothing to dig out of devtools.

### Sessions and privacy

Your browser gets a signed session cookie holding one random id. It has no `Expires`, so the
browser drops it when it closes. Nicknames, hands and scores live in server memory keyed by
that id, and the whole room is deleted 10 minutes after the last person leaves. Nothing is
written to disk; there's no database and no tracking between games. Refreshing or locking
your phone keeps your seat — same cookie, same seat, same hand and score.

Because state is in memory, restarting the server ends games in progress. That's the trade
for zero setup.

## Tests

```bash
pytest                                        # 56 tests, no browser needed
python scripts/simulate_game.py --players 8   # a real full game over real websockets
```

- `tests/test_engine.py` — the rules: dealing, refills, deck recycling, both judge
  rotations, timeouts, away-player and away-judge handling, scoring, rematch, and that no
  view ever leaks another player's hand or an unrevealed card's author.
- `tests/test_events.py` — the transport: cookie identity, room membership, reconnects
  keeping their seat, late joiners refused, host-only actions, judge-only actions.
- `scripts/simulate_game.py` — bots play a whole game against the running server and assert
  a single champion plus no leaked cards. Slower with 8 bots (they share one interpreter);
  the timeout scales with the bot count.

## Running it for real (Docker)

```bash
cp .env.example .env          # set SECRET_KEY; everything else has a default
docker compose up -d --build
curl localhost:5050/healthz
```

One container, published on `127.0.0.1:5050` only — your own nginx/Caddy/Traefik terminates
TLS in front of it. **[DEPLOY.md](DEPLOY.md)** has the whole VPS walkthrough: paste-ready
proxy snippets with the websocket upgrade headers, the DNS step, when to turn on the Secure
cookie, and troubleshooting.

Inside the image it's `gunicorn` with a single gevent websocket worker on Python 3.12.
One worker is deliberate: rooms live in that process's memory, so a second one would hand
two players with the same code two different games.

## If you ever add a second game

The game still lives in its own package (`app/games/gah/`, its own blueprint, engine and
templates), so a second one would be a sibling package with the same split: a pure engine,
a room store, Socket.IO events. You'd give it a URL prefix of its own and put a small
chooser back at `/` — this site deliberately hands the root to Gifs Against Humanity
instead.

## Notes

- `kill -USR1 <pid>` dumps every server thread's stack without killing it — handy if a game
  ever looks stuck.
- The Socket.IO client is vendored at `app/static/js/vendor/socket.io.min.js`, so the game
  works on a LAN with no internet. The only network-dependent nicety is the Google Fonts
  link in `base.html`; the fallback stacks in `theme.css` cover its absence.
- Locally, Socket.IO runs in threading mode (works on any Python, including 3.13+). The
  Docker image switches to gevent via `GN_ASYNC_MODE`, monkey-patched in `wsgi.py`.
- Port 5050, not 5000: macOS AirPlay Receiver already owns 5000.
