from flask import Blueprint, redirect, render_template, url_for

from ...identity import current_pid
from ..registry import GAMES, register_code_resolver
from .engine import MAX_PLAYERS, MIN_PLAYERS, TARGET_SCORE_RANGE, TEST_MIN_PLAYERS
from .rooms import rooms

gah_bp = Blueprint("gah", __name__, url_prefix="/gah")

GAME = next(g for g in GAMES if g.slug == "gah")

# So the home page's Join box can route a bare code to this game.
register_code_resolver("gah", rooms.exists)


def _limits() -> dict:
    return {
        "min_players": MIN_PLAYERS,
        "test_min_players": TEST_MIN_PLAYERS,
        "max_players": MAX_PLAYERS,
        "score_min": TARGET_SCORE_RANGE[0],
        "score_max": TARGET_SCORE_RANGE[1],
    }


@gah_bp.route("/")
def landing():
    current_pid()
    return render_template("gah/landing.html", title=GAME.name, game=GAME, limits=_limits())


@gah_bp.post("/new")
def new_game():
    pid = current_pid()
    game = rooms.create(host_pid=pid)
    return redirect(url_for("gah.play", code=game.code))


@gah_bp.route("/<code>")
def play(code: str):
    code = code.upper()
    game = rooms.get(code)
    if game is None:
        return redirect(url_for("main.home", error="no-room", code=code))
    pid = current_pid()
    with rooms.lock:
        is_member = pid in game.players
        in_progress = game.in_progress
    return render_template(
        "gah/play.html",
        title=f"{GAME.short_name} · {code}",
        code=code,
        game=GAME,
        limits=_limits(),
        is_member=is_member,
        in_progress=in_progress,
    )


@gah_bp.route("/<code>/tv")
def tv(code: str):
    code = code.upper()
    game = rooms.get(code)
    if game is None:
        return redirect(url_for("main.home", error="no-room", code=code))
    return render_template("gah/tv.html", title=f"{GAME.short_name} on TV · {code}", code=code, game=GAME)
