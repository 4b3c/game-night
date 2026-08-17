"""The two files that describe the deck, and the only code that writes them.

    curation/library.json   every card that is in the game
    curation/ignored.json   cards deliberately rejected, so search stops offering them

That is the whole model. It replaces an older `decisions.json` that mixed three things
together: cards in the game, cards explicitly rejected, and a long tail of "this scrolled
past your eyes once" — the last of which was 93% of the file and meant nothing.

A library card records where it came from (which is what makes it rebuildable — see
scripts/rehydrate_gifs.py), what sets it is in, and how it has actually performed:

    uses   rounds it was played in
    wins   rounds it won

so `wins / uses` is a real answer to "is this card funny", earned at the table rather
than guessed at by whoever tagged it.

Two processes touch these files — the curator and the game, in separate containers, both
writing counters — so every write takes an exclusive lock and re-reads first. The read is
locked too, or a reader could catch a half-written file.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "curation"
LIBRARY = STATE_DIR / "library.json"
IGNORED = STATE_DIR / "ignored.json"
LOCK = STATE_DIR / ".lock"

# Within one process, threads share the file lock's fate, so guard them too: flock is
# per-file-descriptor and two threads in one process would otherwise interleave freely.
_local_lock = threading.RLock()


def now() -> str:
    """A timestamp for when something entered the group it is in."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def _exclusive():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with _local_lock:
        with open(LOCK, "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _read(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "cards": {}}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"version": 1, "cards": {}}
    data.setdefault("cards", {})
    return data


def _write(path: Path, data: dict) -> None:
    """Replace the file atomically, so a reader never sees it half-written."""
    data["cards"] = dict(sorted(data.get("cards", {}).items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(handle, "w") as out:
        json.dump(data, out, indent=2)
        out.write("\n")
    os.replace(temporary, path)


# --- reading ---------------------------------------------------------------------
def library() -> dict[str, dict]:
    with _exclusive():
        return _read(LIBRARY)["cards"]


def ignored() -> dict[str, dict]:
    with _exclusive():
        return _read(IGNORED)["cards"]


# --- writing ---------------------------------------------------------------------
def update_library(change: Callable[[dict], None]) -> dict[str, dict]:
    """Re-read, apply `change` to the cards dict, write back — all under the lock."""
    with _exclusive():
        data = _read(LIBRARY)
        change(data["cards"])
        _write(LIBRARY, data)
        return data["cards"]


def put(source_id: str, fields: dict) -> dict:
    """Add or update one card, stamping `added` the first time it appears."""
    result = {}

    def change(cards: dict) -> None:
        card = cards.setdefault(source_id, {"added": now(), "uses": 0, "wins": 0})
        card.update({k: v for k, v in fields.items() if v is not None})
        card.setdefault("added", now())
        card.setdefault("uses", 0)
        card.setdefault("wins", 0)
        result.update(card)

    update_library(change)
    return result


def forget(source_id: str) -> None:
    """Drop a card from the library. Its counters go with it — see `ignore` for the
    case where you want it remembered as a no."""
    update_library(lambda cards: cards.pop(source_id, None))


def ignore(source_id: str, title: str = "", page: str | None = None) -> None:
    """Mark a card as one you never want offered again."""
    with _exclusive():
        data = _read(IGNORED)
        entry = data["cards"].setdefault(source_id, {"ignored": now()})
        entry["title"] = title or entry.get("title", "")
        if page:
            entry["page"] = page
        entry.setdefault("ignored", now())
        _write(IGNORED, data)


def unignore(source_id: str) -> None:
    with _exclusive():
        data = _read(IGNORED)
        if data["cards"].pop(source_id, None) is not None:
            _write(IGNORED, data)


# --- what the game reports back ---------------------------------------------------
def record_round(used: list[str], winner: str | None) -> None:
    """One completed round: every card played gets a use, the winner also gets a win.

    Counted at the end of a round rather than at submit time, so an abandoned round
    doesn't inflate anything. Cards no longer in the library (retagged away mid-game)
    are skipped rather than resurrected.
    """
    def change(cards: dict) -> None:
        for source_id in used:
            card = cards.get(source_id)
            if card is not None:
                card["uses"] = int(card.get("uses", 0)) + 1
        if winner and winner in cards:
            cards[winner]["wins"] = int(cards[winner].get("wins", 0)) + 1

    update_library(change)


def by_card_id() -> dict[str, str]:
    """card id -> source_id.

    The game knows a card by its manifest id (the filename's stem); the library is keyed
    by where the card came from. This is the join between the two.
    """
    return {Path(c["file"]).stem: sid for sid, c in library().items() if c.get("file")}


def record_played_ids(played: list[str], winner: str | None) -> None:
    """Same as record_round, but in the game's vocabulary of card ids."""
    index = by_card_id()
    record_round([index[i] for i in played if i in index], index.get(winner or ""))
