#!/usr/bin/env python3
"""Browse GIFs and put them into the game's sets.

A local web app. You scroll with the arrow keys and tag with N / E / M — a GIF can be in
several sets at once, so a cursed one can be in both Normal and 18+.

    python scripts/curate_gifs.py          # then open http://127.0.0.1:5099

    ←  →      previous / next GIF
    N         toggle Normal        (mode "normal")
    E         toggle Eighteen+     (mode "adult")
    M         toggle Millennial    (mode "millennial")
    /         jump to the search box
    R         related tags for what you're looking at — the way to find more like it
    X         remove from every set

Finding the weird stuff: type anything into the search box (`nuke`, `deep fried`, `cursed`),
or press R on a GIF you like and click one of the related tags Giphy suggests. Packs of
starter queries are built in — try `--pack cursed`.

Your own GIFs: drag files onto the page. They're copied in and tagged like anything else,
so the ones you already collected in Discord can go straight into a set.

Low-frame GIFs (near-stills, 4-frame stutters) are filtered out before you see them;
`--min-frames` sets the bar.

Nothing is deleted that you didn't untag: GIFs you scroll past are remembered as seen so
they don't come round again, and `curation/decisions.json` keeps the lot between sessions.

Where to get an API key:
  giphy: developers.giphy.com -> Create an Account -> Create an App -> pick "API"
  tenor: console.cloud.google.com -> enable the "Tenor API" -> Credentials -> API key
Put it in .env as GIPHY_API_KEY=... (gitignored) or pass --api-key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
GIF_DIR = ROOT / "app" / "static" / "gifs"
MANIFEST = GIF_DIR / "manifest.json"
STATE_DIR = ROOT / "curation"
DECISIONS = STATE_DIR / "decisions.json"
IMPORTED = STATE_DIR / "imported"

# The sets a GIF can belong to. These names are the mode ids in app/games/gah/decks.py.
SETS = {
    "normal": {"key": "N", "label": "Normal"},
    "adult": {"key": "E", "label": "Eighteen+"},
    "millennial": {"key": "M", "label": "Millennial"},
}
CARDS_PER_MODE = 56  # 8 players x 7 cards: what a mode needs to be playable

QUERY_PACKS = {
    "reactions": [
        "reaction", "shrug", "facepalm", "panic", "awkward", "confused", "shocked",
        "eye roll", "slow clap", "nervous", "fail", "smug", "crying", "screaming",
        "celebration", "thumbs up", "disgusted", "suspicious", "sleeping", "bored",
        "excited", "disappointed", "unimpressed", "laughing", "wave", "wink",
    ],
    "cursed": [
        "cursed", "cursed image", "wtf", "unsettling", "deep fried", "deep fried meme",
        "chaotic", "unhinged", "shitpost", "surreal", "fever dream", "glitch",
        "distorted", "gremlin", "no context", "why", "help", "eldritch", "haunted",
        "creature", "goofy", "brainrot", "explosion", "nuke", "gas leak", "on fire",
        "cat scream", "possessed", "melting", "vhs horror", "liminal", "backrooms",
    ],
    "millennial": [
        "2012", "vine", "harlem shake", "doge", "rage comic", "myspace", "emo",
        "tumblr", "nyan cat", "trollface", "dial up", "flip phone", "blockbuster",
        "vhs", "windows xp", "msn", "clippy", "shrek", "spongebob", "office space",
        "the office", "parks and rec", "arrested development", "napoleon dynamite",
    ],
    "chaos": [
        "chaos", "destruction", "car crash", "falling over", "wipeout", "faceplant",
        "flip table", "food fight", "firework fail", "trampoline", "waterslide",
        "sprinkler", "goat scream", "seagull", "raccoon", "possum", "capybara",
    ],
}

USER_AGENT = "gifs-against-humanity-curator/2.0 (local curation tool)"


# --- sources -------------------------------------------------------------------
class GiphySource:
    name = "giphy"
    key_env = "GIPHY_API_KEY"
    key_help = "developers.giphy.com -> Create an App -> pick 'API' -> copy the API Key"

    # A card is ~400px wide on a phone (800 physical) and a hand of seven loads at once,
    # often on mobile data: aim mid-sized rather than taking whatever is listed first.
    TARGET_WIDTH = 480
    MAX_BYTES = 1_500_000

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
        out = []
        for item in _get_json(url).get("data", []):
            images = item.get("images", {})
            media = self._rendition(images)
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
                    # Giphy reports frame count on the original rendition; it's the one
                    # reliable way to drop near-stills and 4-frame stutters.
                    "frames": _as_int((images.get("original") or {}).get("frames")),
                }
            )
        return out

    def related_tags(self, term: str) -> list[str]:
        if not term:
            return []
        url = f"https://api.giphy.com/v1/tags/related/{urllib.parse.quote(term)}?api_key={self.api_key}"
        try:
            return [t["name"] for t in _get_json(url).get("data", []) if t.get("name")][:14]
        except Exception:  # noqa: BLE001 — discovery is a nicety, never fatal
            return []

    def _rendition(self, images: dict) -> str | None:
        """The rendition closest to card size that still downloads quickly.

        Downsampled renditions are penalised: they get their size by dropping frames,
        which is the opposite of what we're filtering for.
        """
        options = []
        for name, data in (images or {}).items():
            if "still" in name or not isinstance(data, dict):
                continue
            url = data.get("url")
            if not url or ".gif" not in url.split("?")[0]:
                continue  # skips mp4/webp renditions
            width = _as_int(data.get("width"))
            size = _as_int(data.get("size"))
            penalty = 150 if "downsampled" in name else 0
            options.append({"url": url.split("?")[0], "width": width, "size": size, "penalty": penalty})
        if not options:
            return None
        affordable = [o for o in options if o["size"] and o["size"] <= self.MAX_BYTES] or options
        best = min(
            affordable,
            key=lambda o: (abs((o["width"] or 9999) - self.TARGET_WIDTH) + o["penalty"], o["size"] or 10**9),
        )
        return best["url"]


class TenorSource:
    name = "tenor"
    key_env = "TENOR_API_KEY"
    key_help = "console.cloud.google.com -> enable 'Tenor API' -> Credentials -> API key"

    def __init__(self, api_key: str, rating: str):
        self.api_key = api_key
        self.rating = {"g": "high", "pg": "medium", "pg-13": "low", "r": "off"}.get(rating, rating)

    def fetch(self, query: str, offset: int) -> list[dict]:
        params = {
            "key": self.api_key,
            "limit": 50,
            "pos": offset,
            "contentfilter": self.rating,
            "media_filter": "gif,mediumgif,tinygif",
            "client_key": "gah_curator",
        }
        if query:
            params["q"] = query
            url = "https://tenor.googleapis.com/v2/search?" + urllib.parse.urlencode(params)
        else:
            url = "https://tenor.googleapis.com/v2/featured?" + urllib.parse.urlencode(params)
        out = []
        for item in _get_json(url).get("results", []):
            formats = item.get("media_formats", {})
            media = (formats.get("mediumgif") or formats.get("gif") or formats.get("tinygif") or {}).get("url")
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
                    # Tenor doesn't publish frame counts; 0 means "unknown", which the
                    # frame filter lets through rather than discarding everything.
                    "frames": 0,
                }
            )
        return out

    def related_tags(self, term: str) -> list[str]:
        if not term:
            return []
        url = "https://tenor.googleapis.com/v2/search_suggestions?" + urllib.parse.urlencode(
            {"key": self.api_key, "q": term, "limit": 14, "client_key": "gah_curator"}
        )
        try:
            return list(_get_json(url).get("results", []))[:14]
        except Exception:  # noqa: BLE001
            return []


SOURCES = {"giphy": GiphySource, "tenor": TenorSource}


def load_dotenv() -> None:
    """Read .env, without overriding the real environment. Keeps keys out of git."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _get_json(url: str) -> dict:
    request_obj = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request_obj, timeout=20) as response:
        return json.loads(response.read())


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def count_frames(path: Path) -> int:
    """Frame count of a local GIF, or 0 if Pillow isn't around to look."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            return getattr(image, "n_frames", 1)
    except Exception:  # noqa: BLE001
        return 0


def _slugify(text: str) -> str:
    text = re.sub(r"\bGIF\b", "", text or "", flags=re.I)
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text or "gif")[:40]


def _pretty_label(title: str | None, filename: str) -> str:
    cleaned = re.sub(r"\bGIF\b", "", title or "", flags=re.I).strip()
    return (cleaned or Path(filename).stem.replace("-", " ").title())[:60]


# --- the library ---------------------------------------------------------------
class Library:
    """Decisions, the game's GIF folder, and the manifest that ties them together."""

    def __init__(self):
        self.lock = threading.RLock()
        STATE_DIR.mkdir(exist_ok=True)
        IMPORTED.mkdir(parents=True, exist_ok=True)
        GIF_DIR.mkdir(parents=True, exist_ok=True)
        self.decisions: dict[str, dict] = {}
        if DECISIONS.exists():
            try:
                self.decisions = json.loads(DECISIONS.read_text())
            except json.JSONDecodeError:
                print("⚠️  decisions.json was unreadable — starting a fresh one")
        self._migrate_verdicts()

    def _migrate_verdicts(self) -> None:
        """Carry over decisions made when each GIF had exactly one verdict.

        The old shape was {"verdict": "keep"|"adult"|"millennial"|"skip"}; sets replaced it
        so a GIF can be in several modes at once. Nothing is thrown away — a skip becomes
        "in no sets", which still means it won't come round again.
        """
        legacy = {"keep": ["normal"], "adult": ["adult"], "millennial": ["millennial"], "skip": []}
        changed = 0
        for entry in self.decisions.values():
            if "sets" not in entry and "verdict" in entry:
                entry["sets"] = legacy.get(entry.pop("verdict"), [])
                changed += 1
        if changed:
            self._save()
            print(f"  migrated {changed} earlier decision(s) to the new set tags")

    # -- decisions --------------------------------------------------------
    def seen(self, source_id: str) -> bool:
        return source_id in self.decisions

    def sets_of(self, source_id: str) -> list[str]:
        return list((self.decisions.get(source_id) or {}).get("sets") or [])

    def mark_seen(self, source_id: str, candidate: dict | None = None) -> None:
        with self.lock:
            entry = self.decisions.setdefault(source_id, {"sets": [], "title": (candidate or {}).get("title", "")})
            entry.setdefault("sets", [])
            self._save()

    def counts(self) -> dict:
        per_set = {name: 0 for name in SETS}
        for entry in self._manifest_entries():
            for name in entry.get("sets", []):
                if name in per_set:
                    per_set[name] += 1
        return {
            "sets": per_set,
            "seen": len(self.decisions),
            "tagged": sum(1 for d in self.decisions.values() if d.get("sets")),
            "target": CARDS_PER_MODE,
            "in_game": len(self._manifest_entries()),
        }

    def _save(self) -> None:
        DECISIONS.write_text(json.dumps(self.decisions, indent=2, sort_keys=True) + "\n")

    # -- membership -------------------------------------------------------
    def apply_sets(self, candidate: dict, wanted: list[str]) -> dict:
        """Make this GIF belong to exactly `wanted`.

        Newly in a set -> the file is fetched. Out of every set -> the file goes away
        again. Nothing else about it changes, so toggling is cheap and reversible.
        """
        wanted = [s for s in wanted if s in SETS]
        source_id = candidate["source_id"]
        with self.lock:
            entry = self.decisions.setdefault(source_id, {"sets": []})
            entry["title"] = candidate.get("title", entry.get("title", ""))
            entry["query"] = candidate.get("query", entry.get("query", ""))
            entry["page"] = candidate.get("page", entry.get("page"))
            filename = entry.get("file")

            if wanted and not filename:
                filename = self._download(candidate)
                entry["file"] = filename
            if wanted:
                self._write_manifest_entry(
                    filename=filename,
                    label=_pretty_label(entry.get("title"), filename),
                    sets=wanted,
                    source=source_id,
                )
            elif filename:
                self._remove_manifest_entry(filename)
                target = GIF_DIR / filename
                if target.exists():
                    target.unlink()
                entry.pop("file", None)

            entry["sets"] = wanted
            self._save()
            return {"sets": wanted, "file": entry.get("file")}

    def _download(self, candidate: dict) -> str:
        slug = _slugify(candidate.get("title") or candidate.get("query") or "gif")
        unique = candidate["source_id"].split(":", 1)[1][:8]
        filename = f"{slug}-{unique}.gif"
        request_obj = urllib.request.Request(candidate["download"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request_obj, timeout=30) as response:
            (GIF_DIR / filename).write_bytes(response.read())
        return filename

    # -- import your own --------------------------------------------------
    def stash_upload(self, storage) -> dict:
        """Keep an uploaded GIF aside; it becomes a candidate like any other."""
        raw = storage.read()
        digest = hashlib.sha1(raw).hexdigest()[:10]
        name = f"{_slugify(Path(storage.filename or 'dropped').stem)}-{digest}.gif"
        path = IMPORTED / name
        path.write_bytes(raw)
        return {
            "source_id": f"local:{digest}",
            "title": Path(storage.filename or name).stem.replace("-", " ").replace("_", " "),
            "preview": f"/imported/{name}",
            "download": path.resolve().as_uri(),
            "page": None,
            "query": "your files",
            "frames": count_frames(path),
            "local": True,
        }

    # -- manifest ---------------------------------------------------------
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

    def _write_manifest_entry(self, *, filename: str, label: str, sets: list[str], source: str) -> None:
        entries = [e for e in self._manifest_entries() if e.get("file") != filename]
        entries.append(
            {"id": Path(filename).stem, "file": filename, "label": label, "sets": sets, "source": source}
        )
        self._write_manifest(entries)

    def _remove_manifest_entry(self, filename: str) -> None:
        self._write_manifest([e for e in self._manifest_entries() if e.get("file") != filename])

    def drop_filler(self) -> int:
        """Delete the generated placeholder cards once real ones are in."""
        with self.lock:
            removed = 0
            for path in sorted(GIF_DIR.glob("gif_[0-9][0-9][0-9].gif")):
                path.unlink()
                removed += 1
            self._write_manifest(
                [e for e in self._manifest_entries() if not re.fullmatch(r"gif_\d{3}\.gif", e.get("file", ""))]
            )
            return removed


# --- candidate queue -----------------------------------------------------------
class Queue:
    """A pool of unseen candidates, refilled in batches of 50 from the API."""

    def __init__(self, source, library: Library, queries: list[str], min_frames: int):
        self.source = source
        self.library = library
        self.queries = queries
        self.min_frames = min_frames
        self.pinned: str | None = None
        self.pool: list[dict] = []
        self.lock = threading.RLock()
        self.last_error: str | None = None
        self.calls = 0
        self.dropped_frames = 0

    def pin(self, query: str | None) -> None:
        """Focus on one search term (or None to go back to the whole pack)."""
        with self.lock:
            self.pinned = (query or "").strip() or None
            self.pool.clear()
            self.last_error = None

    def take(self, count: int) -> list[dict]:
        out = []
        with self.lock:
            for _ in range(count):
                candidate = self._next_one()
                if candidate is None:
                    break
                out.append(candidate)
        return out

    def _next_one(self) -> dict | None:
        for _ in range(8):
            while self.pool:
                candidate = self.pool.pop()
                if self.library.seen(candidate["source_id"]):
                    continue
                # 0 means the source didn't say; only drop when we know it's too few.
                if candidate["frames"] and candidate["frames"] < self.min_frames:
                    self.dropped_frames += 1
                    continue
                candidate["sets"] = []
                return candidate
            if not self._refill():
                return None
        return None

    def _refill(self) -> bool:
        query = self.pinned if self.pinned is not None else (random.choice(self.queries) if self.queries else "")
        # A random offset is what makes this a firehose instead of the same 50 GIFs.
        offset = random.randrange(0, 400)
        try:
            batch = self.source.fetch(query, offset)
            self.calls += 1
            self.last_error = None
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode()[:160]
            except Exception:  # noqa: BLE001
                pass
            if exc.code in (401, 403):
                self.last_error = f"The API rejected the key ({exc.code}). Check {self.source.key_env}. {body}"
            elif exc.code == 429:
                self.last_error = "Rate limited — wait a minute, then keep going."
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
def build_app(queue: Queue, library: Library, source, min_frames: int) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(ROOT / "app" / "static"),
        static_url_path="/static",
    )

    @app.route("/")
    def index():
        return render_template(
            "curate.html",
            source=source.name,
            rating=getattr(source, "rating", ""),
            min_frames=min_frames,
            sets=SETS,
            counts=library.counts(),
            queries=random.sample(queue.queries, min(12, len(queue.queries))) if queue.queries else [],
        )

    @app.get("/imported/<path:name>")
    def imported(name: str):
        return send_from_directory(IMPORTED, name)

    @app.get("/api/next")
    def api_next():
        count = max(1, min(12, _as_int(request.args.get("n")) or 6))
        return jsonify(
            candidates=queue.take(count),
            counts=library.counts(),
            error=queue.last_error,
            query=queue.pinned,
            dropped_frames=queue.dropped_frames,
        )

    @app.post("/api/sets")
    def api_sets():
        data = request.get_json(silent=True) or {}
        candidate = data.get("candidate") or {}
        wanted = data.get("sets")
        if not candidate.get("source_id") or not isinstance(wanted, list):
            return jsonify(error="bad request"), 400
        try:
            result = library.apply_sets(candidate, wanted)
        except Exception as exc:  # noqa: BLE001 — a failed download must not kill the app
            return jsonify(error=f"could not save that one: {exc}"), 502
        return jsonify(ok=True, counts=library.counts(), **result)

    @app.post("/api/seen")
    def api_seen():
        data = request.get_json(silent=True) or {}
        candidate = data.get("candidate") or {}
        if not candidate.get("source_id"):
            return jsonify(error="bad request"), 400
        library.mark_seen(candidate["source_id"], candidate)
        return jsonify(ok=True)

    @app.post("/api/query")
    def api_query():
        data = request.get_json(silent=True) or {}
        queue.pin(data.get("query"))
        return jsonify(ok=True, query=queue.pinned)

    @app.get("/api/related")
    def api_related():
        """Suggestions for "more like this".

        Giphy's related-tags endpoint is thin for oddities — "cursed" returns plenty,
        "nuke" returns nothing — so try the search term, then the words of the GIF's own
        title, and always hand back something clickable.
        """
        term = (request.args.get("term") or "").strip()
        title = (request.args.get("title") or "").strip()
        words = [w for w in re.split(r"[^a-zA-Z0-9]+", title.lower()) if len(w) > 2]
        stop = {"gif", "the", "and", "for", "with", "you", "your", "not", "off", "out", "gifs"}
        words = [w for w in words if w not in stop]
        pairs = [" ".join(words[i : i + 2]) for i in range(len(words) - 1)]

        for attempt in [term, *pairs, *words]:
            if not attempt:
                continue
            tags = source.related_tags(attempt)
            if tags:
                return jsonify(term=attempt, tags=tags, fallback=[])
        # Nothing related: the title's own words are still a way onwards.
        return jsonify(term=term, tags=[], fallback=(words + [term])[:10])

    @app.post("/api/import")
    def api_import():
        files = request.files.getlist("files")
        candidates, skipped = [], []
        for storage in files:
            if not (storage.filename or "").lower().endswith(".gif"):
                skipped.append(storage.filename or "?")
                continue
            candidates.append(library.stash_upload(storage))
        return jsonify(ok=True, candidates=candidates, skipped=skipped, counts=library.counts())

    @app.post("/api/drop-filler")
    def api_drop_filler():
        return jsonify(ok=True, removed=library.drop_filler(), counts=library.counts())

    return app


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", choices=sorted(SOURCES), default="giphy")
    parser.add_argument("--api-key", default=None, help="defaults to $GIPHY_API_KEY / $TENOR_API_KEY or .env")
    parser.add_argument("--rating", default="r", help="giphy: g, pg, pg-13, r (default r — you're curating by hand)")
    parser.add_argument(
        "--pack",
        action="append",
        choices=sorted(QUERY_PACKS),
        help="starter search terms; repeatable (default: all of them)",
    )
    parser.add_argument("--queries", default=None, help="comma-separated search terms, instead of the packs")
    parser.add_argument("--min-frames", type=int, default=10, help="drop GIFs with fewer frames (default 10)")
    parser.add_argument("--port", type=int, default=5099)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    load_dotenv()
    source_class = SOURCES[args.source]
    api_key = args.api_key or os.environ.get(source_class.key_env, "").strip()
    if not api_key:
        print(f"\n  No API key. Put {source_class.key_env}=... in .env, or pass --api-key.\n")
        print(f"  {source_class.key_help}\n")
        return 2

    if args.queries:
        queries = [q.strip() for q in args.queries.split(",") if q.strip()]
    else:
        packs = args.pack or sorted(QUERY_PACKS)
        queries = [q for pack in packs for q in QUERY_PACKS[pack]]

    source = source_class(api_key, args.rating)
    library = Library()
    queue = Queue(source, library, queries, args.min_frames)
    counts = library.counts()

    print(f"\n  🃏 GIF curator — {args.source}, rating {source.rating}, min {args.min_frames} frames")
    print(f"      open:   http://{args.host}:{args.port}")
    print(f"      into:   {GIF_DIR}")
    print(
        "      sets:   "
        + "   ".join(f"{meta['label']} {counts['sets'][name]}/{CARDS_PER_MODE}" for name, meta in SETS.items())
    )
    print("      keys:   ← → scroll    N / E / M tag    R related    / search    X clear\n")

    build_app(queue, library, source, args.min_frames).run(
        host=args.host, port=args.port, debug=False, use_reloader=False
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
