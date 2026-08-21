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

python scripts/rehydrate_gifs.py  # once: downloads the cards this repo describes
python run.py
```

(The GIF *files* aren't in git — see [Where the GIFs live](#where-the-gifs-live). What is
in git is `curation/library.json`, and `rehydrate_gifs.py` turns that back into a full
`app/static/gifs/` in about half a minute. It needs a `GIPHY_API_KEY` in `.env`.)

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

4–8 players, or down to 1 with **test mode** — there the judge answers their own prompt
too, so you can walk a whole round on your own, and none of it counts towards the deck's
statistics. The host gets one switch — **keep it clean**, on by default; turn it off and
the 18+ cards and prompts are mixed in — and everything else (judge rotation, points to
win, timers, test mode) lives behind *More settings*.

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

**The prompts** — write them in the curator's **Prompts** tab, or edit
`curation/prompts.json` directly: one entry per prompt, keyed by id, with `sets` naming
the pile it's in and `___` marking a blank if the prompt has one.

The deck that ships is 100 original prompts, 50 in each pile, and they're flat
statements rather than fill-in-the-blanks — *"Happy birthday"*, *"I caught a deadly
disease"*, *"My grandma told all her friends about me"*. Most GIFs worth playing are
reactions, so the prompt's job is to be the thing reacted to: state a situation, leave it
vague, and let seven people answer it with a face.

The real **Cards Against Humanity** black cards are a supported import rather than part
of the deck: CAH release their own game under CC BY-NC-SA 4.0, the terms that come with
it are in [LICENSE-PROMPTS.md](LICENSE-PROMPTS.md), and
`scripts/import_cah_prompts.py` puts them in the 18+ pile if you want them.

**The GIFs** — use the curator below. Aim for 56+ in the Normal pile so eight hands of
seven don't recycle constantly.

## Curating the deck

There is one deck with a switch on it. Everything tagged **Normal** plays in every game;
everything tagged **18+** joins in only when the host turns *keep it clean* off — so a
dirty game is a clean one plus the spicy pile, never a different game. Cards and prompts
are tagged the same way, because both come off the same switch. You fill both piles here:

```bash
export GIPHY_API_KEY=xxxxxxxx        # developers.giphy.com -> Create an App -> API
python scripts/curate_gifs.py        # then open http://127.0.0.1:5099
```

**Discover** is a search box and a grid of results. Type anything, get 50, tag the ones
you want. There are no built-in search terms: the tool used to invent them from a
hardcoded list of ~100 moods, which produced under a quarter of the deck while hand-typed
searches produced three quarters — so it stopped guessing.

The two piles are exclusive — "in both" would just be a longer way of writing Normal,
since 18+ tops Normal up rather than replacing it. Tagging is reversible, and untagging
deletes the file again. Tagged GIFs land in `app/static/gifs/` and the manifest
immediately, so they're in the game next time it starts.

**Your own GIFs** — paste links into the box on the Library tab. Tenor, Discord, anywhere:
a page gets resolved to the GIF it advertises, a direct link is used as-is. Links rather
than file uploads, because a link is what lets the card be rebuilt on another machine.

```bash
python scripts/curate_gifs.py --source tenor    # Tenor instead of Giphy
python scripts/curate_gifs.py --rating pg-13    # tighten what the API returns
```

The deck needs **56 cards** before eight people can play (28 for four); the lobby disables
Start and says what's missing, and the curator shows a progress meter for the Normal pile.
The 18+ pile has no target — it tops the deck up, so any number of them is a fine number.

**Library and Discover are the same grid.** Library shows what's in the game; Discover
shows what a search turned up. Under every card are the two pile buttons — tap **Normal**
or **18+** to put it in one, tap the lit one to take it out of the game — and a ✕ that
means *not funny*: the card leaves the game and search never offers it again. The Rejected
filter lists those and can undo any of them.

**Prompts** is the same idea for the words. Type one — or several, one per line — pick the
pile it belongs in, and Add. Every prompt already written is listed below, filterable by
pile, with the same two buttons: tap **18+** on a prompt and it stops appearing in clean
games. Click any line to edit it; it saves when you click away. The ✕ deletes, and asks
once first, because unlike a card there is no rejected list to fish it back out of.

Tapping the lit pile leaves a prompt on file but out of play — a soft retirement, and the
only state a card can't be in.

## Where the deck lives

The GIF files are **not in git**, and the repo is public on purpose. What's committed is
three small JSON files in `curation/`, and they are the whole state of the deck:

| | |
|---|---|
| `library.json` | every card in the game: the link it came from, its sets, its size, and how often it has been **played** and **won** |
| `ignored.json` | cards you rejected with ✕ — read only by search, so they stop coming back |
| `prompts.json` | every prompt, its text, the pile it's in, and how often it has been **played** (`source` says who wrote it — see [LICENSE-PROMPTS.md](LICENSE-PROMPTS.md)) |

Each stamps when an entry joined the list it's in. That's a few tens of KB of text that
diffs cleanly, versus ~70 MB of binaries that would sit in the history forever and make
the repo heavy for anyone who clones it.

`uses` and `wins` are written by the **game**, at the end of each round: every card played
gets a use, the winner also gets a win, and the prompt they were answering gets a use of
its own. So `wins / uses` is a real measure of whether a card is funny, and a prompt's
`uses` is a real measure of whether judges reach for it — earned at the table rather than
guessed at when it was tagged.

Only the deployed server keeps score. Recording is off unless `GN_RECORD_STATS=1`, which
`docker-compose.yml` sets and a laptop doesn't, so `python run.py`, the bot scripts and
the tests all leave the record alone. Test-mode games — the two-player ones you start to
try something out — are never counted, on any machine.

```bash
python scripts/rehydrate_gifs.py            # rebuild app/static/gifs/ from library.json
python scripts/rehydrate_gifs.py --check    # what's missing, without downloading
python scripts/rehydrate_gifs.py --prune    # drop files the library no longer lists
```

Two kinds of link, one script: `giphy:<id>` is looked up through the API (ids are
permanent, media URLs aren't), and `url:<digest>` is fetched straight over https. The
manifest the game reads is regenerated from `library.json` each time, so the two can't
drift.

The one exception is links that expire — a Discord attachment URL is signed and dies after
about a day. Those cards keep their bytes in `curation/originals/`, which *is* committed.
It's meant to be a handful of files, never a library.

To get cards onto the server, either curate there directly (below) or rsync:

```bash
GAH_HOST=root@your-server ./scripts/push_gifs.sh
```

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
