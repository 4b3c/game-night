"""Production entry point: `gunicorn ... wsgi:app`.

The monkey-patch has to happen before anything else is imported. It makes the standard
library's sockets, threads and locks cooperative, which is what lets the room lock in
app/games/gah/rooms.py and the ticker in ticker.py work under greenlets.
"""

from gevent import monkey  # isort: skip

monkey.patch_all()

import os  # noqa: E402

os.environ.setdefault("GN_ASYNC_MODE", "gevent")

from app import create_app  # noqa: E402
from app.extensions import socketio  # noqa: E402

app = create_app()

__all__ = ["app", "socketio"]
