"""In-memory room store.

Everything lives in this process's memory: a restart ends any game in progress, which is
the right trade for a LAN party app. All access goes through one reentrant lock, and the
lock is exported so the Socket.IO layer can hold it while it reads state to build views.

Swapping this for Redis later means reimplementing this class, not touching the engine.
"""

from __future__ import annotations

import os
import random
import string
import threading
import time

from .engine import Game

# I and O are left out so nobody squints at a TV wondering if it's a 1 or a 0.
CODE_ALPHABET = "".join(c for c in string.ascii_uppercase if c not in "IO")
CODE_LENGTH = 4
DEFAULT_IDLE_TIMEOUT = 600  # 10 minutes


#: Whether finished rounds are written back to curation/. Off unless something says
#: otherwise, so a laptop running `python run.py` — or a test suite, or the bot scripts —
#: never edits the deck's record of how it plays. docker-compose.yml turns it on, which
#: makes "the deployed container" the definition of a game that counts.
RECORD_STATS = os.environ.get("GN_RECORD_STATS", "0").strip().lower() in {"1", "true", "yes", "on"}


def _record_round(played: list[str], winner: str, prompt_id: str | None = None) -> None:
    """Tell the curation library which cards were played, which won, and on what prompt.

    This is where the game stops being pure — deliberately at the edge, in the thing that
    builds games, rather than inside the engine. Imported lazily and failure-tolerant: a
    read-only or absent curation folder must cost a round of statistics, never the game.
    """
    if not RECORD_STATS:
        return
    try:
        import curation_store

        curation_store.record_played_ids(played, winner, prompt_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[gah] round stats not recorded: {exc}")


class RoomStore:
    def __init__(self, idle_timeout: int = DEFAULT_IDLE_TIMEOUT):
        self.lock = threading.RLock()
        self.idle_timeout = idle_timeout
        self._games: dict[str, Game] = {}
        self._rng = random.SystemRandom()

    # -- lookup --------------------------------------------------------------
    def get(self, code: str) -> Game | None:
        if not code:
            return None
        with self.lock:
            return self._games.get(code.strip().upper())

    def exists(self, code: str) -> bool:
        return self.get(code) is not None

    def snapshot(self) -> list[Game]:
        with self.lock:
            return list(self._games.values())

    def count(self) -> int:
        with self.lock:
            return len(self._games)

    # -- lifecycle -----------------------------------------------------------
    def create(self, host_pid: str) -> Game:
        with self.lock:
            code = self._fresh_code()
            game = Game(code=code, host_pid=host_pid, on_round_awarded=_record_round)
            self._games[code] = game
            return game

    def _fresh_code(self) -> str:
        for _ in range(200):
            code = "".join(self._rng.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self._games:
                return code
        raise RuntimeError("could not find a free room code")

    def delete(self, code: str) -> None:
        with self.lock:
            self._games.pop(code.strip().upper(), None)

    def reap(self, now: float | None = None) -> list[str]:
        """Delete rooms idle longer than the timeout. Returns the codes dropped."""
        now = now if now is not None else time.time()
        dropped = []
        with self.lock:
            for code, game in list(self._games.items()):
                if now - game.updated_at > self.idle_timeout:
                    del self._games[code]
                    dropped.append(code)
        return dropped


#: The one store the app uses.
rooms = RoomStore()
