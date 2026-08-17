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
        if not (GIF_MANIFEST_PATH.parent / g["file"]).is_file():
            missing += 1
            continue
        entry = {
            "id": g["id"],
            "file": g["file"],
            "label": g.get("label", g["id"]),
            # Which modes this card belongs to — a card can be in several. Written by
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
    """Modes a card or prompt belongs to, tolerating older single-`rating` manifests.

    An empty list is honoured as "no modes" rather than treated as missing: a prompt with
    every set turned off is retired but still on file, which is a state cards can't be in
    (untagging a card removes it outright).
    """
    sets = entry.get("sets")
    if isinstance(sets, (list, tuple)):
        return tuple(str(s) for s in sets)
    legacy = {"sfw": "normal", "adult": "adult", "millennial": "millennial"}
    return (legacy.get(entry.get("rating", "sfw"), "normal"),)


# --- modes ---------------------------------------------------------------------
# Each mode deals from its own set of cards *and* its own set of prompts, both tagged by
# scripts/curate_gifs.py. Adding a fourth mode is one entry here plus one button in the
# lobby.
MODES: dict[str, dict] = {
    "normal": {"label": "Normal", "emoji": "🙂"},
    "adult": {"label": "18+", "emoji": "🌶️"},
    "millennial": {"label": "Millennial", "emoji": "📼"},
}
DEFAULT_MODE = "normal"


def gifs_for_mode(mode: str) -> tuple[dict, ...]:
    """The cards this mode plays with. A card can belong to more than one mode."""
    wanted = mode if mode in MODES else DEFAULT_MODE
    return tuple(g for g in load_gifs() if wanted in g["sets"])


def prompts_for_mode(mode: str) -> tuple[dict, ...]:
    """The prompts this mode plays with. A prompt can belong to more than one mode."""
    wanted = mode if mode in MODES else DEFAULT_MODE
    return tuple(p for p in load_prompts() if wanted in p["sets"])


def mode_counts() -> dict[str, int]:
    """How many cards each mode has — the lobby shows this so you can see what's ready."""
    return {name: len(gifs_for_mode(name)) for name in MODES}


def prompt_counts() -> dict[str, int]:
    """How many prompts each mode has. A mode with none can't be played at all."""
    return {name: len(prompts_for_mode(name)) for name in MODES}


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


def build_gif_deck(rng: random.Random, mode: str = DEFAULT_MODE) -> Deck:
    return Deck([g["id"] for g in gifs_for_mode(mode)], rng)


def build_prompt_deck(rng: random.Random, mode: str = DEFAULT_MODE) -> Deck:
    return Deck([p["id"] for p in prompts_for_mode(mode)], rng)


def prompt_index() -> dict[str, dict]:
    """Every prompt by id, mode or no mode — a round in progress still has to be able to
    look up the prompt it is already showing, even if it was retired mid-game."""
    return _prompt_file.get()[1]
