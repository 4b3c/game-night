import hashlib
from pathlib import Path

from flask import Flask
from werkzeug.routing import BaseConverter

from .extensions import socketio

_ASSET_VERSIONS: dict[str, str] = {}


def asset_version(static_folder: str, filename: str) -> str | None:
    """A short fingerprint of a static file, from its size and mtime.

    Every CSS/JS URL carries this as `?v=`, which is what makes a deploy actually reach
    players: a CDN (Cloudflare, here) caches by full URL, so changing the file changes
    the URL and the old cached copy is simply never asked for again. Without it, a
    `Cache-Control: max-age` of a week means a week of players running the old client.
    """
    if filename in _ASSET_VERSIONS:
        return _ASSET_VERSIONS[filename]
    path = Path(static_folder) / filename
    # Directories must be left alone: the GIF base path is handed to the client as a
    # prefix it concatenates filenames onto, so a `?v=` there would corrupt every URL.
    if not path.is_file():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    token = hashlib.sha1(f"{stat.st_size}-{stat.st_mtime_ns}".encode()).hexdigest()[:10]
    _ASSET_VERSIONS[filename] = token
    return token


class RoomCodeConverter(BaseConverter):
    """A 4-letter room code, so `/<code:code>` matches /ABCD and nothing else.

    Without this, a bare `/<code>` rule at the site root would also match /healthz,
    /favicon.ico and anything else someone asks for.
    """

    regex = "[A-Za-z]{4}"


def create_app(config_object: str = "app.config.Config") -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    # Converters must be registered before the blueprints that use them.
    app.url_map.converters["code"] = RoomCodeConverter

    @app.url_defaults
    def _version_static(endpoint: str, values: dict) -> None:
        if endpoint != "static" or "filename" not in values or "v" in values:
            return
        token = asset_version(app.static_folder, values["filename"])
        if token:
            values["v"] = token

    if app.config.get("BEHIND_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        # One hop: the reverse proxy on the same host. Without this, Flask thinks every
        # request arrived over http and marks Secure cookies as unsafe to send.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    from .routes import main

    app.register_blueprint(main)

    from .games.gah import gah_bp

    app.register_blueprint(gah_bp)

    # Importing the module registers its Socket.IO handlers.
    from .games.gah import events  # noqa: F401

    socketio.init_app(app, cors_allowed_origins="*")

    from .games.gah.rooms import rooms

    rooms.idle_timeout = app.config["GN_ROOM_IDLE_TIMEOUT"]

    if app.config["SESSION_COOKIE_SECURE"]:
        # Worth shouting about: with this on, a browser on plain http silently refuses
        # the session cookie, and every player gets stuck unable to join.
        app.logger.warning(
            "GN_COOKIE_SECURE is on — players can only join over https. "
            "Set it to 0 until your TLS proxy is working."
        )

    return app
