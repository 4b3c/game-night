from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from .games.registry import GAMES, find_game_for_code
from .identity import current_pid

main = Blueprint("main", __name__)


@main.route("/healthz")
def healthz():
    """Liveness for Docker/your proxy. Counts, never contents."""
    from .games.gah.rooms import rooms

    games = rooms.snapshot()
    return jsonify(
        status="ok",
        rooms=len(games),
        players=sum(len(game.players) for game in games),
    )


@main.route("/")
def home():
    # Mint the session cookie on the very first page view so the Socket.IO handshake
    # always carries a player id.
    current_pid()
    return render_template(
        "home.html",
        title="Game Night",
        games=GAMES,
        error=request.args.get("error"),
        code=request.args.get("code", ""),
    )


@main.post("/join")
def join():
    """Resolve a bare 4-letter code to whichever game owns it."""
    code = (request.form.get("code") or "").strip().upper()
    if len(code) != 4 or not code.isalpha():
        return redirect(url_for("main.home", error="bad-code", code=code))

    game = find_game_for_code(code)
    if game is None:
        return redirect(url_for("main.home", error="no-room", code=code))

    return redirect(f"/{game.slug}/{code}")
