#!/usr/bin/env python3
"""Fill a game with bots so you can playtest on your own.

    python run.py                                   # terminal 1
    python scripts/playtest_bots.py --bots 3        # terminal 2

It prints a code and the URLs to open. The bots wait in the lobby until enough players
have joined (you, plus anyone else), start the game, and then play their part — judging,
answering and moving rounds along. Ctrl-C to send them home.

    --bots 1 --start-at 2     you + one bot (test mode: the judge answers too)
    --bots 7 --start-at 8     a full table
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulate_game import Bot, describe  # noqa: E402

BOT_NAMES = ["Botsy", "Cluck", "Dingus", "Gremlin", "Noodle", "Pixel", "Rufus"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5050")
    parser.add_argument("--bots", type=int, default=3, help="how many bots to add (1-7)")
    parser.add_argument("--start-at", type=int, default=0, help="total players before starting (default: bots + 1)")
    parser.add_argument("--target", type=int, default=5, help="points to win")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.bots <= 7:
        print("bots must be 1..7")
        return 2
    start_at = args.start_at or args.bots + 1

    host = Bot(args.base, BOT_NAMES[0], verbose=args.verbose)
    code = host.create_game()
    host.connect()
    bots = [host]
    for i in range(1, args.bots):
        bot = Bot(args.base, BOT_NAMES[i], code=code, verbose=args.verbose)
        bot.connect()
        bots.append(bot)

    lan = args.base
    print()
    print(f"  🃏  Game code:  {code}")
    print(f"      Play:  {lan}/{code}")
    print(f"      TV:    {lan}/{code}/tv")
    print(f"      {args.bots} bot(s) waiting — the game starts when {start_at} players are in.")
    print()

    started = False
    try:
        while True:
            time.sleep(0.5)
            state = host.state or {}
            count = len(state.get("players", []))
            if not started and state.get("phase") == "LOBBY" and count >= start_at:
                host.emit(
                    "start_game",
                    {
                        "options": {
                            "target_score": args.target,
                            "test_mode": start_at < 4,
                            "prompt_seconds": 10,
                            "submit_seconds": 90,
                        }
                    },
                )
                started = True
                print(f"  ▶️  starting with {count} players")
            elif not started:
                print(f"\r      waiting… {count}/{start_at} players ", end="", flush=True)
            if state.get("phase") == "GAME_OVER":
                print(f"\n  🏁 {describe(state, bots)}")
                print("      (host bot will sit in GAME_OVER — hit Rematch from a player phone if you're the host)")
                started = False
                time.sleep(4)
    except KeyboardInterrupt:
        print("\n  bots leaving…")
    finally:
        for bot in bots:
            try:
                bot.emit("leave_game")
                bot.sio.disconnect()
            except Exception:  # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
