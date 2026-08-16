#!/usr/bin/env bash
# Send your curated GIFs (and their manifest) to the server, then pick them up.
#
#   GAH_HOST=root@your-server ./scripts/push_gifs.sh
#   GAH_HOST=root@your-server GAH_PATH=/opt/game-night ./scripts/push_gifs.sh
#
# Curated GIFs deliberately stay out of git: they came from someone else's API, and a
# public repo is not the place to redistribute them. rsync is the path instead.
#
# The server needs a docker-compose.override.yml (once) so the container reads the GIF
# folder from disk instead of the copy baked into the image:
#
#   services:
#     app:
#       volumes:
#         - ./app/static/gifs:/app/app/static/gifs:ro
#
# Without it you'd have to rebuild the image after every batch.
set -euo pipefail

HOST="${GAH_HOST:-}"
REMOTE_PATH="${GAH_PATH:-/opt/game-night}"

if [[ -z "$HOST" ]]; then
  echo "Set GAH_HOST, e.g.  GAH_HOST=root@your-server $0" >&2
  exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_GIFS="$HERE/app/static/gifs/"

count=$(find "$LOCAL_GIFS" -maxdepth 1 -name '*.gif' | wc -l | tr -d ' ')
echo "  sending $count gifs + manifest.json to $HOST:$REMOTE_PATH/app/static/gifs/"

# --delete so GIFs you removed locally also disappear there; the manifest travels with
# them so the two never disagree.
rsync -az --delete --info=stats1 \
  --include='*.gif' --include='manifest.json' --exclude='*' \
  "$LOCAL_GIFS" "$HOST:$REMOTE_PATH/app/static/gifs/"

echo "  restarting the game"
ssh "$HOST" "cd '$REMOTE_PATH' && docker compose up -d && sleep 5 && curl -s localhost:5050/healthz && echo"
echo "  done — new cards are live (any game in progress ended)"
