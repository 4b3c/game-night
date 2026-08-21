"""Socket.IO layer tests: identity, room membership and redaction over the wire.

The engine tests cover the rules; these cover the plumbing that decides *who receives
which view* — the part that broke in a way no engine test could catch.
"""

from __future__ import annotations

import pytest

from app import create_app
from app.extensions import socketio
from app.games.gah.rooms import rooms


@pytest.fixture
def app():
    application = create_app()
    application.config["TESTING"] = True
    yield application
    for game in rooms.snapshot():
        rooms.delete(game.code)


class Client:
    """One browser: its own cookie jar (so its own player id) plus a socket.

    `get_received()` drains the queue, so everything goes through pump(), which keeps
    the latest state and every message seen so far.
    """

    def __init__(self, app, code: str | None = None):
        self.http = app.test_client()
        if code is None:
            response = self.http.post("/new")
            code = response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        else:
            self.http.get(f"/{code}")
        self.code = code
        self.sio = socketio.test_client(app, flask_test_client=self.http)
        assert self.sio.is_connected()
        self.state: dict | None = None
        self.states: list[dict] = []
        self.messages: list[dict] = []

    def pump(self) -> list[dict]:
        fresh = self.sio.get_received()
        self.messages.extend(fresh)
        for message in fresh:
            if message["name"] == "state":
                self.state = message["args"][0]
                self.states.append(message["args"][0])
        return fresh

    def emit(self, event: str, data: dict | None = None) -> None:
        self.sio.emit(event, data or {})

    def join(self, nickname: str) -> None:
        self.emit("join_as_player", {"code": self.code, "nickname": nickname})

    def watch(self, *, tv: bool = False) -> None:
        self.emit("join_as_watcher", {"code": self.code, "tv": tv})

    def named(self, name: str) -> list[dict]:
        return [m["args"][0] for m in self.messages if m["name"] == name]

    def errors(self) -> list[str]:
        return [payload.get("message", "") for payload in self.named("action_error")]

    def disconnect(self) -> None:
        self.sio.disconnect()

    @property
    def you(self) -> dict | None:
        return (self.state or {}).get("you")


def table(app, names=("Abe", "Sam", "Jo", "Kim")) -> list[Client]:
    """A seated lobby. First client is the host."""
    host = Client(app)
    host.join(names[0])
    clients = [host]
    for name in names[1:]:
        client = Client(app, host.code)
        client.join(name)
        clients.append(client)
    for client in clients:
        client.pump()
    return clients


def pump_all(clients) -> None:
    for client in clients:
        client.pump()


def test_a_seated_player_never_receives_the_spectator_view(app):
    """Regression: a phone is a watcher before it has a nickname.

    If it stays in the watchers room after joining, the spectator view (which has no
    `you` block) races with its own view and wins — a judge in that state has no
    buttons, so the round can never advance.
    """
    host = Client(app)
    # Exactly what the browser does: watch first, then join with a nickname.
    host.watch()
    host.pump()
    assert host.state["you"] is None  # legitimate: no nickname yet, so it is a spectator

    host.join("Abe")
    host.states.clear()  # everything from here on must be this player's own view
    host.pump()
    assert host.you is not None and host.you["nickname"] == "Abe"

    # Other people acting triggers broadcasts to every recipient.
    second = Client(app, host.code)
    second.join("Sam")
    host.pump()
    third = Client(app, host.code)
    third.watch(tv=True)
    host.pump()

    assert len(host.states) >= 3, "the host stopped receiving updates"
    assert all(s["you"] is not None for s in host.states), "a spectator view leaked to a seated player"
    assert host.you["nickname"] == "Abe"


def test_each_player_gets_their_own_view_and_nobody_elses_hand(app):
    clients = table(app)
    clients[0].emit("start_game", {"options": {"target_score": 3}})
    pump_all(clients)

    hands = {}
    for client in clients:
        you = client.you
        assert you is not None
        assert len(you["hand"]) == 7
        hands[you["nickname"]] = {card["id"] for card in you["hand"]}

    for client in clients:
        mine = client.you["nickname"]
        blob = repr(client.state)
        for other, cards in hands.items():
            if other == mine:
                continue
            for card in cards:
                assert card not in blob, f"{other}'s card {card} reached {mine}"

    everything = [c for cards in hands.values() for c in cards]
    assert len(everything) == len(set(everything)) == 28


def test_the_code_stops_working_once_the_game_starts(app):
    clients = table(app)
    clients[0].emit("start_game", {})
    pump_all(clients)

    latecomer = Client(app, clients[0].code)
    latecomer.join("Late")
    latecomer.pump()

    rejections = latecomer.named("join_rejected")
    assert rejections, "a late joiner was let into a running game"
    assert "already started" in rejections[0]["message"]

    # They become a spectator, so: public state only, no cards face-up.
    assert latecomer.state is not None
    assert latecomer.state["you"] is None
    assert all("gif" not in card for card in latecomer.state["cards"])


def test_a_connected_tv_is_announced_to_the_room(app):
    host = Client(app)
    host.join("Abe")
    host.pump()
    assert host.state["tv_connected"] is False

    tv = Client(app, host.code)
    tv.watch(tv=True)
    tv.pump()
    host.pump()

    assert host.state["tv_connected"] is True
    assert tv.state["is_tv"] is True and tv.state["you"] is None

    tv.disconnect()
    host.pump()
    assert host.state["tv_connected"] is False


def test_only_the_host_can_kick_and_only_in_the_lobby(app):
    host, other = table(app, names=("Abe", "Sam"))[:2]
    target = [p for p in host.state["players"] if p["nickname"] == "Sam"][0]

    other.emit("kick", {"pid": target["pid"]})
    other.pump()
    assert any("host" in message.lower() for message in other.errors())
    host.pump()
    assert len(host.state["players"]) == 2

    host.emit("kick", {"pid": target["pid"]})
    host.pump()
    assert [p["nickname"] for p in host.state["players"]] == ["Abe"]


def test_actions_from_the_wrong_player_are_refused(app):
    clients = table(app)
    clients[0].emit("start_game", {})
    pump_all(clients)

    game = rooms.get(clients[0].code)
    impostors = [c for c in clients if c.you["pid"] != game.judge_pid]
    assert impostors, "everyone somehow thinks they are the judge"

    impostor = impostors[0]
    impostor.emit("judge_ready")
    impostor.emit("pick_prompt", {"prompt_id": "p001"})
    impostor.pump()
    assert sum("judge" in message.lower() for message in impostor.errors()) >= 2
    assert game.phase == "ROUND_READY"


def test_a_reconnecting_player_keeps_their_seat_hand_and_score(app):
    host = Client(app)
    host.join("Abe")
    returning = Client(app, host.code)
    returning.join("Sam")
    for name in ("Jo", "Kim"):
        Client(app, host.code).join(name)
    host.emit("start_game", {})
    returning.pump()

    before = returning.you
    assert before is not None
    hand_before = [card["id"] for card in before["hand"]]
    pid_before = before["pid"]

    returning.disconnect()
    game = rooms.get(host.code)
    assert game.players[pid_before].connected is False
    assert pid_before in game.players, "a mid-game disconnect must not free the seat"

    # Same cookie jar -> same player id -> same seat, hand and score.
    again = socketio.test_client(app, flask_test_client=returning.http)
    again.emit("join_as_player", {"code": host.code, "nickname": "Sam"})
    states = [m["args"][0] for m in again.get_received() if m["name"] == "state"]
    assert states
    you = states[-1]["you"]
    assert you["pid"] == pid_before
    assert [card["id"] for card in you["hand"]] == hand_before
    assert game.players[pid_before].connected is True


def test_a_lobby_disconnect_frees_the_seat_and_the_nickname(app):
    host = Client(app)
    host.join("Abe")
    quitter = Client(app, host.code)
    quitter.join("Sam")
    host.pump()
    assert len(host.state["players"]) == 2

    quitter.disconnect()
    host.pump()
    assert [p["nickname"] for p in host.state["players"]] == ["Abe"]

    # The freed nickname can be taken by someone new.
    newcomer = Client(app, host.code)
    newcomer.join("Sam")
    newcomer.pump()
    assert newcomer.you is not None and newcomer.you["nickname"] == "Sam"


def test_a_kicked_player_is_told(app):
    """Otherwise their phone sits on a lobby it is no longer part of."""
    host = Client(app)
    host.join("Abe")
    victim = Client(app, host.code)
    victim.join("Sam")
    host.pump()
    victim.pump()

    target = [p for p in host.state["players"] if p["nickname"] == "Sam"][0]
    host.emit("kick", {"pid": target["pid"]})
    victim.pump()

    assert victim.named("kicked"), "the kicked player was never told"


def test_leaving_mid_game_keeps_the_seat_but_leaving_the_lobby_does_not(app):
    clients = table(app)
    clients[0].emit("start_game", {})
    pump_all(clients)
    game = rooms.get(clients[0].code)

    quitter = clients[-1]
    pid = quitter.you["pid"]
    quitter.emit("leave_game")

    # A running round may depend on this player (they might be the judge), so the seat
    # stays and the ticker plays for them.
    assert pid in game.players
    assert game.players[pid].connected is False
    assert len(game.players) == 4


def test_the_host_role_moves_on_if_the_host_leaves_the_lobby(app):
    host = Client(app)
    host.join("Abe")
    second = Client(app, host.code)
    second.join("Sam")
    second.pump()
    assert second.state["host_pid"] != second.you["pid"]

    host.disconnect()
    second.pump()
    assert second.state["host_pid"] == second.you["pid"]
    assert second.you["is_host"] is True


# --- the stats gate ----------------------------------------------------------
def test_rounds_are_only_recorded_when_the_deployment_says_so(monkeypatch):
    """`GN_RECORD_STATS` is what makes the deployed server the only game that counts.

    Off by default, so a laptop, the bot scripts and this test suite all leave the deck's
    record of how it plays alone.
    """
    from app.games.gah import rooms as room_store
    import curation_store

    calls = []
    monkeypatch.setattr(curation_store, "record_played_ids", lambda *args: calls.append(args))

    monkeypatch.setattr(room_store, "RECORD_STATS", False)
    room_store._record_round(["gif-a"], "gif-a", "p001")
    assert calls == []

    monkeypatch.setattr(room_store, "RECORD_STATS", True)
    room_store._record_round(["gif-a"], "gif-a", "p001")
    assert calls == [(["gif-a"], "gif-a", "p001")]
