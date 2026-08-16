#!/usr/bin/env python3
"""Swipe through real GIFs and sort them into the game's modes.

A tiny local web app: one big GIF at a time, four keys — skip, or keep it for Normal,
18+ or Millennial mode. Everything you keep is downloaded straight into the game's GIF
folder and tagged in its manifest, so the cards are live the next time the game starts.

Each mode deals only from its own pile, so a mode needs 56 GIFs before eight people can
play it (28 for four). The counters in the corner tell you where each one stands.

    export GIPHY_API_KEY=xxxxxxxx          # see --help for where to get one
    python scripts/curate_gifs.py
    # then open http://127.0.0.1:5099

    python scripts/curate_gifs.py --source tenor --rating pg-13
    python scripts/curate_gifs.py --queries "facepalm,slow clap,confetti"

Keys:  →/D Normal    ←/A skip    ↑/W 18+    ↓/S Millennial    U undo

Decisions live in curation/decisions.json, so nothing you've already judged comes back —
you can stop and resume whenever. Nothing is deleted: skipping just means "not for me".
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).resolve().parent.parent
GIF_DIR = ROOT / "app" / "static" / "gifs"
MANIFEST = GIF_DIR / "manifest.json"
STATE_DIR = ROOT / "curation"
DECISIONS = STATE_DIR / "decisions.json"

# Search terms that tend to produce good reaction GIFs — the kind of thing that works as
# an answer to a prompt. Override with --queries.
DEFAULT_QUERIES = [
    "reaction", "shrug", "facepalm", "panic", "awkward", "confused", "shocked",
    "eye roll", "slow clap", "nervous", "dancing badly", "fail", "smug", "crying",
    "screaming", "celebration", "confetti", "thumbs up", "disgusted", "suspicious",
    "sleeping", "chaos", "explosion", "dog", "cat", "baby", "grandma", "office",
    "sports fail", "wink", "mic drop", "sparkle", "angry", "bored", "excited",
    "disappointed", "hungry", "rain", "money", "cool", "oops", "hug", "kiss",
    "high five", "dance floor", "surprised", "unimpressed", "laughing", "wave",
]

USER_AGENT = "gifs-against-humanity-curator/1.0 (local curation tool)"

# A verdict maps to the `rating` tag in the manifest, which is what decides the mode a
# card belongs to (see app/games/gah/decks.py MODES). "skip" keeps no file.
VERDICT_RATINGS = {"keep": "sfw", "adult": "adult", "millennial": "millennial"}
VERDICTS = tuple(VERDICT_RATINGS) + ("skip",)


# --- sources -------------------------------------------------------------------
class GiphySource:
    name = "giphy"
    key_env = "GIPHY_API_KEY"
    key_help = (
        "developers.giphy.com -> Create an Account -> Create an App -> pick 'API' "
        "(not SDK) -> copy the API Key"
    )
    ratings = ("g", "pg", "pg-13", "r")

    def __init__(self, api_key: str, rating: str):
        self.api_key = api_key
        self.rating = rating

    def fetch(self, query: str, offset: int) -> list[dict]:
        params = {
            "api_key": self.api_key,
            "limit": 50,
            "offset": offset,
            "rating": self.rating,
            "lang": "en",
        }
        if query:
            params["q"] = query
            url = "https://api.giphy.com/v1/gifs/search?" + urllib.parse.urlencode(params)
        else:
            url = "https://api.giphy.com/v1/gifs/trending?" + urllib.parse.urlencode(params)
        payload = _get_json(url)
        out = []
        for item in payload.get("data", []):
            media = _pick_giphy_rendition(item.get("images", {}))
            if not media:
                continue
            out.append(
                {
                    "source_id": f"giphy:{item['id']}",
                    "title": (item.get("title") or "").strip(),
                    "preview": media,
                    "download": media,
                    "page": item.get("url"),
                    "query": query or "trending",
                }
            )
        return out


class TenorSource:
    name = "tenor"
    key_env = "TENOR_API_KEY"
    key_help = (
        "console.cloud.google.com -> enable the 'Tenor API' -> Credentials -> "
        "Create API key"
    )
    ratings = ("high", "medium", "low", "off")

    def __init__(self, api_key: str, rating: str):
        self.api_key = api_key
        # Tenor calls it contentfilter; map the giphy-ish words onto it if needed.
        self.rating = {"g": "high", "pg": "medium", "pg-13": "low", "r": "off"}.get(rating, rating)

    def fetch(self, query: str, offset: int) -> list[dict]:
        params = {
            "key": self.api_key,
            "limit": 50,
            "pos": offset,
            "contentfilter": self.rating,
            "media_filter": "gif,tinygif",
            "client_key": "gah_curator",
        }
        if query:
            params["q"] = query
            url = "https://tenor.googleapis.com/v2/search?" + urllib.parse.urlencode(params)
        else:
            url = "https://tenor.googleapis.com/v2/featured?" + urllib.parse.urlencode(params)
        payload = _get_json(url)
        out = []
        for item in payload.get("results", []):
            formats = item.get("media_formats", {})
            media = (formats.get("gif") or {}).get("url") or (formats.get("tinygif") or {}).get("url")
            if not media:
                continue
            out.append(
                {
                    "source_id": f"tenor:{item['id']}",
                    "title": (item.get("content_description") or "").strip(),
                    "preview": media,
                    "download": media,
                    "page": item.get("itemurl"),
                    "query": query or "featured",
                }
            )
        return out


SOURCES = {"giphy": GiphySource, "tenor": TenorSource}


def _get_json(url: str) -> dict:
    request_obj = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request_obj, timeout=20) as response:
        return json.loads(response.read())


def _pick_giphy_rendition(images: dict) -> str | None:
    """Big enough to look good on a phone card, small enough to load instantly."""
    for name in ("downsized", "fixed_width", "downsized_medium", "fixed_height", "original"):
        candidate = images.get(name) or {}
        url = candidate.get("url")
        if not url:
            continue
        try:
            size = int(candidate.get("size") or 0)
        except ValueError:
            size = 0
        if size and size > 3_000_000:
            continue
        return url.split("?")[0]
    return None


# --- persistence ---------------------------------------------------------------
class Library:
    """The decisions file plus the game's GIF folder and manifest."""

    def __init__(self):
        self.lock = threading.RLock()
        STATE_DIR.mkdir(exist_ok=True)
        GIF_DIR.mkdir(parents=True, exist_ok=True)
        self.decisions: dict[str, dict] = {}
        if DECISIONS.exists():
            self.decisions = json.loads(DECISIONS.read_text())

    # -- decisions --------------------------------------------------------
    def seen(self, source_id: str) -> bool:
        return source_id in self.decisions

    def counts(self) -> dict:
        verdicts = [d["verdict"] for d in self.decisions.values()]
        by_rating: dict[str, int] = {}
        for entry in self._manifest_entries():
            rating = entry.get("rating", "sfw")
            by_rating[rating] = by_rating.get(rating, 0) + 1
        return {
            "normal": by_rating.get("sfw", 0),
            "adult": by_rating.get("adult", 0),
            "millennial": by_rating.get("millennial", 0),
            "skipped": verdicts.count("skip"),
            "total": len(verdicts),
            "in_game": len(self._manifest_entries()),
            # 8 players x 7 cards: what a mode needs before a full table can play it
            "target": 56,
        }

    def _save_decisions(self) -> None:
        DECISIONS.write_text(json.dumps(self.decisions, indent=2, sort_keys=True) + "\n")

    def record(self, candidate: dict, verdict: str) -> dict:
        """Apply a verdict. Keeps download the file and join the manifest."""
        with self.lock:
            entry = {
                "verdict": verdict,
                "title": candidate.get("title", ""),
                "query": candidate.get("query", ""),
                "page": candidate.get("page"),
            }
            if verdict in VERDICT_RATINGS:
                filename = self._download(candidate)
                entry["file"] = filename
                self._add_to_manifest(
                    filename=filename,
                    label=_pretty_label(candidate.get("title"), filename),
                    rating=VERDICT_RATINGS[verdict],
                    source=candidate["source_id"],
                )
            self.decisions[candidate["source_id"]] = entry
            self._save_decisions()
            return entry

    def undo(self, source_id: str) -> bool:
        """Take back the last verdict, removing the file and manifest entry."""
        with self.lock:
            entry = self.decisions.pop(source_id, None)
            if entry is None:
                return False
            filename = entry.get("file")
            if filename:
                target = GIF_DIR / filename
                if target.exists():
                    target.unlink()
                self._remove_from_manifest(filename)
            self._save_decisions()
            return True

    # -- files and manifest ----------------------------------------------
    def _download(self, candidate: dict) -> str:
        slug = _slugify(candidate.get("title") or candidate["query"] or "gif")
        unique = candidate["source_id"].split(":", 1)[1][:8]
        filename = f"{slug}-{unique}.gif"
        target = GIF_DIR / filename
        request_obj = urllib.request.Request(candidate["download"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request_obj, timeout=30) as response:
            data = response.read()
        target.write_bytes(data)
        return filename

    def _manifest(self) -> dict:
        if MANIFEST.exists():
            try:
                return json.loads(MANIFEST.read_text())
            except json.JSONDecodeError:
                pass
        return {"generated": False, "count": 0, "gifs": []}

    def _manifest_entries(self) -> list[dict]:
        return self._manifest().get("gifs", [])

    def _write_manifest(self, entries: list[dict]) -> None:
        MANIFEST.write_text(
            json.dumps({"generated": False, "count": len(entries), "gifs": entries}, indent=2) + "\n"
        )

    def _add_to_manifest(self, *, filename: str, label: str, rating: str, source: str) -> None:
        entries = [e for e in self._manifest_entries() if e.get("file") != filename]
        entries.append(
            {
                "id": Path(filename).stem,
                "file": filename,
                "label": label,
                "rating": rating,
                "source": source,
            }
        )
        self._write_manifest(entries)

    def _remove_from_manifest(self, filename: str) -> None:
        self._write_manifest([e for e in self._manifest_entries() if e.get("file") != filename])

    def drop_filler(self) -> int:
        """Delete the generated placeholder cards once you have real ones."""
        with self.lock:
            removed = 0
            for path in sorted(GIF_DIR.glob("gif_[0-9][0-9][0-9].gif")):
                path.unlink()
                removed += 1
            entries = [
                e for e in self._manifest_entries()
                if not re.fullmatch(r"gif_\d{3}\.gif", e.get("file", ""))
            ]
            self._write_manifest(entries)
            return removed


def _slugify(text: str) -> str:
    text = re.sub(r"\bGIF\b", "", text or "", flags=re.I)
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text or "gif")[:40]


def _pretty_label(title: str | None, filename: str) -> str:
    cleaned = re.sub(r"\bGIF\b", "", title or "", flags=re.I).strip()
    if cleaned:
        return cleaned[:60]
    return Path(filename).stem.replace("-", " ").title()


# --- candidate queue -----------------------------------------------------------
class Queue:
    """Keeps a pool of undecided candidates, refilling from the API in batches.

    Batches matter: a free Giphy key allows ~100 calls an hour, but each call returns up
    to 50 GIFs — so one call is a couple of minutes of swiping.
    """

    def __init__(self, source, library: Library, queries: list[str]):
        self.source = source
        self.library = library
        self.queries = queries
        self.pool: list[dict] = []
        self.lock = threading.RLock()
        self.last_error: str | None = None
        self.calls = 0

    def take(self, count: int) -> list[dict]:
        out = []
        for _ in range(count):
            candidate = self._next_one()
            if candidate is None:
                break
            out.append(candidate)
        return out

    def _next_one(self) -> dict | None:
        with self.lock:
            for _ in range(6):
                while self.pool:
                    candidate = self.pool.pop()
                    if not self.library.seen(candidate["source_id"]):
                        return candidate
                if not self._refill():
                    return None
            return None

    def _refill(self) -> bool:
        query = random.choice(self.queries) if self.queries else ""
        # A random offset is what makes this feel like a firehose rather than the same
        # 50 trending GIFs every time.
        offset = random.randrange(0, 500)
        try:
            batch = self.source.fetch(query, offset)
            self.calls += 1
            self.last_error = None
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode()[:200]
            except Exception:  # noqa: BLE001
                pass
            if exc.code in (401, 403):
                self.last_error = f"The API rejected the key ({exc.code}). Check {self.source.key_env}. {body}"
            elif exc.code == 429:
                self.last_error = "Rate limited by the API — wait a minute and keep going."
            else:
                self.last_error = f"API error {exc.code}: {body}"
            return False
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        random.shuffle(batch)
        self.pool.extend(batch)
        return bool(batch)


# --- web app -------------------------------------------------------------------
def build_app(queue: Queue, library: Library, source) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(ROOT / "app" / "static"),
        static_url_path="/static",
    )
    app.config["JSON_SORT_KEYS"] = False
    recent: list[str] = []

    @app.route("/")
    def index():
        return render_template(
            "curate.html",
            source=source.name,
            rating=source.rating,
            counts=library.counts(),
        )

    @app.get("/api/next")
    def api_next():
        count = max(1, min(8, int(request.args.get("n", 4))))
        candidates = queue.take(count)
        return jsonify(
            candidates=candidates,
            counts=library.counts(),
            error=queue.last_error,
        )

    @app.post("/api/decide")
    def api_decide():
        data = request.get_json(silent=True) or {}
        verdict = data.get("verdict")
        candidate = data.get("candidate") or {}
        if verdict not in VERDICTS or not candidate.get("source_id"):
            return jsonify(error="bad request"), 400
        try:
            library.record(candidate, verdict)
        except Exception as exc:  # noqa: BLE001 — a failed download shouldn't kill the app
            return jsonify(error=f"could not save that one: {exc}"), 502
        recent.append(candidate["source_id"])
        del recent[:-50]
        return jsonify(ok=True, counts=library.counts())

    @app.post("/api/undo")
    def api_undo():
        if not recent:
            return jsonify(error="nothing to undo"), 400
        source_id = recent.pop()
        library.undo(source_id)
        return jsonify(ok=True, counts=library.counts())

    @app.post("/api/drop-filler")
    def api_drop_filler():
        removed = library.drop_filler()
        return jsonify(ok=True, removed=removed, counts=library.counts())

    return app


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Where to get a key:\n"
        f"  giphy: {GiphySource.key_help}\n"
        f"  tenor: {TenorSource.key_help}\n",
    )
    parser.add_argument("--source", choices=sorted(SOURCES), default="giphy")
    parser.add_argument("--api-key", default=None, help="defaults to $GIPHY_API_KEY / $TENOR_API_KEY")
    parser.add_argument(
        "--rating",
        default="pg-13",
        help="giphy: g, pg, pg-13, r (default pg-13). tenor: high, medium, low, off",
    )
    parser.add_argument("--queries", default=None, help="comma-separated search terms to draw from")
    parser.add_argument("--port", type=int, default=5099)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    source_class = SOURCES[args.source]
    api_key = args.api_key or os.environ.get(source_class.key_env, "").strip()
    if not api_key:
        print(f"\n  No API key. Set {source_class.key_env} (or pass --api-key).\n")
        print(f"  How to get one:\n    {source_class.key_help}\n")
        return 2

    queries = (
        [q.strip() for q in args.queries.split(",") if q.strip()]
        if args.queries
        else list(DEFAULT_QUERIES)
    )
    source = source_class(api_key, args.rating)
    library = Library()
    queue = Queue(source, library, queries)

    counts = library.counts()
    print(f"\n  🃏 GIF curator — {args.source}, rating {source.rating}")
    print(f"      open:     http://{args.host}:{args.port}")
    print(f"      keeping:  {GIF_DIR}")
    print(
        f"      so far:   {counts['normal']} normal, {counts['adult']} 18+, "
        f"{counts['millennial']} millennial, {counts['skipped']} skipped"
    )
    print("      keys:     → normal   ← skip   ↑ 18+   ↓ millennial   U undo\n")

    app = build_app(queue, library, source)
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
