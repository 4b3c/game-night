from flask import Blueprint, redirect, render_template, request, url_for

from ...identity import current_pid
from .engine import MAX_PLAYERS, MIN_PLAYERS, TARGET_SCORE_RANGE, TEST_MIN_PLAYERS
from .rooms import rooms

# No url_prefix: this game *is* the site. `/` is the landing page, `/ABCD` is a game.
# The 4-letter room code is matched by the `code` converter registered in create_app(),
# so `/<code:code>` can never swallow /healthz, /static or anything else.
gah_bp = Blueprint("gah", __name__)

GAME = {
    "name": "Gifs Against Humanity",
    "short_name": "GAH",
    "tagline": "Answer terrible prompts with worse GIFs. A judge crowns the funniest.",
    "emoji": "🃏",
    "players": f"{MIN_PLAYERS}–{MAX_PLAYERS} players",
}


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
    # Mint the session cookie on the first page view so the Socket.IO handshake always
    # carries a player id.
    current_pid()
    return render_template(
        "gah/landing.html",
        title=GAME["name"],
        game=GAME,
        limits=_limits(),
        error=request.args.get("error"),
        code=(request.args.get("code") or "")[:4].upper(),
    )


@gah_bp.post("/new")
def new_game():
    pid = current_pid()
    game = rooms.create(host_pid=pid)
    return redirect(url_for("gah.play", code=game.code))


@gah_bp.post("/join")
def join():
    code = (request.form.get("code") or "").strip().upper()
    if len(code) != 4 or not code.isalpha():
        return redirect(url_for("gah.landing", error="bad-code", code=code))
    if not rooms.exists(code):
        return redirect(url_for("gah.landing", error="no-room", code=code))
    return redirect(url_for("gah.play", code=code))


@gah_bp.route("/<code:code>")
def play(code: str):
    code = code.upper()
    game = rooms.get(code)
    if game is None:
        return redirect(url_for("gah.landing", error="no-room", code=code))
    pid = current_pid()
    with rooms.lock:
        is_member = pid in game.players
        in_progress = game.in_progress
    return render_template(
        "gah/play.html",
        title=f"{GAME['short_name']} · {code}",
        code=code,
        game=GAME,
        limits=_limits(),
        is_member=is_member,
        in_progress=in_progress,
    )


@gah_bp.route("/<code:code>/tv")
def tv(code: str):
    code = code.upper()
    if rooms.get(code) is None:
        return redirect(url_for("gah.landing", error="no-room", code=code))
    return render_template(
        "gah/tv.html",
        title=f"{GAME['short_name']} on TV · {code}",
        code=code,
        game=GAME,
    )
