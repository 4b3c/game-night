# Deploying Gifs Against Humanity on a VPS

One container, published on `127.0.0.1:5050`. Your existing reverse proxy terminates TLS
and forwards to it. Nothing is exposed to the internet directly.

## 1. Get it running

```bash
git clone https://github.com/4b3c/game-night.git
cd game-night
cp .env.example .env
nano .env                      # set SECRET_KEY and GIPHY_API_KEY; leave the rest for now
python3 scripts/rehydrate_gifs.py   # pull down the cards library.json describes
docker compose up -d --build
curl localhost:5050/healthz    # {"players":0,"rooms":0,"status":"ok"}
```

The rehydrate step matters: the repo describes the deck but doesn't carry the GIF files,
so without it the lobby will grey out every mode.

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

The GIF files aren't in git — `curation/library.json` is, and
`python scripts/rehydrate_gifs.py` rebuilds the folder from it (see the README). On the
server that's the easiest way to fill a fresh checkout; after that, curate on the server
directly and the files never need to move at all.

The image ships the manifest but no GIFs, so the container reads both the GIF folder and
`curation/` from disk — `docker-compose.yml` mounts them, nothing extra to set up. The
curation mount is writable because the game records which cards were played and won there
at the end of every round.

To send a batch up from your laptop instead:

```bash
GAH_HOST=root@your-server ./scripts/push_gifs.sh
```

It rsyncs `app/static/gifs/` (GIFs + manifest) and restarts the container. The override
file is gitignored, so `git pull` won't fight it. Aim for 56 GIFs per mode — eight players
hold 56 cards at once, and the lobby greys out modes that aren't there yet.

Prompts travel in git too, in `curation/prompts.json` — but they no longer need a deploy
to change: the curator writes that file and the game re-reads it when it changes, so a
prompt written on the server is in the next round. Commit it when you next pull, the same
as `library.json`.

## The curator, published for friends

The curator runs on the server, writing straight into the folders the game reads — tag a
card or write a prompt and it's in the next game, no rsync, no restart (the app re-reads
both the manifest and `curation/prompts.json` when they change). It lives at
`https://your.domain/curate`, behind a shared password, so you can hand the link to
whoever is helping you build the deck.

```bash
cd /opt/game-night

# in .env (gitignored):
CURATOR_PASSWORD=four-random-words-is-plenty   # REQUIRED — it won't start without one
CURATOR_BIND=127.0.0.1                         # nginx reaches it; the internet doesn't
CURATOR_PREFIX=/curate
GIPHY_API_KEY=...                              # curator and rehydrate; the game itself doesn't use it
CURATOR_RATING=r

# both containers write here: the curator saves cards, the game saves round statistics
chown -R 10001:10001 app/static/gifs curation

docker compose --profile curator up -d --build
```

Then add this to the **same** `server {}` block as the game, above `location /`:

```nginx
    # The deck curator. Password-gated by the app itself; the trailing-slash-free
    # proxy_pass plus X-Forwarded-Prefix is what makes its own links come back here
    # instead of landing on the game.
    location /curate {
        proxy_pass http://127.0.0.1:5099;
        proxy_http_version 1.1;

        proxy_set_header Host               $host;
        proxy_set_header X-Real-IP          $remote_addr;
        proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  $scheme;
        proxy_set_header X-Forwarded-Prefix /curate;

        # Resolving a pasted link means fetching someone else's page first.
        proxy_read_timeout 120s;
        client_max_body_size 1m;
    }
```

`sudo nginx -t && sudo systemctl reload nginx`, then check the gate actually holds:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://your.domain/curate/          # 302 -> login
curl -s -o /dev/null -w '%{http_code}\n' https://your.domain/curate/api/library  # 401
ss -tlnp | grep 5099        # must show 127.0.0.1:5099 only — never 0.0.0.0
```

Wrong guesses are throttled to five a minute per address, which is plenty against a script
and forgiving of someone fat-fingering a passphrase on a phone.

Stop it when nobody's curating — no reason to leave it up:

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
