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
import hmac
import json
import os
import random
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

ROOT = Path(__file__).resolve().parent.parent
GIF_DIR = ROOT / "app" / "static" / "gifs"
MANIFEST = GIF_DIR / "manifest.json"
STATE_DIR = ROOT / "curation"
DECISIONS = STATE_DIR / "decisions.json"
# Bytes for cards whose link will rot. Committed, unlike app/static/gifs — see
# keep_original() for what qualifies and why it has to be so few files.
ORIGINALS = STATE_DIR / "originals"

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

# Only ever used to suggest a passphrase when there isn't one. Four of these is about 40
# bits — plenty against a login that allows five guesses a minute, and sayable down a pub.
WORDS = (
    "amber banjo cactus dawn ember fable glimmer harbour ivory jangle kettle lantern "
    "meadow nutmeg orbit pebble quiver ribbon saffron thistle umber velvet walnut zephyr"
).split()


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


def resolve_media(url: str) -> tuple[str, str]:
    """Turn a link a human pasted into a link to actual GIF bytes, plus a title.

    A Discord attachment already points at the file. A Tenor or Giphy *page* is HTML
    wrapping the thing you want, and every one of them advertises the real file as
    `og:image` — the same tag that makes a link preview appear in chat. That is a far
    steadier handle than scraping <img> tags, and it needs no API key for either site.

    Returns (media_url, title). The title is best-effort; the caller has a fallback.
    """
    request_obj = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request_obj, timeout=25) as response:
        content_type = response.headers.get_content_type()
        if not content_type.startswith("text/html"):
            return url, ""  # already the file itself
        body = response.read(200_000).decode("utf-8", "replace")

    def meta(prop: str) -> str | None:
        for pattern in (
            rf'<meta[^>]+property=["\']{prop}["\'][^>]+content=["\']([^"\']+)',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{prop}["\']',
        ):
            found = re.search(pattern, body, re.I)
            if found:
                return found.group(1)
        return None

    media = meta("og:image")
    if not media:
        raise ValueError("that page doesn't advertise a GIF (no og:image)")
    return _lighter_rendition(media), _clean_title(meta("og:title") or "")


# A hand is seven cards loading at once, so a card that weighs more than this is worth
# trading resolution for. Same budget the Giphy path uses.
MAX_CARD_BYTES = 2_000_000


def _lighter_rendition(media: str) -> str:
    """Swap a heavyweight Tenor GIF for its small variant, but only if it is heavy.

    Tenor publishes each GIF at 640px (`…AAAAd`) and 220px (`…AAAAM`) with nothing in
    between, so this is a real trade rather than a tidy-up: 640 looks right on a phone
    and most of them are affordable, but the occasional 4.7 MB card is not worth it when
    seven of them land at once. Anything that isn't Tenor is left exactly as it is.
    """
    # Tenor labels the big rendition AAAAd or AAAAC depending on the upload; the small
    # one is always AAAAM, on the host without the /m/ path.
    found = re.match(r"https://media\d*\.tenor\.com/m/([A-Za-z0-9_\-]+)AAAA[dC]/(.+)$", media)
    if not found:
        return media
    ident, name = found.groups()

    weight = 0
    for _ in range(2):  # one retry: a flaky HEAD shouldn't silently ship a 5 MB card
        try:
            request_obj = urllib.request.Request(media, headers={"User-Agent": USER_AGENT}, method="HEAD")
            with urllib.request.urlopen(request_obj, timeout=15) as response:
                weight = _as_int(response.headers.get("Content-Length"))
            break
        except Exception:  # noqa: BLE001
            continue
    if 0 < weight <= MAX_CARD_BYTES:
        return media
    if not weight:
        return media  # couldn't tell — keep the sharper one rather than guess
    return f"https://media.tenor.com/{ident}AAAAM/{name}"


def keep_original(url: str) -> bool:
    """Whether this link is the expiring kind, so the bytes must be kept alongside it.

    A Discord attachment URL is signed and carries its own expiry (`?ex=<hex unix>`),
    typically about a day out — follow it next week and you get a 404, which would make
    the card unrecoverable. Tenor and Giphy links keep working, so those stay links and
    stay out of git. This is the deliberate exception, and it should apply to a handful
    of files, never a library.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return bool(query.get("ex") or query.get("Expires") or query.get("X-Amz-Expires"))


def _clean_title(title: str) -> str:
    """Strip the SEO tail these sites put in og:title.

    Tenor sends "Cat Poor GIF - Cat Poor - Discover & Share GIFs": the name, then the
    tags, then boilerplate. Only the first segment is the name, and without this every
    card ends up called "…Discover Amp Share Gifs".
    """
    head = re.split(r"\s+[-–|]\s+", title.strip())[0]
    head = re.sub(r"\bdiscover\s*&?a?m?p?;?\s*share\s*gifs?\b", "", head, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"\bGIFs?\b", "", head, flags=re.I)).strip()


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
        GIF_DIR.mkdir(parents=True, exist_ok=True)
        # Set once the API source exists; needed to re-fetch a Giphy card whose file was
        # deleted, since a Giphy id resolves to a media URL only through the API.
        self.source = None
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
            # Giphy cards are found again by their id, so the id in the key is enough.
            # Anything else has only its link, and without it the card could never be
            # rebuilt on another machine — so that is the one field we must not lose.
            if candidate.get("url"):
                entry["url"] = candidate["url"]
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
            payload = response.read()
        (GIF_DIR / filename).write_bytes(payload)
        # A link with an expiry can't bring this card back tomorrow, so keep the bytes.
        if candidate.get("url") and keep_original(candidate["url"]):
            ORIGINALS.mkdir(parents=True, exist_ok=True)
            (ORIGINALS / filename).write_bytes(payload)
        return filename

    # -- the library: what is already in the game --------------------------
    def cards(self) -> list[dict]:
        """Every card that is in at least one set — what the Library tab shows."""
        out = []
        for source_id, entry in self.decisions.items():
            if not entry.get("sets") or not entry.get("file"):
                continue
            out.append(
                {
                    "source_id": source_id,
                    "file": entry["file"],
                    "title": _pretty_label(entry.get("title"), entry["file"]),
                    "sets": list(entry["sets"]),
                    "page": entry.get("page"),
                    "on_disk": (GIF_DIR / entry["file"]).is_file(),
                }
            )
        out.sort(key=lambda c: c["title"].lower())
        return out

    def retag(self, source_id: str, wanted: list[str]) -> dict:
        """Change which sets a card that is already in the library belongs to.

        This is the Library tab's whole job, and it is deliberately the same code path as
        tagging during discovery — so a card moved from Normal to 18+ here behaves exactly
        as if it had been tagged that way in the first place.
        """
        entry = self.decisions.get(source_id)
        if not entry:
            raise KeyError(source_id)
        candidate = {
            "source_id": source_id,
            "title": entry.get("title", ""),
            "query": entry.get("query", ""),
            "page": entry.get("page"),
            "url": entry.get("url"),
        }
        # Re-tagging something whose file was deleted has to fetch it again; the link
        # that brought it in the first time is the one that brings it back.
        if wanted and not (entry.get("file") and (GIF_DIR / entry["file"]).is_file()):
            entry.pop("file", None)
            candidate["download"] = self.download_url_for(source_id, entry)
        return self.apply_sets(candidate, wanted)

    def download_url_for(self, source_id: str, entry: dict) -> str:
        """Where to fetch this card from, given only what decisions.json remembers."""
        if entry.get("url"):
            return resolve_media(entry["url"])[0]
        if source_id.startswith("giphy:") and self.source is not None:
            found = _get_json(
                "https://api.giphy.com/v1/gifs/"
                + urllib.parse.quote(source_id.split(":", 1)[1])
                + f"?api_key={self.source.api_key}"
            )
            media = self.source._rendition((found.get("data") or {}).get("images", {}))
            if media:
                return media
        raise ValueError("no link recorded for that card")

    # -- add your own, by link --------------------------------------------
    def candidate_from_link(self, url: str) -> dict:
        """Make a candidate out of a pasted link — Discord, Tenor, anywhere.

        The *page* URL is what gets remembered, not the media URL it resolves to today:
        CDN paths get re-pointed, but the link you copied out of Discord keeps working,
        and it is what scripts/rehydrate_gifs.py will follow on the next machine.
        """
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("that doesn't look like a link")
        media, title = resolve_media(url)
        return {
            "source_id": f"url:{hashlib.sha1(url.encode()).hexdigest()[:10]}",
            "title": title or Path(urllib.parse.urlparse(url).path).stem.replace("-", " "),
            "preview": media,
            "download": media,
            "url": url,
            "page": url,
            "query": "your links",
            # No API says how many frames a pasted link has, and 0 means "unknown",
            # which the frame filter lets through rather than dropping.
            "frames": 0,
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

    # How many different searches go into one refill when nothing is pinned. One search
    # returns 50 results, so filling from a single term meant roughly fifty cards in a row
    # all "found under backrooms" — you had to reload to escape a term. Blending several
    # and shuffling makes consecutive cards come from different searches, for the same
    # number of API calls overall (three times as many per refill, a third as often).
    BLEND = 3

    def _refill(self) -> bool:
        if self.pinned is not None:
            queries = [self.pinned]
        elif self.queries:
            queries = random.sample(self.queries, min(self.BLEND, len(self.queries)))
        else:
            queries = [""]

        batch: list[dict] = []
        errors = []
        for query in queries:
            # A random offset is what makes this a firehose instead of the same 50 GIFs.
            try:
                batch.extend(self.source.fetch(query, random.randrange(0, 400)))
                self.calls += 1
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode()[:160]
                except Exception:  # noqa: BLE001
                    pass
                if exc.code in (401, 403):
                    errors.append(f"The API rejected the key ({exc.code}). Check {self.source.key_env}. {body}")
                elif exc.code == 429:
                    errors.append("Rate limited — wait a minute, then keep going.")
                else:
                    errors.append(f"API error {exc.code}: {body}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")

        # One dead search out of three is not worth stopping for; report only a total loss.
        self.last_error = errors[0] if errors and not batch else None
        random.shuffle(batch)
        self.pool.extend(batch)
        return bool(batch)


# --- web app -------------------------------------------------------------------
class PrefixMiddleware:
    """Let the curator live under a path like /curate on a domain it shares.

    nginx forwards the original prefix in X-Forwarded-Prefix; moving it into SCRIPT_NAME
    is what makes url_for() emit /curate/login instead of /login — otherwise every
    redirect would land on the game instead of here.
    """

    def __init__(self, app, prefix: str = ""):
        self.app = app
        self.prefix = "/" + prefix.strip("/") if prefix.strip("/") else ""

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX", self.prefix).rstrip("/")
        if prefix:
            environ["SCRIPT_NAME"] = prefix
            path = environ.get("PATH_INFO", "")
            if path.startswith(prefix):
                environ["PATH_INFO"] = path[len(prefix) :] or "/"
        return self.app(environ, start_response)


def _safe_next(target: str | None, fallback: str) -> str:
    """Where to go after signing in — but only somewhere inside this app.

    `next` arrives from the query string, so it is attacker-controlled: without this a
    crafted link could bounce someone straight off the site the moment they authenticate.
    Anything that isn't a plain path under our own root falls back to the front page.
    """
    if not target or not target.startswith("/"):
        return fallback
    if target.startswith("//") or "://" in target:  # protocol-relative or absolute
        return fallback
    root = (fallback or "/").rstrip("/")
    return target if not root or target == root or target.startswith(root + "/") else fallback


def build_app(queue: Queue, library: Library, source, min_frames: int, password: str) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(ROOT / "app" / "static"),
        static_url_path="/static",
    )
    # Signs the "you're in" cookie. Reusing the game's key is fine — different cookie
    # name, same box — but a curator-specific one keeps the two independent.
    app.secret_key = os.environ.get("CURATOR_SECRET_KEY") or os.environ.get("SECRET_KEY") or os.urandom(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Same trap as the game's GN_COOKIE_SECURE: turn this on only once HTTPS works,
        # or the browser drops the cookie and the password page just reappears forever.
        SESSION_COOKIE_SECURE=os.environ.get("CURATOR_COOKIE_SECURE", "0") == "1",
    )

    # Whoever has the password can retag anything, which is the point — this gate exists
    # to keep the internet at large out, not to tell friends apart.
    attempts: dict[str, list[float]] = {}

    def throttled(ip: str) -> bool:
        """Five wrong guesses a minute, per address. Enough to stop a script, not enough
        to lock out someone fat-fingering a passphrase on a phone."""
        now = time.monotonic()
        recent = [t for t in attempts.get(ip, []) if now - t < 60]
        attempts[ip] = recent
        return len(recent) >= 5

    @app.before_request
    def guard():
        if session.get("curator") or request.endpoint in {"login", "static"}:
            return None
        if request.path.startswith("/api/"):
            return jsonify(error="not signed in"), 401
        # script_root + path, not path: `path` is relative to the app, so under /curate it
        # is just "/" — and sending someone there after login lands them on the game.
        return redirect(url_for("login", next=request.script_root + request.path))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            ip = request.headers.get("X-Real-IP") or request.remote_addr or "?"
            if throttled(ip):
                error = "Too many tries — wait a minute."
            elif hmac.compare_digest(request.form.get("password", ""), password):
                session["curator"] = True
                session.permanent = True
                return redirect(_safe_next(request.args.get("next"), url_for("index")))
            else:
                attempts.setdefault(ip, []).append(time.monotonic())
                error = "That's not it."
        return render_template("curate_login.html", error=error)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

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

    @app.get("/api/library")
    def api_library():
        return jsonify(cards=library.cards(), counts=library.counts())

    @app.post("/api/retag")
    def api_retag():
        data = request.get_json(silent=True) or {}
        source_id, wanted = data.get("source_id"), data.get("sets")
        if not source_id or not isinstance(wanted, list):
            return jsonify(error="bad request"), 400
        try:
            result = library.retag(source_id, wanted)
        except KeyError:
            return jsonify(error="no such card"), 404
        except Exception as exc:  # noqa: BLE001 — one bad card must not kill the app
            return jsonify(error=str(exc)), 502
        return jsonify(ok=True, counts=library.counts(), **result)

    @app.post("/api/add-link")
    def api_add_link():
        """Take a pasted Tenor/Discord/whatever link and queue it up for tagging."""
        data = request.get_json(silent=True) or {}
        added, failed = [], []
        for raw in (data.get("links") or []):
            try:
                added.append(library.candidate_from_link(raw))
            except Exception as exc:  # noqa: BLE001
                failed.append({"link": raw, "why": str(exc)})
        return jsonify(ok=True, candidates=added, failed=failed, counts=library.counts())

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
    parser.add_argument(
        "--prefix",
        default="",
        help="path this is served under, e.g. /curate (nginx can send X-Forwarded-Prefix instead)",
    )
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

    # The curator is reachable from the internet now, so it needs a password. Refusing to
    # start without one is deliberate: a silent default would be worse than no gate at all,
    # because you would think you had one.
    password = os.environ.get("CURATOR_PASSWORD", "").strip()
    if not password:
        suggestion = "-".join(secrets.choice(WORDS) for _ in range(4))
        print("\n  No CURATOR_PASSWORD set, and this tool can retag the whole deck.")
        print(f"  Put one in .env, e.g.  CURATOR_PASSWORD={suggestion}\n")
        return 2

    source = source_class(api_key, args.rating)
    library = Library()
    library.source = source
    queue = Queue(source, library, queries, args.min_frames)
    counts = library.counts()

    print(f"\n  🃏 GIF curator — {args.source}, rating {source.rating}, min {args.min_frames} frames")
    print(f"      open:   http://{args.host}:{args.port}{args.prefix}")
    print(f"      into:   {GIF_DIR}")
    print(
        "      sets:   "
        + "   ".join(f"{meta['label']} {counts['sets'][name]}/{CARDS_PER_MODE}" for name, meta in SETS.items())
    )
    print("      keys:   ← → scroll    N / E / M tag    R related    / search    X clear\n")

    app = build_app(queue, library, source, args.min_frames, password)
    app.wsgi_app = PrefixMiddleware(app.wsgi_app, args.prefix)
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
