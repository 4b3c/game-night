import os
import secrets


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Config:
    # A fresh key each boot is fine (and slightly safer for a party app): server-side
    # game state is in memory anyway, so a restart ends any game in progress. Set
    # SECRET_KEY in the environment if you want cookies to survive a restart.
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

    # --- session cookie ---------------------------------------------------
    # SESSION_PERMANENT = False means the cookie is written with no Expires/Max-Age,
    # so the browser throws it away when it closes. It holds nothing but a random
    # player id; nicknames, hands and scores live in server memory keyed by that id.
    SESSION_COOKIE_NAME = "gn_player"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_PERMANENT = False
    # Turn this on once you're actually serving over HTTPS. With it on, browsers refuse
    # to store the cookie over plain http — which would stop anyone from joining.
    SESSION_COOKIE_SECURE = _flag("GN_COOKIE_SECURE", False)

    TEMPLATES_AUTO_RELOAD = True

    # --- deployment -------------------------------------------------------
    # Behind nginx/Traefik/Caddy: trust one hop of X-Forwarded-* so Flask knows the
    # real scheme and host. Leave off when the app is exposed directly, otherwise
    # clients could spoof those headers.
    BEHIND_PROXY = _flag("GN_BEHIND_PROXY", False)

    # --- game night -------------------------------------------------------
    # Rooms are deleted this many seconds after their last activity.
    GN_ROOM_IDLE_TIMEOUT = int(os.environ.get("GN_ROOM_IDLE_TIMEOUT", 600))
