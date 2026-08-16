import argparse
import faulthandler
import os
import signal
import socket

from app import create_app
from app.extensions import socketio

app = create_app()

# `kill -USR1 <pid>` dumps every thread's stack without killing the server — handy when a
# game looks stuck and you want to know which thread is sitting on what.
if hasattr(signal, "SIGUSR1"):
    faulthandler.register(signal.SIGUSR1, all_threads=True)


def _lan_ip() -> str:
    """Best-effort local IP so you can read it off the terminal and tell people."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# Not 5000: on macOS, AirPlay Receiver (ControlCenter) already listens on *:5000, so
# binding all interfaces there fails — and it fails exactly when you try to reach the
# game from a phone. System Settings > General > AirDrop & Handoff can free it, but it's
# simpler to sit somewhere else.
DEFAULT_PORT = 5050

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Game Night")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0 so phones on your wifi can reach it)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.host == "0.0.0.0":
        print(f"\n  🎲 Game Night")
        print(f"      this computer:      http://localhost:{args.port}")
        print(f"      phones on the wifi: http://{_lan_ip()}:{args.port}\n")

    try:
        # use_reloader stays off: the reloader would run two processes and the games live
        # in this process's memory.
        socketio.run(app, host=args.host, port=args.port, debug=args.debug, use_reloader=False, allow_unsafe_werkzeug=True)
    except OSError as exc:
        if exc.errno not in (48, 98):  # EADDRINUSE on macOS / Linux
            raise
        print(f"\n  ✖ Port {args.port} is already in use.")
        if args.port == 5000:
            print("    On macOS that's usually AirPlay Receiver. Try: python run.py --port 5050")
        else:
            print(f"    Something else is on it — try: python run.py --port {args.port + 1}")
        raise SystemExit(1)
