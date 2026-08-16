"""The list of games on the platform.

Adding game #2 is: build `app/games/<slug>/`, append a GameInfo here, and (if it uses
room codes) call `register_code_resolver` from that package.
"""

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class GameInfo:
    slug: str
    name: str
    short_name: str
    tagline: str
    emoji: str
    players: str
    accent: str  # theme token name: accent-1 .. accent-4
    available: bool = True
    create_endpoint: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


GAMES: list[GameInfo] = [
    GameInfo(
        slug="gah",
        name="Gifs Against Humanity",
        short_name="GAH",
        tagline="Answer terrible prompts with worse GIFs. A judge crowns the funniest.",
        emoji="🃏",
        players="4–8 players",
        accent="accent-1",
        available=True,
        create_endpoint="gah.new_game",
        tags=("party", "phones", "18+ humour"),
    ),
    GameInfo(
        slug="doodle",
        name="Doodle Panic",
        short_name="Doodle",
        tagline="Everyone draws the same prompt. Nobody can draw.",
        emoji="🎨",
        players="3–10 players",
        accent="accent-2",
        available=False,
    ),
    GameInfo(
        slug="fibbers",
        name="Fibbers",
        short_name="Fibbers",
        tagline="Invent a definition. Fool the room. Spot the liar.",
        emoji="🤥",
        players="4–12 players",
        accent="accent-3",
        available=False,
    ),
]


def live_games() -> list[GameInfo]:
    return [g for g in GAMES if g.available]


# --- room-code lookup ----------------------------------------------------------
# Each game with room codes registers a resolver so the home page's Join box can find
# which game a bare 4-letter code belongs to.
_RESOLVERS: dict[str, Callable[[str], bool]] = {}


def register_code_resolver(slug: str, fn: Callable[[str], bool]) -> None:
    _RESOLVERS[slug] = fn


def find_game_for_code(code: str) -> GameInfo | None:
    code = (code or "").strip().upper()
    if not code:
        return None
    for game in GAMES:
        resolver = _RESOLVERS.get(game.slug)
        if resolver and resolver(code):
            return game
    return None
