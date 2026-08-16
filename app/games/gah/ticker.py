"""One background loop drives every room's clock.

Rather than a timer thread per phase per room, a single task ticks four times a second,
asks each game whether a deadline has passed, and broadcasts only when something actually
changed. It also keeps live rooms alive and reaps dead ones.
"""

from __future__ import annotations

import threading
import time

from ...extensions import socketio
from .rooms import rooms

TICK_SECONDS = 0.25
REAP_EVERY_SECONDS = 30.0

_started = False
_start_lock = threading.Lock()


def start_ticker() -> None:
    """Idempotent — the first socket connection starts the loop."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    socketio.start_background_task(_loop)


def _loop() -> None:
    last_reap = time.time()
    while True:
        socketio.sleep(TICK_SECONDS)
        now = time.time()
        try:
            for game in rooms.snapshot():
                with rooms.lock:
                    # A room with anybody connected is never idle.
                    if game.tv_count or any(p.connected for p in game.players.values()):
                        game.updated_at = now
                    changed = game.tick()
                if changed:
                    from .events import broadcast

                    broadcast(game)

            if now - last_reap >= REAP_EVERY_SECONDS:
                last_reap = now
                for code in rooms.reap(now):
                    socketio.emit("room_gone", {"code": code}, to=f"gah:{code}:watch")
        except Exception as exc:  # noqa: BLE001 - the loop must never die
            print(f"[gah ticker] {type(exc).__name__}: {exc}")
