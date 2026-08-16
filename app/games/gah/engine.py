"""Gifs Against Humanity -- the game itself.

Deliberately free of Flask and Socket.IO imports: this is a state machine you hand an
action to, and it either mutates state or raises ActionError. That makes the whole game
unit-testable without a browser, and keeps every rule (including *who is allowed to see
what*) in one place instead of scattered through templates.

Phases:

    LOBBY -> ROUND_READY -> PROMPT_PICK -> SUBMIT -> REVEAL -> PICK_WINNER
          -> ROUND_RESULT -> ROUND_READY ...  (or GAME_OVER)
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable

from .decks import (
    DEFAULT_MODE,
    MODES,
    Deck,
    build_gif_deck,
    build_prompt_deck,
    gif_index,
    mode_counts,
    prompt_index,
)

# --- tuning knobs -------------------------------------------------------------
HAND_SIZE = 7
PROMPT_CHOICES = 3
MIN_PLAYERS = 4
TEST_MIN_PLAYERS = 2
MAX_PLAYERS = 8

DEFAULT_TARGET_SCORE = 5
TARGET_SCORE_RANGE = (3, 10)
DEFAULT_PROMPT_SECONDS = 10
DEFAULT_SUBMIT_SECONDS = 90

# A disconnected judge must not be able to stall the game forever. A *present* judge is
# never rushed -- these only apply while the judge is away.
JUDGE_AWAY_GRACE = 20.0
# A disconnected player's card gets played for them this long after they vanish, even if
# the submit timer is longer (or off).
PLAYER_AWAY_GRACE = 30.0

NICKNAME_MIN = 2
NICKNAME_MAX = 12

AVATARS = [
    "🦊", "🐸", "🦑", "🐙", "🦅", "🐝", "🦄", "🐼",
    "🦖", "🐧", "🦩", "🐳", "🦉", "🐢", "🦔", "🐻",
]
AVATAR_COLORS = ["av-1", "av-2", "av-3", "av-4", "av-5", "av-6", "av-7", "av-8"]


class Phase:
    LOBBY = "LOBBY"
    ROUND_READY = "ROUND_READY"
    PROMPT_PICK = "PROMPT_PICK"
    SUBMIT = "SUBMIT"
    REVEAL = "REVEAL"
    PICK_WINNER = "PICK_WINNER"
    ROUND_RESULT = "ROUND_RESULT"
    GAME_OVER = "GAME_OVER"


#: Phases that only advance when the judge taps something (or is away past the grace).
JUDGE_GATED = {Phase.ROUND_READY, Phase.REVEAL, Phase.PICK_WINNER, Phase.ROUND_RESULT}


class ActionError(Exception):
    """A rejected action. The message is safe to show the player."""


# --- options ------------------------------------------------------------------
@dataclass
class Options:
    # The one setting most tables will touch: which set of GIFs is in play.
    mode: str = DEFAULT_MODE  # see decks.MODES
    judge_rotation: str = "circle"  # "circle" | "last_winner"
    target_score: int = DEFAULT_TARGET_SCORE
    prompt_seconds: int = DEFAULT_PROMPT_SECONDS  # 0 = no timer
    submit_seconds: int = DEFAULT_SUBMIT_SECONDS  # 0 = no timer
    test_mode: bool = False  # allow starting with 2 players

    @property
    def min_players(self) -> int:
        return TEST_MIN_PLAYERS if self.test_mode else MIN_PLAYERS

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "judge_rotation": self.judge_rotation,
            "target_score": self.target_score,
            "prompt_seconds": self.prompt_seconds,
            "submit_seconds": self.submit_seconds,
            "test_mode": self.test_mode,
            "min_players": self.min_players,
        }

    def update(self, raw: dict) -> None:
        """Apply a partial dict of untrusted input, clamping everything."""
        if "mode" in raw:
            value = str(raw["mode"])
            if value not in MODES:
                raise ActionError("Unknown game mode")
            self.mode = value
        if "judge_rotation" in raw:
            value = str(raw["judge_rotation"])
            if value not in ("circle", "last_winner"):
                raise ActionError("Unknown judge rotation")
            self.judge_rotation = value
        if "target_score" in raw:
            lo, hi = TARGET_SCORE_RANGE
            self.target_score = max(lo, min(hi, _as_int(raw["target_score"], DEFAULT_TARGET_SCORE)))
        if "prompt_seconds" in raw:
            self.prompt_seconds = _clamp_timer(raw["prompt_seconds"], DEFAULT_PROMPT_SECONDS, 5, 60)
        if "submit_seconds" in raw:
            self.submit_seconds = _clamp_timer(raw["submit_seconds"], DEFAULT_SUBMIT_SECONDS, 15, 300)
        if "test_mode" in raw:
            self.test_mode = bool(raw["test_mode"])


def _as_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _clamp_timer(value, fallback: int, lo: int, hi: int) -> int:
    seconds = _as_int(value, fallback)
    if seconds <= 0:
        return 0  # timer off
    return max(lo, min(hi, seconds))


# --- players and submissions --------------------------------------------------
@dataclass
class Player:
    pid: str
    nickname: str
    avatar: str
    color: str
    seat: int
    joined_at: float
    score: int = 0
    hand: list[str] = field(default_factory=list)
    connected: bool = True
    away_since: float | None = None


@dataclass
class Submission:
    pid: str
    gif_id: str
    revealed: bool = False
    auto: bool = False  # played by the timer rather than chosen


# --- the game -----------------------------------------------------------------
class Game:
    def __init__(
        self,
        code: str,
        host_pid: str,
        *,
        rng: random.Random | None = None,
        now: Callable[[], float] = time.time,
    ):
        self.code = code
        self.host_pid = host_pid
        self.options = Options()
        self._rng = rng or random.Random()
        self._now = now

        self.version = 0
        self.created_at = self._now()
        self.updated_at = self.created_at

        self.players: dict[str, Player] = {}
        self.seat_order: list[str] = []
        self.phase = Phase.LOBBY

        self.round_number = 0
        self.judge_pid: str | None = None
        self.prompt_choice_ids: list[str] = []
        self.prompt_id: str | None = None
        self.submissions: list[Submission] = []
        self.round_winner_pid: str | None = None
        self.round_winner_slot: int | None = None
        self.last_winner_pid: str | None = None
        self.champion_pid: str | None = None
        self.deadline: float | None = None
        self.tv_count = 0

        self._gif_deck: Deck | None = None
        self._prompt_deck: Deck | None = None

    # -- small helpers --------------------------------------------------------
    def _touch(self) -> None:
        self.version += 1
        self.updated_at = self._now()

    def _player(self, pid: str) -> Player:
        player = self.players.get(pid)
        if player is None:
            raise ActionError("You're not in this game")
        return player

    def _require_host(self, pid: str) -> Player:
        if pid != self.host_pid:
            raise ActionError("Only the host can do that")
        return self._player(pid)

    def _require_judge(self, pid: str) -> Player:
        if pid != self.judge_pid:
            raise ActionError("Only the judge can do that")
        return self._player(pid)

    def _require_phase(self, *phases: str) -> None:
        if self.phase not in phases:
            raise ActionError("Too late for that")

    def _ordered_players(self) -> list[Player]:
        return [self.players[pid] for pid in self.seat_order if pid in self.players]

    @property
    def in_progress(self) -> bool:
        return self.phase not in (Phase.LOBBY, Phase.GAME_OVER)

    @property
    def judge(self) -> Player | None:
        return self.players.get(self.judge_pid) if self.judge_pid else None

    def _set_deadline(self, seconds: int | None) -> None:
        self.deadline = self._now() + seconds if seconds else None

    def _non_judge_players(self) -> list[Player]:
        return [p for p in self._ordered_players() if p.pid != self.judge_pid]

    # -- lobby ---------------------------------------------------------------
    def add_player(self, pid: str, nickname: str) -> Player:
        existing = self.players.get(pid)
        if existing is not None:
            self.mark_connected(pid)
            return existing

        if self.in_progress:
            raise ActionError("That game has already started")
        if self.phase == Phase.GAME_OVER:
            raise ActionError("That game has finished")
        if len(self.players) >= MAX_PLAYERS:
            raise ActionError(f"That game is full ({MAX_PLAYERS} players)")

        name = clean_nickname(nickname)
        if any(p.nickname.casefold() == name.casefold() for p in self.players.values()):
            raise ActionError("Someone already took that name")

        used_avatars = {p.avatar for p in self.players.values()}
        avatar = next((a for a in AVATARS if a not in used_avatars), self._rng.choice(AVATARS))
        used_colors = {p.color for p in self.players.values()}
        color = next((c for c in AVATAR_COLORS if c not in used_colors), self._rng.choice(AVATAR_COLORS))

        player = Player(
            pid=pid,
            nickname=name,
            avatar=avatar,
            color=color,
            seat=len(self.seat_order),
            joined_at=self._now(),
        )
        self.players[pid] = player
        self.seat_order.append(pid)
        self._touch()
        return player

    def kick(self, actor_pid: str, target_pid: str) -> None:
        self._require_host(actor_pid)
        if self.phase != Phase.LOBBY:
            raise ActionError("You can only remove players in the lobby")
        if target_pid == actor_pid:
            raise ActionError("You can't kick yourself")
        self.remove_player(target_pid)

    def remove_player(self, pid: str) -> None:
        if pid not in self.players:
            return
        del self.players[pid]
        self.seat_order = [p for p in self.seat_order if p in self.players]
        for seat, seat_pid in enumerate(self.seat_order):
            self.players[seat_pid].seat = seat
        if pid == self.host_pid:
            self._transfer_host()
        self._touch()

    def _transfer_host(self) -> None:
        """Hand the host role to the longest-present connected player."""
        candidates = sorted(self.players.values(), key=lambda p: (not p.connected, p.joined_at))
        self.host_pid = candidates[0].pid if candidates else self.host_pid

    def set_options(self, actor_pid: str, raw: dict) -> None:
        self._require_host(actor_pid)
        self._require_phase(Phase.LOBBY)
        self.options.update(raw or {})
        self._touch()

    def start_game(self, actor_pid: str, options: dict | None = None) -> None:
        self._require_host(actor_pid)
        self._require_phase(Phase.LOBBY)
        if options:
            self.options.update(options)
        if len(self.players) < self.options.min_players:
            raise ActionError(f"Need at least {self.options.min_players} players")

        # Deal from one shared deck, so a GIF can only ever be in one place. Check the
        # deck is big enough *before* touching anyone's hand: running dry halfway would
        # otherwise leave a half-dealt game. This matters when you swap the filler GIFs
        # for your own — eight players need 56 distinct cards.
        gif_deck = build_gif_deck(self._rng, self.options.mode)
        needed = len(self.players) * HAND_SIZE
        if len(gif_deck) < needed:
            mode_label = MODES[self.options.mode]["label"]
            raise ActionError(
                f"{mode_label} mode only has {len(gif_deck)} GIFs — {len(self.players)} "
                f"players need {needed}. Curate more with scripts/curate_gifs.py, or pick "
                f"another mode."
            )
        hands = {pid: gif_deck.draw_many(HAND_SIZE) for pid in self.players}

        self._rng.shuffle(self.seat_order)
        for seat, pid in enumerate(self.seat_order):
            self.players[pid].seat = seat

        self._gif_deck = gif_deck
        self._prompt_deck = build_prompt_deck(self._rng)
        for pid, player in self.players.items():
            player.score = 0
            player.hand = hands[pid]

        self.round_number = 0
        self.champion_pid = None
        self.last_winner_pid = None
        self.judge_pid = self._rng.choice(list(self.players))
        self._begin_round()

    def rematch(self, actor_pid: str) -> None:
        self._require_host(actor_pid)
        self._require_phase(Phase.GAME_OVER)
        self.phase = Phase.LOBBY
        self.round_number = 0
        self.judge_pid = None
        self.prompt_id = None
        self.prompt_choice_ids = []
        self.submissions = []
        self.round_winner_pid = None
        self.round_winner_slot = None
        self.last_winner_pid = None
        self.champion_pid = None
        self.deadline = None
        for player in self.players.values():
            player.score = 0
            player.hand = []
        self._touch()

    # -- round flow ----------------------------------------------------------
    def _begin_round(self) -> None:
        self.round_number += 1
        self.prompt_id = None
        self.prompt_choice_ids = []
        self.submissions = []
        self.round_winner_pid = None
        self.round_winner_slot = None
        self.phase = Phase.ROUND_READY
        self.deadline = None  # the judge's "Ready" button gates this, not a clock
        self._touch()

    def judge_ready(self, actor_pid: str) -> None:
        self._require_judge(actor_pid)
        self._require_phase(Phase.ROUND_READY)
        self._deal_prompt_choices()

    def _deal_prompt_choices(self) -> None:
        assert self._prompt_deck is not None
        self.prompt_choice_ids = self._prompt_deck.draw_many(PROMPT_CHOICES)
        self.phase = Phase.PROMPT_PICK
        self._set_deadline(self.options.prompt_seconds)
        self._touch()

    def pick_prompt(self, actor_pid: str, prompt_id: str) -> None:
        self._require_judge(actor_pid)
        self._require_phase(Phase.PROMPT_PICK)
        if prompt_id not in self.prompt_choice_ids:
            raise ActionError("That prompt isn't on the table")
        self._commit_prompt(prompt_id)

    def _commit_prompt(self, prompt_id: str) -> None:
        assert self._prompt_deck is not None
        self.prompt_id = prompt_id
        self._prompt_deck.discard(*[p for p in self.prompt_choice_ids if p != prompt_id])
        self.prompt_choice_ids = []
        self.phase = Phase.SUBMIT
        self._set_deadline(self.options.submit_seconds)
        self._touch()

    def submit_card(self, pid: str, gif_id: str) -> None:
        player = self._player(pid)
        self._require_phase(Phase.SUBMIT)
        if pid == self.judge_pid:
            raise ActionError("You're the judge this round")
        if any(s.pid == pid for s in self.submissions):
            raise ActionError("You already played a card")
        if gif_id not in player.hand:
            raise ActionError("That card isn't in your hand")
        self._play_card(player, gif_id, auto=False)
        self._maybe_close_submissions()

    def _play_card(self, player: Player, gif_id: str, *, auto: bool) -> None:
        player.hand.remove(gif_id)
        self.submissions.append(Submission(pid=player.pid, gif_id=gif_id, auto=auto))
        # Refill immediately so their hand is full before the next round.
        assert self._gif_deck is not None
        player.hand.append(self._gif_deck.draw())
        self._touch()

    def _maybe_close_submissions(self) -> bool:
        if self.phase != Phase.SUBMIT:
            return False
        if len(self.submissions) < len(self._non_judge_players()):
            return False
        self._start_reveal()
        return True

    def _start_reveal(self) -> None:
        # Shuffle now, so slot order says nothing about who submitted when.
        self._rng.shuffle(self.submissions)
        self.phase = Phase.REVEAL
        self.deadline = None  # the judge sets the pace here
        self._touch()

    def flip(self, actor_pid: str, slot: int) -> None:
        self._require_judge(actor_pid)
        self._require_phase(Phase.REVEAL)
        if not 0 <= slot < len(self.submissions):
            raise ActionError("No card there")
        submission = self.submissions[slot]
        if submission.revealed:
            raise ActionError("Already flipped")
        submission.revealed = True
        if all(s.revealed for s in self.submissions):
            self.phase = Phase.PICK_WINNER
        self._touch()

    def _reveal_all(self) -> None:
        for submission in self.submissions:
            submission.revealed = True
        self.phase = Phase.PICK_WINNER
        self._touch()

    def pick_winner(self, actor_pid: str, slot: int) -> None:
        self._require_judge(actor_pid)
        self._require_phase(Phase.PICK_WINNER)
        if not 0 <= slot < len(self.submissions):
            raise ActionError("No card there")
        self._award(slot)

    def _award(self, slot: int) -> None:
        assert self._gif_deck is not None
        submission = self.submissions[slot]
        winner = self.players.get(submission.pid)
        self.round_winner_slot = slot
        self.round_winner_pid = submission.pid
        self.last_winner_pid = submission.pid
        if winner is not None:
            winner.score += 1

        # Played cards go to the discard pile so a long night can recycle them.
        self._gif_deck.discard(*[s.gif_id for s in self.submissions])

        if winner is not None and winner.score >= self.options.target_score:
            self.champion_pid = winner.pid
            self.phase = Phase.GAME_OVER
        else:
            self.phase = Phase.ROUND_RESULT
        self.deadline = None
        self._touch()

    def next_round(self, actor_pid: str) -> None:
        self._require_judge(actor_pid)
        self._require_phase(Phase.ROUND_RESULT)
        self._advance_judge()
        self._begin_round()

    def _advance_judge(self) -> None:
        if self.options.judge_rotation == "last_winner" and self.last_winner_pid in self.players:
            self.judge_pid = self.last_winner_pid
            return
        order = self.seat_order
        if not order:
            return
        try:
            index = order.index(self.judge_pid)
        except ValueError:
            index = -1
        self.judge_pid = order[(index + 1) % len(order)]

    # -- presence ------------------------------------------------------------
    def mark_connected(self, pid: str) -> bool:
        player = self.players.get(pid)
        if player is None or player.connected:
            return False
        player.connected = True
        player.away_since = None
        self._touch()
        return True

    def mark_disconnected(self, pid: str) -> bool:
        player = self.players.get(pid)
        if player is None or not player.connected:
            return False
        player.connected = False
        player.away_since = self._now()
        # In the lobby there's no game to protect, so drop them from the seat list.
        if self.phase == Phase.LOBBY:
            self.remove_player(pid)
            return True
        self._touch()
        return True

    def set_tv_count(self, count: int) -> bool:
        count = max(0, int(count))
        if count == self.tv_count:
            return False
        self.tv_count = count
        self._touch()
        return True

    # -- the clock -----------------------------------------------------------
    def tick(self) -> bool:
        """Enforce deadlines and away-graces. Returns True if anything changed."""
        now = self._now()
        changed = False

        if self.phase == Phase.PROMPT_PICK:
            if self.deadline is not None and now >= self.deadline:
                self._commit_prompt(self._rng.choice(self.prompt_choice_ids))
                changed = True
            elif self._judge_away_past_grace(now):
                self._commit_prompt(self._rng.choice(self.prompt_choice_ids))
                changed = True

        elif self.phase == Phase.SUBMIT:
            timed_out = self.deadline is not None and now >= self.deadline
            pending = [p for p in self._non_judge_players() if not any(s.pid == p.pid for s in self.submissions)]
            for player in pending:
                away_too_long = (
                    not player.connected
                    and player.away_since is not None
                    and now - player.away_since >= PLAYER_AWAY_GRACE
                )
                if (timed_out or away_too_long) and player.hand:
                    self._play_card(player, self._rng.choice(player.hand), auto=True)
                    changed = True
            if self._maybe_close_submissions():
                changed = True

        elif self.phase in JUDGE_GATED and self._judge_away_past_grace(now):
            if self.phase == Phase.ROUND_READY:
                self._deal_prompt_choices()
            elif self.phase == Phase.REVEAL:
                self._reveal_all()
            elif self.phase == Phase.PICK_WINNER:
                self._award(self._rng.randrange(len(self.submissions)))
            elif self.phase == Phase.ROUND_RESULT:
                self._advance_judge()
                self._begin_round()
            changed = True

        return changed

    def _judge_away_past_grace(self, now: float) -> bool:
        judge = self.judge
        if judge is None:
            return False
        if judge.connected or judge.away_since is None:
            return False
        return now - judge.away_since >= JUDGE_AWAY_GRACE

    # -- invariants ----------------------------------------------------------
    def card_locations(self) -> dict[str, list[str]]:
        """Every GIF in the game and where it currently is.

        There is exactly one deck and a card is only ever in one place: a hand, this
        round's submissions, the draw pile or the discard pile. Drawing pops; playing
        moves a card out of a hand into `submissions`; only after the round is awarded do
        those go to the discard. That's what makes it impossible for two players to hold
        the same GIF — tests assert it round after round, including across reshuffles.
        """
        # Once a round is awarded its cards go to the discard pile, but `submissions`
        # keeps listing them so the winning card can still be shown. Counting both would
        # double-count the same physical cards, so only count submissions still in flight.
        in_flight = self.phase in (Phase.SUBMIT, Phase.REVEAL, Phase.PICK_WINNER)
        return {
            "hands": [card for player in self.players.values() for card in player.hand],
            "submissions": [s.gif_id for s in self.submissions] if in_flight else [],
            "draw": list(self._gif_deck.draw_pile) if self._gif_deck else [],
            "discard": list(self._gif_deck.discard_pile) if self._gif_deck else [],
        }

    # -- views ---------------------------------------------------------------
    def _player_view(self, player: Player) -> dict:
        return {
            "pid": player.pid,
            "nickname": player.nickname,
            "avatar": player.avatar,
            "color": player.color,
            "score": player.score,
            "connected": player.connected,
            "is_host": player.pid == self.host_pid,
            "is_judge": player.pid == self.judge_pid,
            "has_submitted": any(s.pid == player.pid for s in self.submissions),
        }

    def _card_views(self) -> list[dict]:
        """Submissions as the room may see them.

        Nothing about a hidden card is sent -- no gif id, no author -- so there is
        nothing to dig out of devtools. Authorship is only ever attached to the winning
        card, once the judge has crowned it.
        """
        if self.phase not in (Phase.REVEAL, Phase.PICK_WINNER, Phase.ROUND_RESULT, Phase.GAME_OVER):
            return []
        gifs = gif_index()
        cards = []
        for slot, submission in enumerate(self.submissions):
            card: dict = {"slot": slot, "revealed": submission.revealed}
            if submission.revealed:
                gif = gifs.get(submission.gif_id, {"id": submission.gif_id, "file": "", "label": ""})
                card["gif"] = gif
            if slot == self.round_winner_slot:
                card["is_winner"] = True
                card["author"] = self._player_view(self.players[submission.pid]) if submission.pid in self.players else None
            cards.append(card)
        return cards

    def _prompt_view(self) -> dict | None:
        if not self.prompt_id:
            return None
        return prompt_index().get(self.prompt_id)

    def public_state(self) -> dict:
        prompts = prompt_index()
        counts = mode_counts()
        waiting_on = [
            {"nickname": p.nickname, "avatar": p.avatar, "connected": p.connected}
            for p in self._non_judge_players()
            if not any(s.pid == p.pid for s in self.submissions)
        ] if self.phase == Phase.SUBMIT else []

        return {
            "code": self.code,
            "v": self.version,
            "phase": self.phase,
            "round": self.round_number,
            "options": self.options.to_dict(),
            "players": [self._player_view(p) for p in self._ordered_players()],
            "host_pid": self.host_pid,
            "judge_pid": self.judge_pid,
            "prompt": self._prompt_view(),
            "cards": self._card_views(),
            "submitted_count": len(self.submissions),
            "expected_count": len(self._non_judge_players()),
            "waiting_on": waiting_on,
            "round_winner_pid": self.round_winner_pid,
            "round_winner_slot": self.round_winner_slot,
            "champion_pid": self.champion_pid,
            "deadline_ts": self.deadline,
            "server_now": self._now(),
            "tv_connected": self.tv_count > 0,
            "min_players": self.options.min_players,
            "max_players": MAX_PLAYERS,
            "modes": [
                {
                    "id": name,
                    "label": meta["label"],
                    "emoji": meta["emoji"],
                    "cards": counts.get(name, 0),
                    "enough": counts.get(name, 0) >= max(len(self.players), MIN_PLAYERS) * HAND_SIZE,
                }
                for name, meta in MODES.items()
            ],
            "can_start": len(self.players) >= self.options.min_players,
            "prompt_count": len(prompts),
        }

    def view_for(self, pid: str) -> dict:
        """The state as one player may see it: public state plus their own hand."""
        state = self.public_state()
        player = self.players.get(pid)
        if player is None:
            state["you"] = None
            return state

        gifs = gif_index()
        submitted = next((s for s in self.submissions if s.pid == pid), None)
        you = {
            "pid": pid,
            "nickname": player.nickname,
            "avatar": player.avatar,
            "color": player.color,
            "score": player.score,
            "is_host": pid == self.host_pid,
            "is_judge": pid == self.judge_pid,
            "hand": [gifs[g] for g in player.hand if g in gifs],
            "submitted_gif": gifs.get(submitted.gif_id) if submitted else None,
            "submitted_auto": submitted.auto if submitted else False,
            # Which card on the table is yours, so your phone can mark it during the
            # reveal. Only ever in *your* view — you already know what you played, so
            # this tells you nothing new, and it tells nobody else anything at all.
            "slot": (
                self.submissions.index(submitted)
                if submitted is not None and self.phase in (Phase.REVEAL, Phase.PICK_WINNER, Phase.ROUND_RESULT, Phase.GAME_OVER)
                else None
            ),
        }
        state["you"] = you
        # Only the judge sees the three prompts on offer.
        if pid == self.judge_pid and self.phase == Phase.PROMPT_PICK:
            prompts = prompt_index()
            state["prompt_choices"] = [prompts[p] for p in self.prompt_choice_ids if p in prompts]
        return state

    def view_for_tv(self) -> dict:
        state = self.public_state()
        state["you"] = None
        state["is_tv"] = True
        return state


# --- helpers ------------------------------------------------------------------
def clean_nickname(raw: str) -> str:
    name = " ".join((raw or "").split())
    if len(name) < NICKNAME_MIN:
        raise ActionError(f"Nickname needs at least {NICKNAME_MIN} characters")
    if len(name) > NICKNAME_MAX:
        name = name[:NICKNAME_MAX]
    return name
