"""Site-wide routes: health, and permanent redirects from the old /gah/* URLs."""

from flask import Blueprint, jsonify, redirect, url_for

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


# --- legacy URLs -----------------------------------------------------------------
# The game used to live under /gah/ while this was a multi-game platform. Anyone with
# an open tab or a shared link keeps working.
@main.route("/gah/")
@main.route("/gah")
def legacy_landing():
    return redirect(url_for("gah.landing"), code=301)


@main.post("/gah/new")
def legacy_new():
    return redirect(url_for("gah.new_game"), code=308)  # 308 keeps it a POST


@main.route("/gah/<code:code>")
def legacy_play(code: str):
    return redirect(url_for("gah.play", code=code.upper()), code=301)


@main.route("/gah/<code:code>/tv")
def legacy_tv(code: str):
    return redirect(url_for("gah.tv", code=code.upper()), code=301)
