"""Rules tests for the Gifs Against Humanity engine.

No Flask, no browser, no sockets — the engine is a plain state machine, so every rule
(including who is allowed to see what) is tested directly.
"""

from __future__ import annotations

import random

import pytest

from app.games.gah import engine as E
from app.games.gah.decks import Deck, build_gif_deck, gifs_for, load_gifs
from app.games.gah.engine import ActionError, Game, Phase


class Clock:
    def __init__(self, t: float = 1_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


NAMES = ["Abe", "Sam", "Jo", "Kim", "Lee", "Max", "Nia", "Oz"]


def make_game(n: int = 4, *, start: bool = False, **options) -> tuple[Game, Clock]:
    clock = Clock()
    game = Game(code="TEST", host_pid="p0", rng=random.Random(1234), now=clock)
    for i in range(n):
        game.add_player(f"p{i}", NAMES[i])
    if options:
        game.set_options("p0", options)
    if start:
        game.start_game("p0")
    return game, clock


def submitters(game: Game) -> list[E.Player]:
    return [p for p in game.players.values() if p.pid != game.judge_pid]


def play_round(game: Game, *, winner_pid: str | None = None) -> str:
    """Run one full round, returning the pid awarded the point."""
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    for player in submitters(game):
        game.submit_card(player.pid, player.hand[0])
    for slot in range(len(game.submissions)):
        game.flip(judge, slot)
    slot = 0
    if winner_pid is not None:
        slot = next(i for i, s in enumerate(game.submissions) if s.pid == winner_pid)
    awarded = game.submissions[slot].pid
    game.pick_winner(judge, slot)
    return awarded


# --- lobby --------------------------------------------------------------------
def test_join_assigns_distinct_avatars_and_colors():
    game, _ = make_game(8)
    avatars = [p.avatar for p in game.players.values()]
    colors = [p.color for p in game.players.values()]
    assert len(set(avatars)) == 8
    assert len(set(colors)) == 8


def test_room_is_capped_at_eight():
    game, _ = make_game(8)
    with pytest.raises(ActionError, match="full"):
        game.add_player("p8", "Nine")


def test_duplicate_nickname_is_rejected_case_insensitively():
    game, _ = make_game(2)
    with pytest.raises(ActionError, match="took that name"):
        game.add_player("px", "abe")


def test_nickname_is_trimmed_and_truncated():
    game, _ = make_game(1)
    player = game.add_player("px", "   a  very long nickname here ")
    assert player.nickname == "a very long n"[:E.NICKNAME_MAX]
    with pytest.raises(ActionError):
        game.add_player("py", "x")


def test_rejoining_with_the_same_pid_returns_the_same_seat():
    game, _ = make_game(4)
    before = game.players["p1"]
    again = game.add_player("p1", "Totally Different")
    assert again is before
    assert len(game.players) == 4


def test_start_requires_minimum_players():
    game, _ = make_game(3)
    with pytest.raises(ActionError, match="at least 4"):
        game.start_game("p0")


def test_test_mode_allows_two_players():
    game, _ = make_game(2, test_mode=True)
    game.start_game("p0")
    assert game.phase == Phase.ROUND_READY


def test_only_host_can_start_or_set_options():
    game, _ = make_game(4)
    with pytest.raises(ActionError, match="Only the host"):
        game.start_game("p1")
    with pytest.raises(ActionError, match="Only the host"):
        game.set_options("p1", {"target_score": 3})


def test_kick_is_lobby_only_and_reseats():
    game, _ = make_game(4)
    game.kick("p0", "p2")
    assert "p2" not in game.players
    assert [game.players[pid].seat for pid in game.seat_order] == [0, 1, 2]
    game.add_player("p2", "Jo")
    game.start_game("p0")
    with pytest.raises(ActionError, match="lobby"):
        game.kick("p0", "p1")


def test_host_transfers_when_the_host_leaves():
    game, _ = make_game(4)
    game.remove_player("p0")
    assert game.host_pid in game.players
    assert game.host_pid == "p1"


def test_options_are_clamped():
    game, _ = make_game(4)
    game.set_options("p0", {"target_score": 99, "prompt_seconds": 2, "submit_seconds": 0})
    assert game.options.target_score == E.TARGET_SCORE_RANGE[1]
    assert game.options.prompt_seconds == 5  # floor, not 2
    assert game.options.submit_seconds == 0  # 0 means "no timer"
    with pytest.raises(ActionError, match="rotation"):
        game.set_options("p0", {"judge_rotation": "nonsense"})


# --- dealing ------------------------------------------------------------------
def test_start_deals_seven_unique_cards_each():
    game, _ = make_game(8, start=True)
    all_cards = [c for p in game.players.values() for c in p.hand]
    assert all(len(p.hand) == E.HAND_SIZE for p in game.players.values())
    assert len(all_cards) == len(set(all_cards)) == 8 * E.HAND_SIZE


def test_submitting_refills_the_hand_immediately():
    game, _ = make_game(4, start=True)
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    player = submitters(game)[0]
    played = player.hand[2]
    game.submit_card(player.pid, played)
    assert len(player.hand) == E.HAND_SIZE
    assert played not in player.hand


def check_no_duplicates(game: Game, total: int, where: str) -> None:
    """No GIF may ever be in two places at once, and none may go missing.

    The property that matters to players: a card in someone's hand is in nobody else's
    hand, and isn't simultaneously sitting in the deck waiting to be dealt again.
    """
    places = game.card_locations()
    hands = places["hands"]
    everywhere = [card for cards in places.values() for card in cards]

    duplicates = {card for card in everywhere if everywhere.count(card) > 1}
    assert not duplicates, (
        f"{where}: {sorted(duplicates)} in more than one place — "
        + ", ".join(f"{k}={len(v)}" for k, v in places.items())
    )
    assert len(set(hands)) == len(hands), f"{where}: two players hold the same GIF"
    for pile in ("draw", "discard"):
        clash = set(hands) & set(places[pile])
        assert not clash, f"{where}: {sorted(clash)} is both in a hand and in the {pile} pile"
    assert len(everywhere) == total, (
        f"{where}: {len(everywhere)} cards accounted for, expected {total} — "
        + ", ".join(f"{k}={len(v)}" for k, v in places.items())
    )


def test_two_players_can_never_hold_the_same_gif():
    """The headline guarantee, checked at every step of a long 8-player game.

    Eight players hold 56 of the 80 cards, so the draw pile empties and the discard is
    reshuffled several times over a game this long — exactly where a double-deal bug
    would show up.
    """
    total = len(gifs_for(clean=True))
    game, _ = make_game(8, start=True, target_score=10, judge_rotation="circle")
    check_no_duplicates(game, total, "after the deal")

    reshuffles = 0
    for round_number in range(40):
        judge = game.judge_pid
        game.judge_ready(judge)
        game.pick_prompt(judge, game.prompt_choice_ids[0])
        check_no_duplicates(game, total, f"round {round_number} prompt chosen")

        for player in submitters(game):
            before = len(game._gif_deck.draw_pile)
            game.submit_card(player.pid, player.hand[0])
            if len(game._gif_deck.draw_pile) > before:
                reshuffles += 1
            check_no_duplicates(game, total, f"round {round_number} after {player.nickname} played")

        # Every hand is still full and still has no repeats inside it.
        for player in submitters(game):
            assert len(player.hand) == E.HAND_SIZE
            assert len(set(player.hand)) == E.HAND_SIZE, f"{player.nickname} holds a duplicate"

        for slot in range(len(game.submissions)):
            game.flip(judge, slot)
        game.pick_winner(judge, 0)
        check_no_duplicates(game, total, f"round {round_number} awarded")
        if game.phase == Phase.GAME_OVER:
            break
        game.next_round(judge)
        check_no_duplicates(game, total, f"round {round_number} ended")

    assert reshuffles > 0, "the deck never recycled, so this test proved less than it should"


def test_clean_leaves_the_adult_pile_out_and_dirty_adds_it(monkeypatch):
    """One deck with a switch on it: dirty is clean plus the 18+ pile, never instead."""
    from app.games.gah import decks

    tagged = (
        [{"id": f"s{i}", "file": f"s{i}.gif", "label": "s", "sets": ["normal"]} for i in range(60)]
        + [{"id": f"a{i}", "file": f"a{i}.gif", "label": "a", "sets": ["adult"]} for i in range(30)]
    )
    monkeypatch.setattr(decks, "load_gifs", lambda: tuple(tagged))

    assert decks.deck_counts()["clean"]["gifs"] == 60
    assert decks.deck_counts()["spicy"]["gifs"] == 90
    assert {c["id"] for c in decks.gifs_for(True)} < {c["id"] for c in decks.gifs_for(False)}

    # Clean is the default, and it deals nothing from the 18+ pile.
    game, _ = make_game(4, start=True)
    assert game.options.clean is True
    dealt = [card for player in game.players.values() for card in player.hand]
    assert dealt and not any(card.startswith("a") for card in dealt)

    # Turned off, the 18+ cards are in the deck — checked on the pile rather than the
    # hands, since 28 cards out of 90 could miss them by luck.
    game, _ = make_game(4)
    game.set_options("p0", {"clean": False})
    game.start_game("p0")
    assert len(game._gif_deck) == 90 - 4 * E.HAND_SIZE  # what's left after the deal
    assert game.public_state()["options"]["clean"] is False


def test_a_card_in_no_pile_is_dealt_by_neither(monkeypatch):
    """Untagging is how a card leaves the game — including the old Millennial ones."""
    from app.games.gah import decks

    tagged = (
        [{"id": f"s{i}", "file": f"s{i}.gif", "label": "s", "sets": ["normal"]} for i in range(60)]
        + [{"id": "orphan", "file": "orphan.gif", "label": "o", "sets": []}]
    )
    monkeypatch.setattr(decks, "load_gifs", lambda: tuple(tagged))

    assert "orphan" not in {c["id"] for c in decks.gifs_for(True)}
    assert "orphan" not in {c["id"] for c in decks.gifs_for(False)}


def test_an_older_single_tag_manifest_still_works():
    """Manifests written before sets existed used one `rating` per card."""
    from app.games.gah.decks import _sets_of

    assert _sets_of({"rating": "sfw"}) == ("normal",)
    assert _sets_of({"rating": "adult"}) == ("adult",)
    assert _sets_of({}) == ("normal",)
    assert _sets_of({"sets": ["adult"], "rating": "sfw"}) == ("adult",)
    assert _sets_of({"sets": []}) == ()


def test_the_clean_switch_takes_anything_truthy():
    """It arrives from a checkbox, so it is a bool or nothing at all."""
    game, _ = make_game(4)
    game.set_options("p0", {"clean": False})
    assert game.options.clean is False
    game.set_options("p0", {"target_score": 7})
    assert game.options.clean is False, "an unrelated setting must not reset it"
    game.set_options("p0", {"clean": True})
    assert game.options.clean is True


def test_a_deck_without_enough_cards_says_so(monkeypatch):
    from app.games.gah import decks

    thin = [{"id": f"s{i}", "file": f"s{i}.gif", "label": "s", "sets": ["normal"]} for i in range(10)]
    spicy = [{"id": f"a{i}", "file": f"a{i}.gif", "label": "a", "sets": ["adult"]} for i in range(60)]
    monkeypatch.setattr(decks, "load_gifs", lambda: tuple(thin + spicy))

    game, _ = make_game(4)
    with pytest.raises(ActionError, match="only has 10 GIFs"):
        game.start_game("p0")
    assert game.phase == Phase.LOBBY

    # The lobby is told, so it can disable Start rather than let someone find out here.
    deck = game.public_state()["deck"]
    assert deck["ready"] is False
    assert "10 GIFs" in deck["why"]
    assert deck["adds"] == {"gifs": 60, "prompts": 0}

    # And turning the switch off is enough to make it playable.
    game.set_options("p0", {"clean": False})
    assert game.public_state()["deck"]["ready"] is True
    game.start_game("p0")
    assert game.phase == Phase.ROUND_READY


def test_starting_without_enough_gifs_is_refused_before_dealing(monkeypatch):
    """Swapping in your own GIFs shouldn't be able to half-deal a game."""
    game, _ = make_game(8)
    small = tuple(gifs_for(clean=True)[:20])  # 20 cards, 8 players need 56
    monkeypatch.setattr("app.games.gah.decks.load_gifs", lambda: small)

    with pytest.raises(ActionError, match="only has 20 GIFs"):
        game.start_game("p0")

    # Nothing was dealt and the game is still joinable.
    assert game.phase == Phase.LOBBY
    assert all(p.hand == [] for p in game.players.values())


def test_deck_recycles_its_discard_pile():
    deck = build_gif_deck(random.Random(1))
    drawn = [deck.draw() for _ in range(len(gifs_for(clean=True)))]
    assert not deck.draw_pile
    deck.discard(*drawn)
    assert deck.draw() in drawn  # recycled rather than raising


def test_empty_deck_raises_rather_than_dealing_nothing():
    deck = Deck([], random.Random(1))
    with pytest.raises(Exception):
        deck.draw()


# --- round flow ---------------------------------------------------------------
def test_happy_path_round():
    game, _ = make_game(4, start=True)
    judge = game.judge_pid
    assert game.phase == Phase.ROUND_READY

    game.judge_ready(judge)
    assert game.phase == Phase.PROMPT_PICK
    assert len(game.prompt_choice_ids) == E.PROMPT_CHOICES

    game.pick_prompt(judge, game.prompt_choice_ids[1])
    assert game.phase == Phase.SUBMIT
    assert game.prompt_id is not None

    for player in submitters(game):
        game.submit_card(player.pid, player.hand[0])
    assert game.phase == Phase.REVEAL
    assert len(game.submissions) == 3

    for slot in range(2):
        game.flip(judge, slot)
    assert game.phase == Phase.REVEAL  # not all flipped yet

    game.flip(judge, 2)
    assert game.phase == Phase.PICK_WINNER

    winner = game.submissions[1].pid
    game.pick_winner(judge, 1)
    assert game.phase == Phase.ROUND_RESULT
    assert game.players[winner].score == 1
    assert game.round_winner_pid == winner

    game.next_round(judge)
    assert game.phase == Phase.ROUND_READY
    assert game.round_number == 2


def test_judge_cannot_submit_and_others_cannot_judge():
    game, _ = make_game(4, start=True)
    judge = game.judge_pid
    other = submitters(game)[0].pid
    game.judge_ready(judge)
    with pytest.raises(ActionError, match="Only the judge"):
        game.pick_prompt(other, game.prompt_choice_ids[0])
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    with pytest.raises(ActionError, match="judge this round"):
        game.submit_card(judge, game.players[judge].hand[0])


def test_cannot_submit_twice_or_play_a_card_you_dont_hold():
    game, _ = make_game(4, start=True)
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    player = submitters(game)[0]
    game.submit_card(player.pid, player.hand[0])
    with pytest.raises(ActionError, match="already played"):
        game.submit_card(player.pid, player.hand[0])
    other = submitters(game)[1]
    with pytest.raises(ActionError, match="isn't in your hand"):
        game.submit_card(other.pid, "gif_does_not_exist")


def test_winner_cannot_be_picked_before_every_card_is_flipped():
    game, _ = make_game(4, start=True)
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    for player in submitters(game):
        game.submit_card(player.pid, player.hand[0])
    with pytest.raises(ActionError, match="Too late|"):
        game.pick_winner(judge, 0)  # still REVEAL
    assert game.phase == Phase.REVEAL


def test_flipping_the_same_slot_twice_is_rejected():
    game, _ = make_game(4, start=True)
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    for player in submitters(game):
        game.submit_card(player.pid, player.hand[0])
    game.flip(judge, 0)
    with pytest.raises(ActionError, match="Already flipped"):
        game.flip(judge, 0)
    with pytest.raises(ActionError, match="No card there"):
        game.flip(judge, 99)


# --- judge rotation -----------------------------------------------------------
def test_circle_rotation_follows_seat_order():
    game, _ = make_game(4, start=True, judge_rotation="circle")
    first = game.judge_pid
    expected = game.seat_order[(game.seat_order.index(first) + 1) % 4]
    play_round(game)
    game.next_round(first)
    assert game.judge_pid == expected


def test_last_winner_rotation_makes_the_winner_judge():
    game, _ = make_game(4, start=True, judge_rotation="last_winner")
    judge = game.judge_pid
    winner = play_round(game)
    game.next_round(judge)
    assert game.judge_pid == winner


# --- scoring / end of game ----------------------------------------------------
def test_reaching_the_target_score_ends_the_game():
    game, _ = make_game(4, start=True, target_score=3, judge_rotation="circle")
    hero = next(p for p in game.players.values() if p.pid != game.judge_pid).pid

    wins = 0
    while game.phase != Phase.GAME_OVER:
        judge = game.judge_pid
        target = hero if hero != judge else None
        awarded = play_round(game, winner_pid=target)
        if awarded == hero:
            wins += 1
        if game.phase == Phase.ROUND_RESULT:
            game.next_round(judge)

    assert game.champion_pid == hero
    assert game.players[hero].score == 3
    assert wins == 3
    # The winning card is still on the table for the TV's confetti moment.
    cards = game.public_state()["cards"]
    assert any(c.get("is_winner") for c in cards)


def test_rematch_keeps_players_but_resets_the_game():
    game, _ = make_game(4, start=True, target_score=3)
    game.players["p1"].score = 3
    game.champion_pid = "p1"
    game.phase = Phase.GAME_OVER
    game.rematch("p0")
    assert game.phase == Phase.LOBBY
    assert len(game.players) == 4
    assert all(p.score == 0 for p in game.players.values())
    assert all(p.hand == [] for p in game.players.values())
    assert game.champion_pid is None
    game.start_game("p0")
    assert game.phase == Phase.ROUND_READY


# --- timers and away players --------------------------------------------------
def test_prompt_timer_picks_a_prompt_at_random():
    game, clock = make_game(4, start=True, prompt_seconds=10)
    judge = game.judge_pid
    game.judge_ready(judge)
    choices = list(game.prompt_choice_ids)
    clock.advance(9)
    assert game.tick() is False
    clock.advance(2)
    assert game.tick() is True
    assert game.phase == Phase.SUBMIT
    assert game.prompt_id in choices


def test_submit_timer_plays_a_random_card_for_everyone_left():
    game, clock = make_game(4, start=True, submit_seconds=90)
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    first = submitters(game)[0]
    game.submit_card(first.pid, first.hand[0])

    clock.advance(91)
    assert game.tick() is True
    assert game.phase == Phase.REVEAL
    assert len(game.submissions) == 3
    autos = [s for s in game.submissions if s.auto]
    assert len(autos) == 2
    assert all(len(p.hand) == E.HAND_SIZE for p in submitters(game))


def test_away_player_is_played_for_before_the_submit_timer_expires():
    game, clock = make_game(4, start=True, submit_seconds=300)
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    ghost = submitters(game)[0]
    game.mark_disconnected(ghost.pid)

    clock.advance(E.PLAYER_AWAY_GRACE - 1)
    game.tick()
    assert not any(s.pid == ghost.pid for s in game.submissions)

    clock.advance(2)
    assert game.tick() is True
    assert any(s.pid == ghost.pid and s.auto for s in game.submissions)
    assert game.phase == Phase.SUBMIT  # still waiting on the humans


def test_a_present_judge_is_never_rushed():
    game, clock = make_game(4, start=True)
    clock.advance(3600)
    assert game.tick() is False
    assert game.phase == Phase.ROUND_READY


def test_an_away_judge_cannot_stall_the_game():
    game, clock = make_game(4, start=True, submit_seconds=0, prompt_seconds=0)
    judge = game.judge_pid
    game.mark_disconnected(judge)

    clock.advance(E.JUDGE_AWAY_GRACE + 1)
    assert game.tick() is True
    assert game.phase == Phase.PROMPT_PICK  # ROUND_READY auto-advanced

    assert game.tick() is True
    assert game.phase == Phase.SUBMIT  # prompt auto-chosen despite timers being off

    # The other players are present, so the room still waits for them (timers are off).
    for player in submitters(game):
        game.submit_card(player.pid, player.hand[0])
    assert game.phase == Phase.REVEAL

    assert game.tick() is True
    assert game.phase == Phase.PICK_WINNER  # all cards auto-flipped

    assert game.tick() is True
    assert game.phase in (Phase.ROUND_RESULT, Phase.GAME_OVER)
    assert game.round_winner_pid is not None


def test_with_timers_off_present_players_are_waited_on_indefinitely():
    """"No timers" has to mean exactly that — the room waits for a present player."""
    game, clock = make_game(4, start=True, submit_seconds=0)
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    clock.advance(9999)
    assert game.tick() is False
    assert game.phase == Phase.SUBMIT
    assert game.submissions == []


def test_disconnect_keeps_the_seat_mid_game_but_frees_it_in_the_lobby():
    game, _ = make_game(4, start=True)
    game.mark_disconnected("p1")
    assert "p1" in game.players
    assert game.players["p1"].connected is False
    game.mark_connected("p1")
    assert game.players["p1"].connected is True

    lobby, _ = make_game(4)
    lobby.mark_disconnected("p1")
    assert "p1" not in lobby.players


# --- redaction ----------------------------------------------------------------
def test_a_player_never_receives_another_players_hand():
    game, _ = make_game(4, start=True)
    view = game.view_for("p1")
    blob = repr(view)
    assert len(view["you"]["hand"]) == E.HAND_SIZE
    for pid, player in game.players.items():
        if pid == "p1":
            continue
        for card in player.hand:
            assert card not in blob, f"{pid}'s card {card} leaked into p1's view"


def test_only_the_judge_sees_the_three_prompt_choices():
    game, _ = make_game(4, start=True)
    judge = game.judge_pid
    other = submitters(game)[0].pid
    game.judge_ready(judge)
    assert len(game.view_for(judge)["prompt_choices"]) == E.PROMPT_CHOICES
    assert "prompt_choices" not in game.view_for(other)
    assert "prompt_choices" not in game.view_for_tv()


def test_hidden_cards_carry_no_gif_and_no_author():
    game, _ = make_game(4, start=True)
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    for player in submitters(game):
        game.submit_card(player.pid, player.hand[0])

    for view in (game.view_for(judge), game.view_for_tv(), game.view_for(submitters(game)[0].pid)):
        for card in view["cards"]:
            assert card["revealed"] is False
            assert "gif" not in card
            assert "author" not in card

    game.flip(judge, 0)
    tv = game.view_for_tv()
    assert tv["cards"][0]["gif"]["id"]
    assert "author" not in tv["cards"][0]  # revealed, but still anonymous


def test_during_submission_nothing_reveals_what_was_played():
    game, _ = make_game(4, start=True)
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    player = submitters(game)[0]
    played = player.hand[0]
    game.submit_card(player.pid, played)

    tv = game.view_for_tv()
    judge_view = game.view_for(judge)
    assert tv["cards"] == [] and judge_view["cards"] == []
    assert tv["submitted_count"] == 1
    assert played not in repr(tv)
    assert played not in repr(judge_view)
    # The TV does say who the room is waiting on — that's the point of it.
    assert len(tv["waiting_on"]) == 2
    assert game.view_for(player.pid)["you"]["submitted_gif"]["id"] == played


def test_only_the_winning_card_gets_an_author():
    game, _ = make_game(4, start=True)
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    for player in submitters(game):
        game.submit_card(player.pid, player.hand[0])
    for slot in range(3):
        game.flip(judge, slot)
    game.pick_winner(judge, 1)

    cards = game.view_for_tv()["cards"]
    authored = [c for c in cards if c.get("author")]
    assert len(authored) == 1
    assert authored[0]["slot"] == 1
    assert authored[0]["author"]["pid"] == game.round_winner_pid


def test_you_learn_which_card_is_yours_and_nobody_elses():
    """Each player's own view marks their own answer, and only their own."""
    game, _ = make_game(4, start=True)
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    played = {}
    for player in submitters(game):
        played[player.pid] = player.hand[0]
        game.submit_card(player.pid, played[player.pid])

    # During the reveal, each player is told their slot — and it's the right one.
    for pid, gif_id in played.items():
        slot = game.view_for(pid)["you"]["slot"]
        assert slot is not None, "a player wasn't told which card was theirs"
        assert game.submissions[slot].pid == pid
        assert game.submissions[slot].gif_id == gif_id

    # Everybody's slot is different, and nothing about it reaches the room or the TV.
    slots = [game.view_for(pid)["you"]["slot"] for pid in played]
    assert len(set(slots)) == len(slots)
    assert game.view_for(judge)["you"]["slot"] is None  # the judge played nothing
    assert "slot" not in game.view_for_tv()
    assert "slot" not in game.public_state()
    for card in game.view_for_tv()["cards"]:
        assert "pid" not in card and "author" not in card


def test_no_slot_is_revealed_before_the_reveal():
    game, _ = make_game(4, start=True)
    judge = game.judge_pid
    game.judge_ready(judge)
    game.pick_prompt(judge, game.prompt_choice_ids[0])
    player = submitters(game)[0]
    game.submit_card(player.pid, player.hand[0])
    # Still SUBMIT: slots aren't shuffled yet, so an index would leak play order.
    assert game.view_for(player.pid)["you"]["slot"] is None


def test_every_round_deals_a_fresh_table():
    """Each round's cards are new objects with their own gifs.

    A client that keyed its card elements only by *count* showed the previous round's
    GIFs when two rounds in a row had the same number of answers. The engine side of
    that guarantee: consecutive rounds hand out different gif ids in each slot.
    """
    game, _ = make_game(4, start=True, target_score=10)
    seen = []
    for _ in range(3):
        judge = game.judge_pid
        game.judge_ready(judge)
        game.pick_prompt(judge, game.prompt_choice_ids[0])
        for player in submitters(game):
            game.submit_card(player.pid, player.hand[0])
        for slot in range(len(game.submissions)):
            game.flip(judge, slot)
        seen.append([c["gif"]["id"] for c in game.public_state()["cards"]])
        game.pick_winner(judge, 0)
        game.next_round(judge)

    flat = [gif for round_cards in seen for gif in round_cards]
    assert len(flat) == len(set(flat)), "a gif was played in two different rounds"
    assert seen[0] != seen[1] != seen[2]


def test_public_state_never_contains_a_hand():
    game, _ = make_game(4, start=True)
    blob = repr(game.public_state())
    for player in game.players.values():
        for card in player.hand:
            assert card not in blob
