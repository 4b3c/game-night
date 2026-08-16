from flask import Flask
from werkzeug.routing import BaseConverter

from .extensions import socketio


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
