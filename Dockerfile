# Python 3.12 on purpose: gevent has prebuilt wheels for it, so the image needs no
# compiler. (Locally the app runs on whatever you have — 3.13+ included — using
# Socket.IO's threading mode instead.)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GN_ASYNC_MODE=gevent

WORKDIR /app

# Dependencies first, so editing the game doesn't reinstall them on every build.
COPY requirements.txt requirements-prod.txt ./
RUN pip install -r requirements-prod.txt

COPY . .

# The manifest is generated, not committed: build it from whatever GIFs are in the image.
# At runtime a mounted GIF folder (see DEPLOY.md) brings its own manifest and wins.
RUN python scripts/scan_gifs.py

# Run as a normal user. The app writes nothing to disk — games live in memory.
RUN useradd --create-home --uid 10001 gamenight \
    && chown -R gamenight:gamenight /app
USER gamenight

EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5050/healthz', timeout=3).status == 200 else 1)"

# One worker, deliberately. Rooms are in this process's memory, so a second worker would
# hand players with the same code two different games. A single gevent worker handles
# hundreds of idle sockets fine — this is a party game, not a public service.
CMD ["gunicorn", \
     "--worker-class", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", \
     "--workers", "1", \
     "--bind", "0.0.0.0:5050", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "wsgi:app"]
