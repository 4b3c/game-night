# Deploying Game Night on a VPS

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

## Swapping in your own GIFs

Without rebuilding the image:

```bash
mkdir -p gifs && cp /wherever/*.gif gifs/          # your files
docker compose exec app python scripts/scan_gifs.py  # or run it on the host copy
```

then uncomment the `volumes:` block in `docker-compose.yml` (pointing at your folder) and
`docker compose up -d`. Aim for 60+ GIFs so eight hands of seven don't recycle constantly.
Same idea for prompts: edit `app/data/prompts.json` and redeploy.

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
