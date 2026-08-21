"""Card sources for Gifs Against Humanity: the prompt deck and the GIF deck.

Both are draw-pile/discard-pile decks that reshuffle when exhausted, so a long night
never runs out of cards.

Both are also re-read whenever their file changes on disk, rather than cached for the
life of the process: the curator (scripts/curate_gifs.py) writes to both while the game
is running, and anything you tag or write there should be in the next game without a
restart.
"""

from __future__ import annotations

import json
import random
import threading
from pathlib import Path
from typing import Callable

from ...assets import file_token

APP_DIR = Path(__file__).resolve().parents[2]
PROMPTS_PATH = APP_DIR.parent / "curation" / "prompts.json"
GIF_MANIFEST_PATH = APP_DIR / "static" / "gifs" / "manifest.json"


class DeckError(RuntimeError):
    pass


class _Reloading:
    """A file's contents, parsed once and then again each time the file changes.

    Holds whatever `build` returned, so anything derived from the file — an id index,
    say — is cached with it and rebuilt on the same trigger rather than on every call.
    """

    def __init__(self, path: Path, build: Callable[[], object]):
        self.path = path
        self.build = build
        self.lock = threading.Lock()
        self.cached: tuple[int, object] | None = None

    def stamp(self) -> int:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return 0

    def get(self):
        stamp = self.stamp()
        cached = self.cached
        if cached is not None and cached[0] == stamp:
            return cached[1]
        with self.lock:
            cached = self.cached
            if cached is not None and cached[0] == stamp:
                return cached[1]
            built = self.build()
            self.cached = (stamp, built)
            return built


def _read_prompts() -> tuple[tuple[dict, ...], dict[str, dict]]:
    """[{id, text, blanks, sets}] from curation/prompts.json, plus an index by id.

    Prompts live beside the cards, in the folder both containers mount, because the
    curator writes them: they used to be baked into the image at app/data/prompts.json,
    where changing one meant a commit and a rebuild.
    """
    if not PROMPTS_PATH.exists():
        raise DeckError(f"{PROMPTS_PATH} missing — prompts live there now, not in app/data")
    raw = json.loads(PROMPTS_PATH.read_text())
    rows = raw["prompts"] if isinstance(raw, dict) else raw
    # A dict keyed by id is the file's shape; a list of entries is the older one.
    pairs = rows.items() if isinstance(rows, dict) else ((p.get("id"), p) for p in rows)
    out = []
    for prompt_id, p in pairs:
        if not prompt_id or not p.get("text"):
            continue
        out.append(
            {
                "id": prompt_id,
                "text": p["text"],
                "blanks": int(p.get("blanks", 1)),
                "sets": _sets_of(p),
            }
        )
    if len(out) < 10:
        raise DeckError(f"only {len(out)} prompts loaded from {PROMPTS_PATH}")
    prompts = tuple(out)
    return prompts, {p["id"]: p for p in prompts}


def load_prompts() -> tuple[dict, ...]:
    return _prompt_file.get()[0]


def load_gifs() -> tuple[dict, ...]:
    """[{id, file, label, sets}] from app/static/gifs/manifest.json.

    Entries whose file isn't on disk are skipped: the manifest travels in git while the
    curated GIFs are rsynced separately, so a partially-synced folder should mean fewer
    cards, not broken pictures mid-game.
    """
    return _gif_file.get()[0]


def _read_manifest() -> tuple[tuple[dict, ...], dict[str, dict]]:
    if not GIF_MANIFEST_PATH.exists():
        raise DeckError(
            f"{GIF_MANIFEST_PATH} missing -- run `python scripts/rehydrate_gifs.py` to "
            f"rebuild the cards from curation/library.json"
        )
    raw = json.loads(GIF_MANIFEST_PATH.read_text())
    rows = raw["gifs"] if isinstance(raw, dict) else raw
    out = []
    missing = 0
    for g in rows:
        if not g.get("id") or not g.get("file"):
            continue
        token = file_token(GIF_MANIFEST_PATH.parent / g["file"])
        if token is None:
            missing += 1
            continue
        entry = {
            "id": g["id"],
            "file": g["file"],
            "label": g.get("label", g["id"]),
            # The client hangs this on the URL as `?v=`. A GIF folder is one long-lived
            # path behind a CDN that caches for a week, so re-curating a card under the
            # same filename would otherwise never reach anyone. Worked out here rather
            # than stored in the manifest: the file is the truth, and this runs again
            # every time the manifest changes.
            "v": token,
            # Which pile this card is in — Normal or 18+. Written by
            # scripts/curate_gifs.py. `rating` is the older single-tag form.
            "sets": _sets_of(g),
        }
        # The GIF's real shape, so a card can reserve the right space before the picture
        # arrives instead of forcing everything into a box and cropping it. Older
        # manifests don't have it; the client falls back to 4:3.
        if g.get("w") and g.get("h"):
            entry["w"], entry["h"] = int(g["w"]), int(g["h"])
        out.append(entry)
    if missing:
        print(f"[gah] {missing} gif(s) in the manifest are not on disk — skipping them")
    if len(out) < 16:
        raise DeckError(f"only {len(out)} gifs loaded from {GIF_MANIFEST_PATH}")
    gifs = tuple(out)
    return gifs, {g["id"]: g for g in gifs}


_prompt_file = _Reloading(PROMPTS_PATH, _read_prompts)
_gif_file = _Reloading(GIF_MANIFEST_PATH, _read_manifest)


def _sets_of(entry: dict) -> tuple[str, ...]:
    """Piles a card or prompt belongs to, tolerating older single-`rating` manifests.

    An empty list is honoured as "no pile" rather than treated as missing: a prompt with
    both sets turned off is retired but still on file, which is a state cards can't be in
    (untagging a card removes it outright).
    """
    sets = entry.get("sets")
    if isinstance(sets, (list, tuple)):
        return tuple(str(s) for s in sets)
    return ("adult",) if entry.get("rating") == "adult" else ("normal",)


# --- the two piles ---------------------------------------------------------------
# Not modes: one deck with a switch on it. Everything clean is `normal` and plays in every
# game; `adult` is the pile that only joins in when the host turns "keep it clean" off. So
# a dirty game is a superset of a clean one, never a different game.
#
# There used to be a third pile, Millennial, picked as a mode alongside the other two. It
# split a deck that was already thin across three piles nobody filled, and the joke was
# never in the cards being millennial — it was in the GIF.
SETS: dict[str, dict] = {
    "normal": {"label": "Normal"},
    "adult": {"label": "18+"},
}
CLEAN_BY_DEFAULT = True


def _in_play(entry: dict, clean: bool) -> bool:
    sets = entry["sets"]
    return "normal" in sets or (not clean and "adult" in sets)


def gifs_for(clean: bool) -> tuple[dict, ...]:
    """The cards a game deals from. Clean leaves the 18+ pile out; dirty adds it on top."""
    return tuple(g for g in load_gifs() if _in_play(g, clean))


def prompts_for(clean: bool) -> tuple[dict, ...]:
    """The prompts a game deals from, on the same switch as the cards."""
    return tuple(p for p in load_prompts() if _in_play(p, clean))


def deck_counts() -> dict[str, dict[str, int]]:
    """What each side of the switch is worth, so the lobby can show what turning it off
    would add before anyone commits to it."""
    return {
        "clean": {"gifs": len(gifs_for(True)), "prompts": len(prompts_for(True))},
        "spicy": {"gifs": len(gifs_for(False)), "prompts": len(prompts_for(False))},
    }


def gif_index() -> dict[str, dict]:
    return _gif_file.get()[1]


class Deck:
    """A shuffled draw pile with a discard pile that recycles when the draw pile empties."""

    def __init__(self, ids: list[str], rng: random.Random):
        self._rng = rng
        self.draw_pile: list[str] = list(ids)
        self._rng.shuffle(self.draw_pile)
        self.discard_pile: list[str] = []

    def __len__(self) -> int:
        return len(self.draw_pile) + len(self.discard_pile)

    def draw(self) -> str:
        if not self.draw_pile:
            if not self.discard_pile:
                raise DeckError("deck is completely empty")
            self.draw_pile = self.discard_pile
            self.discard_pile = []
            self._rng.shuffle(self.draw_pile)
        return self.draw_pile.pop()

    def draw_many(self, n: int) -> list[str]:
        return [self.draw() for _ in range(n)]

    def discard(self, *ids: str) -> None:
        self.discard_pile.extend(ids)


def build_gif_deck(rng: random.Random, clean: bool = CLEAN_BY_DEFAULT) -> Deck:
    return Deck([g["id"] for g in gifs_for(clean)], rng)


def build_prompt_deck(rng: random.Random, clean: bool = CLEAN_BY_DEFAULT) -> Deck:
    return Deck([p["id"] for p in prompts_for(clean)], rng)


def prompt_index() -> dict[str, dict]:
    """Every prompt by id, in play or not — a round in progress still has to be able to
    look up the prompt it is already showing, even if it was retired mid-game."""
    return _prompt_file.get()[1]
