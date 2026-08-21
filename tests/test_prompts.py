"""The prompt half of the deck: the store, the curator's routes, and which pile is dealt.

Every test here runs against a temporary curation/ folder, so nothing touches the real
deck. The curator app is built directly rather than through main(), which wants API keys
and a password on the command line.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import curation_store as store  # noqa: E402
from scripts import curate_gifs as curator  # noqa: E402

PASSWORD = "open-sesame-please-now"


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Point the store at a throwaway folder, seeded with a few prompts."""
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(store, "PROMPTS", tmp_path / "prompts.json")
    monkeypatch.setattr(store, "LIBRARY", tmp_path / "library.json")
    monkeypatch.setattr(store, "IGNORED", tmp_path / "ignored.json")
    monkeypatch.setattr(store, "LOCK", tmp_path / ".lock")
    store.update_prompts(
        lambda rows: rows.update(
            {
                "p001": {"text": "Clean one ___.", "blanks": 1, "sets": ["normal"], "added": "x"},
                "p002": {"text": "Filthy one ___.", "blanks": 1, "sets": ["adult"], "added": "x"},
                "p003": {"text": "Retired ___.", "blanks": 1, "sets": [], "added": "x"},
            }
        )
    )
    return tmp_path


class Source:
    """Stands in for Giphy — the prompt routes never reach for it."""

    name = "giphy"
    rating = "r"
    key_env = "GIPHY_API_KEY"


@pytest.fixture
def client(state):
    app = curator.build_app(curator.Library(), Source(), PASSWORD)
    app.config.update(TESTING=True)
    with app.test_client() as client:
        client.post("/login", data={"password": PASSWORD})
        yield client


# --- the store ------------------------------------------------------------------
def test_add_prompt_picks_a_free_id_and_stamps_it(state):
    prompt_id, row = store.add_prompt("Brand new ___.", ["normal"])
    assert prompt_id == "p004"
    assert row["sets"] == ["normal"]
    assert row["added"]
    assert store.prompts()[prompt_id]["text"] == "Brand new ___."


def test_add_prompt_never_reuses_an_id(state):
    """Deleting from the middle used to make the next id collide with a live row."""
    store.drop_prompt("p002")
    first, _ = store.add_prompt("One ___.", ["normal"])
    second, _ = store.add_prompt("Two ___.", ["normal"])
    assert first != second
    assert len(store.prompts()) == 4


def test_drop_prompt_reports_whether_there_was_one(state):
    assert store.drop_prompt("p001") is True
    assert store.drop_prompt("p001") is False
    assert "p001" not in store.prompts()


def test_put_prompt_keeps_the_original_added_stamp(state):
    store.put_prompt("p001", {"text": "Edited ___.", "sets": ["adult"]})
    row = store.prompts()["p001"]
    assert row["text"] == "Edited ___." and row["sets"] == ["adult"]
    assert row["added"] == "x"


# --- the curator's routes -------------------------------------------------------
def test_prompts_route_lists_everything_with_counts(client):
    data = client.get("/api/prompts").get_json()
    assert [p["id"] for p in data["prompts"]] == ["p001", "p002", "p003"]
    assert data["counts"] == {"normal": 1, "adult": 1, "all": 3}


def test_saving_without_an_id_creates_one(client):
    data = client.post("/api/prompt-save", json={"text": "Fresh ___.", "sets": ["adult"]}).get_json()
    assert data["prompt"]["id"] == "p004"
    assert data["prompt"]["sets"] == ["adult"]
    assert data["counts"]["adult"] == 2


def test_saving_with_an_id_edits_in_place(client):
    data = client.post(
        "/api/prompt-save", json={"id": "p001", "text": "  Reworded   ___.  ", "sets": ["normal", "adult"]}
    ).get_json()
    assert data["prompt"]["text"] == "Reworded ___."  # whitespace collapsed
    assert data["prompt"]["sets"] == ["normal", "adult"]
    assert len(client.get("/api/prompts").get_json()["prompts"]) == 3


def test_blanks_are_counted_from_the_text(client):
    data = client.post("/api/prompt-save", json={"text": "___ and ___.", "sets": ["normal"]}).get_json()
    assert data["prompt"]["blanks"] == 2


def test_empty_text_is_refused(client):
    response = client.post("/api/prompt-save", json={"text": "   ", "sets": ["normal"]})
    assert response.status_code == 400
    assert "words" in response.get_json()["error"]


def test_overlong_text_is_refused(client):
    response = client.post(
        "/api/prompt-save", json={"text": "x" * (curator.MAX_PROMPT_CHARS + 1), "sets": ["normal"]}
    )
    assert response.status_code == 400


def test_unknown_sets_are_dropped_rather_than_stored(client):
    data = client.post(
        "/api/prompt-save", json={"text": "Odd ___.", "sets": ["normal", "nonsense"]}
    ).get_json()
    assert data["prompt"]["sets"] == ["normal"]


def test_editing_something_that_is_not_there_is_a_404(client):
    assert client.post("/api/prompt-save", json={"id": "p999", "text": "x ___", "sets": []}).status_code == 404


def test_dropping_removes_it(client):
    assert client.post("/api/prompt-drop", json={"id": "p002"}).get_json()["counts"]["all"] == 2
    assert client.post("/api/prompt-drop", json={"id": "p002"}).status_code == 404


def test_prompt_routes_need_the_password(state):
    app = curator.build_app(curator.Library(), Source(), PASSWORD)
    with app.test_client() as anonymous:
        assert anonymous.get("/api/prompts").status_code == 401
        assert anonymous.post("/api/prompt-save", json={"text": "x ___", "sets": []}).status_code == 401
        assert anonymous.post("/api/prompt-drop", json={"id": "p001"}).status_code == 401


# --- what the game does with them -----------------------------------------------
@pytest.fixture
def deck(tmp_path, monkeypatch):
    """The game's reader, pointed at a prompt file we control."""
    from app.games.gah import decks as D

    path = tmp_path / "prompts.json"
    monkeypatch.setattr(D, "PROMPTS_PATH", path)
    monkeypatch.setattr(D, "_prompt_file", D._Reloading(path, D._read_prompts))

    def write(rows: dict) -> None:
        path.write_text(json.dumps({"version": 1, "prompts": rows}))

    return D, write


def rows(n: int, sets: list[str], start: int = 1) -> dict:
    return {f"p{i:03d}": {"text": f"Number {i} ___.", "sets": sets} for i in range(start, start + n)}


def test_the_adult_pile_tops_up_the_clean_one(deck):
    """Dirty is clean plus 18+, not instead of it — a prompt tagged Normal is in both."""
    D, write = deck
    write({**rows(10, ["normal"]), **rows(4, ["adult"], start=50)})
    assert len(D.prompts_for(clean=True)) == 10
    assert len(D.prompts_for(clean=False)) == 14
    assert {p["id"] for p in D.prompts_for(True)} < {p["id"] for p in D.prompts_for(False)}


def test_a_prompt_in_no_pile_plays_nowhere_but_stays_readable(deck):
    """Untagging is how a prompt is retired. A round already showing it must still be
    able to look it up, so it leaves the deck without leaving the index."""
    D, write = deck
    write({**rows(10, ["normal"]), "p999": {"text": "Retired ___.", "sets": []}})
    assert "p999" not in {p["id"] for p in D.prompts_for(clean=True)}
    assert "p999" not in {p["id"] for p in D.prompts_for(clean=False)}
    assert D.prompt_index()["p999"]["text"] == "Retired ___."


def test_the_deck_is_built_from_the_side_of_the_switch_it_was_asked_for(deck):
    D, write = deck
    write({**rows(10, ["normal"]), **rows(4, ["adult"], start=50)})
    assert len(D.build_prompt_deck(random.Random(1), clean=True)) == 10
    assert len(D.build_prompt_deck(random.Random(1), clean=False)) == 14


def test_an_edit_is_picked_up_without_a_restart(deck):
    """The curator writes this file while the game is running — that's the whole reason
    prompts moved out of the image and into curation/."""
    D, write = deck
    write(rows(10, ["normal"]))
    assert len(D.load_prompts()) == 10
    write(rows(12, ["normal"]))
    assert len(D.load_prompts()) == 12


def test_starting_with_no_prompts_in_this_deck_says_so(deck, monkeypatch):
    from app.games.gah import engine as E
    from tests.test_engine import make_game

    D, write = deck
    monkeypatch.setattr(E, "build_prompt_deck", D.build_prompt_deck)
    monkeypatch.setattr(E, "deck_counts", D.deck_counts)

    # Tag every prompt 18+ so the clean deck has none. The GIF check runs first and
    # passes on the real library, which is what isolates the prompt check here.
    write(rows(10, ["adult"]))
    game, _ = make_game(4)
    with pytest.raises(E.ActionError, match="prompts"):
        game.start_game("p0")

    # Turning the switch off is enough — the same prompts are suddenly in the deck.
    game.set_options("p0", {"clean": False})
    game.start_game("p0")
    assert game.phase is not E.Phase.LOBBY


# --- what a finished round writes back ---------------------------------------
def test_record_round_counts_the_prompt_it_was_played_on(state):
    """A prompt's score is rounds played on it, written by the game, same as a card's."""
    store.record_round([], None, "p001")
    store.record_round([], None, "p001")
    store.record_round([], None, "p002")

    rows = store.prompts()
    assert rows["p001"]["uses"] == 2
    assert rows["p002"]["uses"] == 1
    assert "uses" not in rows["p003"], "a prompt nobody played should stay untouched"


def test_record_round_survives_a_prompt_deleted_mid_round(state):
    """Deleting a prompt while it is on the table must not cost the cards their round."""
    store.record_round([], None, "p404")
    assert "p404" not in store.prompts()
