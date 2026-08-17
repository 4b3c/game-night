# Deploying Gifs Against Humanity on a VPS

One container, published on `127.0.0.1:5050`. Your existing reverse proxy terminates TLS
and forwards to it. Nothing is exposed to the internet directly.

## 1. Get it running

```bash
git clone https://github.com/4b3c/game-night.git
cd game-night
cp .env.example .env
nano .env                      # set SECRET_KEY (see below); leave the rest for now
docker compose up -d --build
curl localhost:5050/healthz    # {"players":0,"rooms":0,"status":"ok"}
```

Generate a secret key with `openssl rand -hex 32`. Without one the app still runs, but a
new key is minted on every restart, which logs everyone out.

The first build takes a couple of minutes. Later deploys are:

```bash
git pull && docker compose up -d --build
```

## 2. Point your domain at it

Add a DNS **A record** for the hostname you want (`game.example.com`) pointing at the VPS's
IP, then add one of these to your proxy.

### nginx

The two `Upgrade`/`Connection` lines are what make websockets work — without them the game
falls back to slow HTTP polling, and reveals feel laggy.

```nginx
server {
    listen 443 ssl http2;
    server_name game.example.com;

    # however you already manage certs (certbot, etc.)
    ssl_certificate     /etc/letsencrypt/live/game.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/game.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_http_version 1.1;

        # websockets
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";

        # so the app knows the real scheme and host
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # a game socket stays open for the whole night
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering    off;
    }
}

server {
    listen 80;
    server_name game.example.com;
    return 301 https://$host$request_uri;
}
```

`sudo nginx -t && sudo systemctl reload nginx`

### Caddy

Caddy handles certificates and websocket upgrades on its own:

```
game.example.com {
    reverse_proxy 127.0.0.1:5050
}
```

### Traefik (labels on the compose service)

Add to the `app` service in `docker-compose.yml`, and put the service on your Traefik
network:

```yaml
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.gamenight.rule=Host(`game.example.com`)"
      - "traefik.http.routers.gamenight.entrypoints=websecure"
      - "traefik.http.routers.gamenight.tls.certresolver=letsencrypt"
      - "traefik.http.services.gamenight.loadbalancer.server.port=5050"
```

## 3. Turn on the Secure cookie

Once `https://game.example.com` loads, and **only** then:

```bash
sed -i 's/GN_COOKIE_SECURE=0/GN_COOKIE_SECURE=1/' .env
docker compose up -d
```

While that's on, browsers refuse the session cookie over plain http — so if you flip it too
early, players get stuck on the join screen forever. The app logs a warning at startup as a
reminder.

## Day-to-day

```bash
docker compose logs -f app            # watch
docker compose restart app            # restart (ends games in progress)
docker compose down                   # stop
curl localhost:5050/healthz           # rooms and player counts
docker compose exec app python -c "print('alive')"
```

Everything lives in memory, so **restarting or redeploying ends any game in progress**.
Deploy between rounds, not during them. There's nothing to back up.

## Settings

All optional — every one has a working default. See `.env.example`.

| Variable | Default | What it does |
|---|---|---|
| `SECRET_KEY` | random each boot | Signs the player cookie. Set it so restarts don't log everyone out. |
| `GN_BEHIND_PROXY` | `1` | Trust one hop of `X-Forwarded-*`. Keep on behind a proxy; turn off if exposed directly. |
| `GN_COOKIE_SECURE` | `0` | Mark the cookie Secure. Turn on after HTTPS works. |
| `GN_ROOM_IDLE_TIMEOUT` | `600` | Seconds a room survives after the last person leaves. |
| `HOST_PORT` | `5050` | Host port the container publishes on, bound to localhost. |

## Getting your curated GIFs onto the server

Curate locally (`python scripts/curate_gifs.py` — see the README), then push. One-time
setup on the server so the container reads GIFs from disk instead of the copy baked into
the image:

```bash
cat > /opt/game-night/docker-compose.override.yml <<'YAML'
services:
  app:
    volumes:
      - ./app/static/gifs:/app/app/static/gifs:ro
YAML
docker compose up -d
```

After that, every batch is one command from your laptop:

```bash
GAH_HOST=root@your-server ./scripts/push_gifs.sh
```

It rsyncs `app/static/gifs/` (GIFs + manifest) and restarts the container. The override
file is gitignored, so `git pull` won't fight it. Aim for 56 GIFs per mode — eight players
hold 56 cards at once, and the lobby greys out modes that aren't there yet.

Prompts travel in git: edit `app/data/prompts.json`, commit, pull, `docker compose up -d
--build`.

## Curating from your phone (Tailscale only)

The curator can run on the server, writing straight into the folder the game reads — tag a
card on your phone and it's in the next game, no rsync, no restart (the app re-reads the
manifest when it changes).

It has **no login**, so the only thing protecting it is what address it listens on. Bind it
to the box's Tailscale address and it's reachable from your own devices and nothing else:

```bash
cd /opt/game-night
tailscale ip -4                                    # e.g. 100.117.22.95

# in .env (gitignored):
CURATOR_BIND=100.117.22.95     # your Tailscale address — NEVER 0.0.0.0 here
GIPHY_API_KEY=...              # curator only; the game doesn't use it
CURATOR_MIN_FRAMES=10
CURATOR_RATING=r

# the folders the curator writes to must be writable by the container user
chown -R 10001:10001 app/static/gifs curation

docker compose --profile curator up -d --build
```

Then open `http://100.117.22.95:5099` on any device on your tailnet. Check the outside can't:

```bash
curl -m 5 http://YOUR.PUBLIC.IP:5099/     # must fail (000 / connection refused)
ss -tlnp | grep 5099                      # must show only 100.x.y.z:5099
```

Stop it when you're done curating — no reason to leave it up:

```bash
docker compose --profile curator stop curator
```

The curator mounts `scripts/` read-only, so tweaking its UI needs a `restart`, not a rebuild.
The game deliberately doesn't do that: it runs exactly what was built.

## Sizing

A single gevent worker on the smallest VPS is plenty: one game is 8 sockets exchanging a
few KB per action. The GIFs are static files (~35 KB each) served by Flask — if you ever run
several loud tables at once, hand `/static/` to nginx directly:

```nginx
    location /static/ {
        alias /home/you/game-night/app/static/;
        expires 7d;
    }
```

## Troubleshooting

**Everyone stuck on the join screen.** `GN_COOKIE_SECURE=1` while the site is being reached
over http. Set it to 0, `docker compose up -d`.

**Reveals feel laggy.** Websockets aren't getting through — the `Upgrade`/`Connection`
headers are missing from the proxy config, so Socket.IO fell back to polling. Confirm with
devtools → Network → WS.

**"unsafe port" in a browser after changing `HOST_PORT`.** Browsers block a handful of
ports (5060 is SIP, for example). Pick another; 5050 is fine.

**Port already in use.** Something else holds `HOST_PORT`. Change it in `.env` and point
your proxy at the new one.

**Two players entered the same code and got different games.** Only ever run one worker —
rooms live in one process's memory. Don't add `--workers 2`.

**You deployed a fix but players still see the old behaviour.** A CDN in front (Cloudflare,
say) caches CSS and JS aggressively — `cf-cache-status: HIT` with an `age` of hours means
it's serving the copy from before your deploy. Every CSS/JS URL therefore carries a
fingerprint (`/static/js/gah-play.js?v=9d58d51e4f`) derived from the file itself: change the
file and the URL changes, so the stale copy is never requested again and the fix lands with
no purge. Check it's working with:

```bash
curl -s https://your.domain/ | grep -o 'gah-play.js?v=[a-f0-9]*'
```

The **GIFs** are the exception — the client builds those URLs from a plain prefix, so they
have no fingerprint. That's the right trade for 80 files that never change, but it means
replacing a GIF *while keeping its filename* will keep serving the cached picture. Use new
filenames (real GIFs will have their own anyway), or purge that path.
