from flask import Flask

from .extensions import socketio


def create_app(config_object: str = "app.config.Config") -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

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
