#!/usr/bin/env python3
"""Play a whole game of Gifs Against Humanity with bots.

Exercises the real transport — cookies, Socket.IO, the ticker, redaction — not just the
engine. Start the server first, then:

    python run.py                       # terminal 1
    python scripts/simulate_game.py     # terminal 2

    python scripts/simulate_game.py --players 6 --target 3 --verbose

Exits non-zero if the game doesn't finish with exactly one champion.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import random
import re
import sys
import threading
import time
import urllib.request

import socketio

NAMES = ["Abe", "Sam", "Jo", "Kim", "Lee", "Max", "Nia", "Oz"]


class Bot:
    """One phone. Reacts to state; never assumes what it wasn't told."""

    def __init__(self, base: str, name: str, *, code: str | None = None, verbose: bool = False):
        self.base = base.rstrip("/")
        self.name = name
        self.code = code
        self.verbose = verbose
        self.pid = None
        self.state = None
        self.done = threading.Event()
        self.error: str | None = None
        self.jar = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.sio = socketio.Client(reconnection=True, logger=False, engineio_logger=False)
        self._wire()

    # -- http ---------------------------------------------------------------
    def create_game(self) -> str:
        """POST /gah/new and read the code out of the redirect."""
        request = urllib.request.Request(f"{self.base}/gah/new", data=b"", method="POST")
        with self.http.open(request) as response:
            match = re.search(r"/gah/([A-Z]{4})", response.geturl())
            if not match:
                raise RuntimeError(f"could not find a game code in {response.geturl()}")
            self.code = match.group(1)
        return self.code

    def load_page(self) -> None:
        """Fetch the play page so the session cookie exists before the socket opens."""
        with self.http.open(f"{self.base}/gah/{self.code}") as response:
            response.read()

    def cookie_header(self) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in self.jar)

    # -- socket -------------------------------------------------------------
    def connect(self) -> None:
        self.load_page()
        self.sio.connect(
            self.base,
            headers={"Cookie": self.cookie_header()},
            transports=["websocket"],
            wait_timeout=10,
        )
        # The "connect" handler does the joining.

    def emit(self, event: str, data: dict | None = None) -> None:
        """Emit, tolerating a reconnect in flight.

        Never block or sleep in a Socket.IO handler: it stalls this client's message
        pump, which eventually looks like a dead connection to the server.
        """
        try:
            self.sio.emit(event, data or {})
        except socketio.exceptions.SocketIOError as exc:
            self.log("emit failed:", type(exc).__name__)

    def log(self, *parts) -> None:
        if self.verbose:
            print(f"  [{self.name}]", *parts)

    def _wire(self) -> None:
        @self.sio.on("connect")
        def _connect():
            # Re-announce on every connect, including reconnects — same as the browser
            # client does. Without this a reconnected socket sits in no room and stops
            # receiving state.
            self.emit("join_as_player", {"code": self.code, "nickname": self.name})

        @self.sio.on("joined")
        def _joined(info):
            self.pid = info["pid"]
            self.log("joined as", info["nickname"])

        @self.sio.on("join_rejected")
        def _rejected(info):
            self.error = f"join rejected: {info.get('message')}"
            self.done.set()

        @self.sio.on("action_error")
        def _action_error(info):
            # Losing a race (two bots acting on the same state) is normal; the server
            # just says no. A real error shows up as a stalled game instead.
            self.log("refused:", info.get("message"))

        @self.sio.on("room_gone")
        def _gone(info):
            self.error = "room disappeared"
            self.done.set()

        @self.sio.on("state")
        def _state(state):
            self.state = state
            self.react(state)

    # -- behaviour ----------------------------------------------------------
    def react(self, state: dict) -> None:
        you = state.get("you")
        if not you:
            return
        phase = state["phase"]

        if phase == "GAME_OVER":
            self.done.set()
            return
        if not self.sio.connected or self.done.is_set():
            return  # a state packet that landed while we were shutting down

        if you["is_judge"]:
            if phase == "ROUND_READY":
                self.emit("judge_ready")
            elif phase == "PROMPT_PICK":
                choices = state.get("prompt_choices") or []
                if choices:
                    self.emit("pick_prompt", {"prompt_id": random.choice(choices)["id"]})
            elif phase == "REVEAL":
                hidden = [c["slot"] for c in state["cards"] if not c["revealed"]]
                if hidden:
                    self.emit("flip", {"slot": hidden[0]})
            elif phase == "PICK_WINNER":
                self.emit("pick_winner", {"slot": random.randrange(len(state["cards"]))})
            elif phase == "ROUND_RESULT":
                self.emit("next_round")
        else:
            if phase == "SUBMIT" and not you["submitted_gif"] and you["hand"]:
                self.emit("submit_card", {"gif_id": random.choice(you["hand"])["id"]})

    def check_redaction(self) -> list[str]:
        """A bot should never receive anything it isn't entitled to."""
        problems = []
        state = self.state or {}
        you = state.get("you") or {}
        if state.get("phase") in ("SUBMIT",) and state.get("cards"):
            problems.append("cards were visible during SUBMIT")
        for card in state.get("cards") or []:
            if not card.get("revealed") and ("gif" in card or "author" in card):
                problems.append(f"hidden card {card.get('slot')} carried data")
        if not you.get("is_judge") and "prompt_choices" in state:
            problems.append("a non-judge received the judge's prompt choices")
        return problems


def describe(state: dict | None, bots: list[Bot]) -> str:
    """Why is the game sitting there? Used for stall reports."""
    if not state:
        return "no state received at all"
    bits = [f"phase={state['phase']}", f"round={state['round']}"]
    judge = [p for p in state["players"] if p["is_judge"]]
    if judge:
        bits.append(f"judge={judge[0]['nickname']}{'' if judge[0]['connected'] else ' (away)'}")
    if state["phase"] == "SUBMIT":
        bits.append(f"submitted={state['submitted_count']}/{state['expected_count']}")
        waiting = ",".join(w["nickname"] + ("" if w["connected"] else "!away") for w in state["waiting_on"])
        bits.append(f"waiting_on={waiting or '-'}")
        left = state.get("deadline_ts")
        if left:
            bits.append(f"deadline_in={left - state['server_now']:.1f}s")
        else:
            bits.append("deadline=none")
    if state["phase"] in ("REVEAL", "PICK_WINNER"):
        flipped = sum(1 for c in state["cards"] if c["revealed"])
        bits.append(f"flipped={flipped}/{len(state['cards'])}")
    dead = [b.name for b in bots if not b.sio.connected]
    if dead:
        bits.append(f"bots_disconnected={','.join(dead)}")
    return " ".join(bits)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5050")
    parser.add_argument("--players", type=int, default=4)
    parser.add_argument("--target", type=int, default=3)
    # 8 bots in one Python process are much slower than 8 real phones — every state
    # broadcast is parsed 8 times on one interpreter — so scale the budget with the
    # bot count rather than assuming a stall.
    parser.add_argument("--timeout", type=float, default=0.0, help="seconds (default: 40s per player)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not 2 <= args.players <= 8:
        print("players must be 2..8")
        return 2
    if not args.timeout:
        args.timeout = 40.0 * args.players

    host = Bot(args.base, NAMES[0], verbose=args.verbose)
    code = host.create_game()
    print(f"game {code} — {args.players} bots, first to {args.target}")
    host.connect()

    others = [Bot(args.base, NAMES[i], code=code, verbose=args.verbose) for i in range(1, args.players)]
    for bot in others:
        bot.connect()
    bots = [host] + others

    # Wait for everyone to land in the lobby, then start.
    deadline = time.time() + 15
    while time.time() < deadline:
        if host.state and len(host.state.get("players", [])) == args.players:
            break
        time.sleep(0.1)
    else:
        print("bots never all showed up in the lobby")
        return 1

    host.emit(
        "start_game",
        {
            "options": {
                "target_score": args.target,
                "test_mode": args.players < 4,
                "prompt_seconds": 10,
                "submit_seconds": 30,
            }
        },
    )

    end = time.time() + args.timeout
    last_report = 0.0
    while time.time() < end:
        if all(bot.done.is_set() for bot in bots):
            break
        failed = [bot for bot in bots if bot.error]
        if failed:
            print("bot error:", failed[0].error)
            for bot in bots:
                bot.sio.disconnect()
            return 1
        if args.verbose and time.time() - last_report > 5:
            last_report = time.time()
            print("  ...", describe(host.state, bots))
        time.sleep(0.2)
    else:
        print(f"game never finished after {args.timeout}s: {describe(host.state, bots)}")
        for bot in bots:
            bot.sio.disconnect()
        return 1

    final = host.state
    champion = final.get("champion_pid")
    winners = [p for p in final["players"] if p["pid"] == champion]
    scores = " · ".join(f"{p['avatar']} {p['nickname']} {p['score']}" for p in final["players"])

    problems = []
    for bot in bots:
        problems += [f"{bot.name}: {p}" for p in bot.check_redaction()]

    for bot in bots:
        bot.sio.disconnect()

    print(f"rounds played: {final['round']}")
    print(f"final: {scores}")
    if len(winners) != 1:
        print("no single champion — something is wrong")
        return 1
    if winners[0]["score"] < args.target:
        print("champion is below the target score")
        return 1
    if problems:
        print("REDACTION PROBLEMS:")
        for problem in problems:
            print("  -", problem)
        return 1

    print(f"🏆 {winners[0]['nickname']} wins — game completed cleanly, no leaks seen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
