"""Card sources for Gifs Against Humanity: the prompt deck and the GIF deck.

Both are draw-pile/discard-pile decks that reshuffle when exhausted, so a long night
never runs out of cards. Loading is cached -- the JSON files are read once per process.
"""

from __future__ import annotations

import json
import random
from functools import lru_cache
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2]
PROMPTS_PATH = APP_DIR / "data" / "prompts.json"
GIF_MANIFEST_PATH = APP_DIR / "static" / "gifs" / "manifest.json"


class DeckError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_prompts() -> tuple[dict, ...]:
    """[{id, text, blanks}] from app/data/prompts.json."""
    raw = json.loads(PROMPTS_PATH.read_text())
    prompts = raw["prompts"] if isinstance(raw, dict) else raw
    out = []
    for p in prompts:
        if not p.get("id") or not p.get("text"):
            continue
        out.append({"id": p["id"], "text": p["text"], "blanks": int(p.get("blanks", 1))})
    if len(out) < 10:
        raise DeckError(f"only {len(out)} prompts loaded from {PROMPTS_PATH}")
    return tuple(out)


@lru_cache(maxsize=1)
def load_gifs() -> tuple[dict, ...]:
    """[{id, file, label}] from app/static/gifs/manifest.json."""
    if not GIF_MANIFEST_PATH.exists():
        raise DeckError(
            f"{GIF_MANIFEST_PATH} missing -- run `python scripts/make_placeholder_gifs.py`"
        )
    raw = json.loads(GIF_MANIFEST_PATH.read_text())
    gifs = raw["gifs"] if isinstance(raw, dict) else raw
    out = []
    for g in gifs:
        if not g.get("id") or not g.get("file"):
            continue
        out.append({"id": g["id"], "file": g["file"], "label": g.get("label", g["id"])})
    if len(out) < 16:
        raise DeckError(f"only {len(out)} gifs loaded from {GIF_MANIFEST_PATH}")
    return tuple(out)


def gif_index() -> dict[str, dict]:
    return {g["id"]: g for g in load_gifs()}


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


def build_gif_deck(rng: random.Random) -> Deck:
    return Deck([g["id"] for g in load_gifs()], rng)


def build_prompt_deck(rng: random.Random) -> Deck:
    return Deck([p["id"] for p in load_prompts()], rng)


@lru_cache(maxsize=1)
def prompt_index() -> dict[str, dict]:
    return {p["id"]: p for p in load_prompts()}
