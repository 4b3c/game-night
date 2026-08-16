"""Per-browser player identity, stored in a signed session cookie.

The cookie carries a random opaque id and nothing else. It must be minted during an
HTTP request (page load) -- Socket.IO handlers get a read-only copy of the session, so
they can read the id but cannot create one.
"""

import uuid

from flask import session

PID_KEY = "pid"


def current_pid() -> str:
    """Return this browser's player id, minting one into the session if needed."""
    pid = session.get(PID_KEY)
    if not pid:
        pid = uuid.uuid4().hex
        session[PID_KEY] = pid
        session.permanent = False
    return pid


def pid_from_socket() -> str | None:
    """Read the player id inside a Socket.IO handler. None if the cookie is missing."""
    return session.get(PID_KEY)
