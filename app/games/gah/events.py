"""Socket.IO transport for Gifs Against Humanity.

This layer does three things and nothing else: figure out who is talking (from the signed
session cookie, never from the client's own claim), call one engine method under the room
lock, and broadcast fresh per-recipient views. Every rule lives in engine.py.
"""

from __future__ import annotations

import threading

from flask import request
from flask_socketio import emit, join_room, leave_room

from ...extensions import socketio
from ...identity import pid_from_socket
from .engine import ActionError, Game
from .rooms import rooms

#: sid -> {"code", "role": "player"|"watcher"|"tv", "pid"}
SESSIONS: dict[str, dict] = {}
_sessions_lock = threading.RLock()


# --- rooms / addressing --------------------------------------------------------
def room_watchers(code: str) -> str:
    """TVs and not-yet-joined phones. Only ever receives public state."""
    return f"gah:{code}:watch"


def room_player(code: str, pid: str) -> str:
    return f"gah:{code}:p:{pid}"


def broadcast(game: Game) -> None:
    """Send every recipient their own redacted view."""
    with rooms.lock:
        player_views = {pid: game.view_for(pid) for pid in list(game.players)}
        tv_view = game.view_for_tv()
    for pid, view in player_views.items():
        socketio.emit("state", view, to=room_player(game.code, pid))
    socketio.emit("state", tv_view, to=room_watchers(game.code))


def _tv_count(code: str) -> int:
    with _sessions_lock:
        return sum(1 for s in SESSIONS.values() if s["code"] == code and s["role"] == "tv")


def _sids_for(code: str, pid: str, *, exclude: str | None = None) -> list[str]:
    with _sessions_lock:
        return [
            sid
            for sid, s in SESSIONS.items()
            if sid != exclude and s["code"] == code and s.get("pid") == pid and s["role"] == "player"
        ]


# --- shared plumbing ----------------------------------------------------------
def _session() -> dict | None:
    with _sessions_lock:
        return SESSIONS.get(request.sid)


def _act(handler) -> None:
    """Resolve this socket's game, run one engine mutation, then broadcast."""
    session = _session()
    if session is None:
        emit("action_error", {"message": "You're not in a game — try reloading"})
        return
    game = rooms.get(session["code"])
    if game is None:
        emit("room_gone", {"code": session["code"]})
        return
    try:
        with rooms.lock:
            handler(game, session)
    except ActionError as exc:
        emit("action_error", {"message": str(exc)})
    except Exception:  # noqa: BLE001 - never let one bad action kill the room
        emit("action_error", {"message": "Something went wrong with that"})
        raise
    finally:
        broadcast(game)


def _payload(payload) -> dict:
    return payload if isinstance(payload, dict) else {}


# --- connect / join -----------------------------------------------------------
@socketio.on("connect")
def on_connect(auth=None):  # noqa: ARG001
    from .ticker import start_ticker

    start_ticker()


@socketio.on("join_as_player")
def on_join_as_player(payload=None):
    data = _payload(payload)
    code = str(data.get("code", "")).strip().upper()
    nickname = str(data.get("nickname", ""))
    pid = pid_from_socket()
    if not pid:
        emit("action_error", {"message": "Your browser didn't send a session — reload the page"})
        return

    game = rooms.get(code)
    if game is None:
        emit("room_gone", {"code": code})
        return

    try:
        with rooms.lock:
            player = game.add_player(pid, nickname)
    except ActionError as exc:
        emit("join_rejected", {"message": str(exc), "code": code})
        # Still let them watch the room they tried to join.
        _watch(code, role="watcher")
        return

    with _sessions_lock:
        SESSIONS[request.sid] = {"code": code, "role": "player", "pid": pid}
    # A phone starts out as a watcher (before it has a nickname). It must stop being one:
    # otherwise it keeps receiving the spectator view, which has no `you` block, and that
    # view races with — and wins over — its own. A judge whose phone is showing the
    # spectator view has no buttons, and the round can never advance.
    leave_room(room_watchers(code))
    join_room(room_player(code, pid))
    with rooms.lock:
        game.set_tv_count(_tv_count(code))
    emit("joined", {"code": code, "pid": pid, "nickname": player.nickname})
    broadcast(game)


@socketio.on("join_as_watcher")
def on_join_as_watcher(payload=None):
    data = _payload(payload)
    code = str(data.get("code", "")).strip().upper()
    role = "tv" if data.get("tv") else "watcher"
    if rooms.get(code) is None:
        emit("room_gone", {"code": code})
        return
    _watch(code, role=role)


def _watch(code: str, *, role: str) -> None:
    with _sessions_lock:
        previous = SESSIONS.get(request.sid)
        SESSIONS[request.sid] = {"code": code, "role": role, "pid": None}
    # Going the other way (player -> spectator) must also be exclusive.
    if previous and previous.get("pid"):
        leave_room(room_player(previous["code"], previous["pid"]))
    join_room(room_watchers(code))
    game = rooms.get(code)
    if game is None:
        return
    with rooms.lock:
        game.set_tv_count(_tv_count(code))
    broadcast(game)


@socketio.on("leave_game")
def on_leave_game(payload=None):  # noqa: ARG001
    session = _session()
    if session is None:
        return
    code, pid = session["code"], session.get("pid")
    game = rooms.get(code)
    with _sessions_lock:
        SESSIONS.pop(request.sid, None)
    if pid:
        leave_room(room_player(code, pid))
    leave_room(room_watchers(code))
    if game is None:
        return
    with rooms.lock:
        if pid and not _sids_for(code, pid):
            if game.in_progress:
                # Mid-game we keep the seat: pulling a player out from under a running
                # round (they might be the judge) would leave the game inconsistent.
                # The ticker plays their cards until the round ends.
                game.mark_disconnected(pid)
            else:
                game.remove_player(pid)
        game.set_tv_count(_tv_count(code))
    broadcast(game)


@socketio.on("disconnect")
def on_disconnect(reason=None):  # noqa: ARG001
    sid = request.sid
    with _sessions_lock:
        session = SESSIONS.pop(sid, None)
    if session is None:
        return
    code, pid, role = session["code"], session.get("pid"), session["role"]
    game = rooms.get(code)
    if game is None:
        return
    with rooms.lock:
        if role == "player" and pid and not _sids_for(code, pid, exclude=sid):
            # Keeps their seat, hand and score — the ticker plays cards for them.
            game.mark_disconnected(pid)
        game.set_tv_count(_tv_count(code))
    broadcast(game)


# --- lobby actions ------------------------------------------------------------
@socketio.on("set_options")
def on_set_options(payload=None):
    options = _payload(payload).get("options") or {}
    _act(lambda game, s: game.set_options(s["pid"], options))


@socketio.on("kick")
def on_kick(payload=None):
    target = str(_payload(payload).get("pid", ""))
    session = _session()
    if session is None:
        return
    code = session["code"]
    game = rooms.get(code)
    if game is None:
        emit("room_gone", {"code": code})
        return
    try:
        with rooms.lock:
            game.kick(session["pid"], target)
    except ActionError as exc:
        emit("action_error", {"message": str(exc)})
        broadcast(game)
        return
    # Tell them, otherwise their phone sits on a lobby that no longer includes them.
    socketio.emit("kicked", {"code": code}, to=room_player(code, target))
    broadcast(game)


@socketio.on("start_game")
def on_start_game(payload=None):
    options = _payload(payload).get("options") or {}
    _act(lambda game, s: game.start_game(s["pid"], options))


@socketio.on("rematch")
def on_rematch(payload=None):  # noqa: ARG001
    _act(lambda game, s: game.rematch(s["pid"]))


# --- round actions ------------------------------------------------------------
@socketio.on("judge_ready")
def on_judge_ready(payload=None):  # noqa: ARG001
    _act(lambda game, s: game.judge_ready(s["pid"]))


@socketio.on("pick_prompt")
def on_pick_prompt(payload=None):
    prompt_id = str(_payload(payload).get("prompt_id", ""))
    _act(lambda game, s: game.pick_prompt(s["pid"], prompt_id))


@socketio.on("submit_card")
def on_submit_card(payload=None):
    gif_id = str(_payload(payload).get("gif_id", ""))
    _act(lambda game, s: game.submit_card(s["pid"], gif_id))


@socketio.on("flip")
def on_flip(payload=None):
    slot = _payload(payload).get("slot", -1)
    _act(lambda game, s: game.flip(s["pid"], int(slot)))


@socketio.on("pick_winner")
def on_pick_winner(payload=None):
    slot = _payload(payload).get("slot", -1)
    _act(lambda game, s: game.pick_winner(s["pid"], int(slot)))


@socketio.on("next_round")
def on_next_round(payload=None):  # noqa: ARG001
    _act(lambda game, s: game.next_round(s["pid"]))
