import os

from flask_socketio import SocketIO

# How Socket.IO does concurrency.
#
#   threading (default)  works everywhere, including Python 3.13+, and needs no
#                        monkey-patching. Real websockets via simple-websocket. This is
#                        what `python run.py` uses locally.
#   gevent               what the Docker image uses under gunicorn. wsgi.py monkey-patches
#                        *before* anything else is imported, which is what makes the
#                        threading.RLock in rooms.py safe under greenlets.
#
# Set GN_ASYNC_MODE to override. Don't switch to gevent without monkey-patching first.
ASYNC_MODE = os.environ.get("GN_ASYNC_MODE", "threading")

socketio = SocketIO(async_mode=ASYNC_MODE)
