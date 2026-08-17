#!/usr/bin/env python3
"""Build the game's deck: the GIFs, and the prompts they answer.

A web app with three tabs. Everything is in one of two piles: Normal plays in every game,
and 18+ only joins in when the host turns "keep it clean" off. Cards and prompts are tagged
the same way, because a game deals both from the same switch.

    python scripts/curate_gifs.py          # then open http://127.0.0.1:5099

    Library    every card already in the game, filterable by set
    Discover   you search, it shows the results, you tag the ones you want
    Prompts    write them, edit them, and choose which pile they're in

There are deliberately no built-in search terms. There used to be ~100 hardcoded moods
("backrooms", "capybara") that the tool searched at random whenever you hadn't typed
anything; they produced under a quarter of the deck while hand-typed searches produced
three quarters of it. Guessing at someone's taste went badly, so now it just asks.

Your own GIFs: paste links on the Library tab — Tenor, Discord, anywhere. A link rather
than a file upload, because a link is what lets the card be rebuilt on another machine
(see scripts/rehydrate_gifs.py).

State is three files, all in `curation/`: `library.json` is every card in the game — where
it came from, which sets it's in, and how often it has been played and won —
`ignored.json` is the ones you rejected, which only ever stops search offering them again,
and `prompts.json` is every prompt and the pile it's in. The game reads all three live,
so anything you change here is in the next round, without a restart.

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
sys.path.insert(0, str(ROOT))

import curation_store as store  # noqa: E402 — needs the path above

GIF_DIR = ROOT / "app" / "static" / "gifs"
MANIFEST = GIF_DIR / "manifest.json"
STATE_DIR = ROOT / "curation"
# Bytes for cards whose link will rot. Committed, unlike app/static/gifs — see
# keep_original() for what qualifies and why it has to be so few files.
ORIGINALS = STATE_DIR / "originals"

# The two piles a GIF or prompt can be in — the same names app/games/gah/decks.py uses.
# They are exclusive: everything Normal plays in every game, and 18+ only joins in when
# the host turns "keep it clean" off. A card in both would just be a card in Normal, so
# the tool doesn't offer that state.
SETS = {
    "normal": {"label": "Normal"},
    "adult": {"label": "18+"},
}
CARDS_PER_DECK = 56  # 8 players x 7 cards: what the clean deck needs to be playable

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


def _measure(path: Path) -> tuple[int, int]:
    """A GIF's pixel size, or (0, 0) if Pillow isn't installed or the file isn't here."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:  # noqa: BLE001 — dimensions are an optimisation, never required
        return 0, 0


def _slugify(text: str) -> str:
    text = re.sub(r"\bGIF\b", "", text or "", flags=re.I)
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text or "gif")[:40]


def _pretty_label(title: str | None, filename: str) -> str:
    cleaned = re.sub(r"\bGIF\b", "", title or "", flags=re.I).strip()
    return (cleaned or Path(filename).stem.replace("-", " ").title())[:60]


# --- the library ---------------------------------------------------------------
class Library:
    """The game's GIF folder and the manifest, on top of curation_store's two files."""

    def __init__(self):
        self.lock = threading.RLock()
        STATE_DIR.mkdir(exist_ok=True)
        GIF_DIR.mkdir(parents=True, exist_ok=True)
        # Set once the API source exists; needed to re-fetch a Giphy card whose file was
        # deleted, since a Giphy id resolves to a media URL only through the API.
        self.source = None

    # -- lookups ----------------------------------------------------------
    def sets_of(self, source_id: str) -> list[str]:
        return list((store.library().get(source_id) or {}).get("sets") or [])

    def counts(self) -> dict:
        cards = store.library()
        per_set = {name: 0 for name in SETS}
        for card in cards.values():
            for name in card.get("sets", []):
                if name in per_set:
                    per_set[name] += 1
        return {
            "sets": per_set,
            "ignored": len(store.ignored()),
            "target": CARDS_PER_DECK,
            "in_game": len(cards),
        }

    # -- membership -------------------------------------------------------
    def apply_sets(self, candidate: dict, wanted: list[str]) -> dict:
        """Make this GIF belong to exactly `wanted`.

        Newly in a set -> the file is fetched. Out of every set -> it leaves the library
        and the file goes away. Counters survive re-tagging, because a card you move from
        Normal to 18+ is the same card with the same history.
        """
        wanted = [s for s in wanted if s in SETS]
        source_id = candidate["source_id"]
        with self.lock:
            existing = store.library().get(source_id) or {}
            filename = existing.get("file")

            if wanted and not filename:
                filename = self._download(candidate)
            if wanted:
                # Tagging something is the clearest possible statement that it is not
                # unfunny, so it stops being ignored.
                store.unignore(source_id)
                card = store.put(
                    source_id,
                    {
                        "file": filename,
                        "title": candidate.get("title") or existing.get("title", ""),
                        "sets": wanted,
                        "page": candidate.get("page") or existing.get("page"),
                        # Giphy cards are found again by their id, so the key is enough.
                        # Anything else has only its link, and without it the card could
                        # never be rebuilt elsewhere — the one field we must not lose.
                        "url": candidate.get("url") or existing.get("url"),
                    },
                )
                self._write_manifest_entry(
                    filename=filename,
                    label=_pretty_label(card.get("title"), filename),
                    sets=wanted,
                    source=source_id,
                    card=card,
                )
            elif filename:
                self._remove_manifest_entry(filename)
                target = GIF_DIR / filename
                if target.exists():
                    target.unlink()
                store.forget(source_id)
            return {"sets": wanted, "file": filename if wanted else None}

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
        """Every card in the game — what the Library tab shows."""
        out = []
        for source_id, entry in store.library().items():
            if not entry.get("file"):
                continue
            out.append(
                {
                    "source_id": source_id,
                    "file": entry["file"],
                    "title": _pretty_label(entry.get("title"), entry["file"]),
                    "sets": list(entry.get("sets") or []),
                    "page": entry.get("page"),
                    "added": entry.get("added"),
                    "uses": int(entry.get("uses", 0)),
                    "wins": int(entry.get("wins", 0)),
                    "on_disk": (GIF_DIR / entry["file"]).is_file(),
                }
            )
        out.sort(key=lambda c: c["title"].lower())
        return out

    def ignored_cards(self) -> list[dict]:
        """Cards you rejected. Kept only so search can skip them, and so a mistake here
        is undoable — nothing else in the app reads this list."""
        out = [
            {"source_id": sid, "title": e.get("title") or sid, "page": e.get("page"), "ignored": e.get("ignored")}
            for sid, e in store.ignored().items()
        ]
        out.sort(key=lambda c: (c.get("ignored") or ""), reverse=True)
        return out

    def retag(self, source_id: str, wanted: list[str]) -> dict:
        """Change which sets a card that is already in the library belongs to.

        Deliberately the same code path as tagging during discovery, so a card moved from
        Normal to 18+ here behaves exactly as if it had been tagged that way originally.
        """
        entry = store.library().get(source_id)
        if not entry:
            raise KeyError(source_id)
        candidate = {
            "source_id": source_id,
            "title": entry.get("title", ""),
            "page": entry.get("page"),
            "url": entry.get("url"),
        }
        # Re-tagging something whose file was deleted has to fetch it again; the link
        # that brought it in the first time is the one that brings it back.
        if wanted and not (entry.get("file") and (GIF_DIR / entry["file"]).is_file()):
            candidate["download"] = self.download_url_for(source_id, entry)
        return self.apply_sets(candidate, wanted)

    def reject(self, source_id: str, title: str = "", page: str | None = None) -> dict:
        """The ✕: drop it from the game *and* stop search offering it again.

        Works on a card you already had and on one you have only just been shown, which
        is why the title comes in from the caller — a search result has no library row to
        read it from, and "giphy:26tPgV8c" is no use to anyone reviewing the list later.
        """
        entry = store.library().get(source_id) or {}
        result = self.apply_sets({"source_id": source_id}, []) if entry else {"sets": [], "file": None}
        store.ignore(source_id, title or entry.get("title", ""), page or entry.get("page"))
        return result

    def download_url_for(self, source_id: str, entry: dict) -> str:
        """Where to fetch this card from, given only what the library remembers."""
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

    def _write_manifest_entry(
        self, *, filename: str, label: str, sets: list[str], source: str, card: dict | None = None
    ) -> None:
        entries = [e for e in self._manifest_entries() if e.get("file") != filename]
        entry = {"id": Path(filename).stem, "file": filename, "label": label, "sets": sets, "source": source}
        # The card's real shape, so the board can reserve the right space before the
        # picture loads instead of cropping it into a 4:3 box.
        width, height = (card or {}).get("w"), (card or {}).get("h")
        if not (width and height):
            width, height = _measure(GIF_DIR / filename)
            if width and height:
                store.put(source, {"w": width, "h": height})
        if width and height:
            entry["w"], entry["h"] = width, height
        entries.append(entry)
        self._write_manifest(entries)

    def _remove_manifest_entry(self, filename: str) -> None:
        self._write_manifest([e for e in self._manifest_entries() if e.get("file") != filename])


# --- prompts --------------------------------------------------------------------
# A prompt is text and the modes it plays in — the same three sets a GIF has, because a
# mode is a pairing: 18+ deals 18+ GIFs against 18+ prompts. No state to hold, so these
# are plain functions over curation/prompts.json rather than a class like Library.
MAX_PROMPT_CHARS = 200


def _count_blanks(text: str) -> int:
    """How many gaps the sentence has. The game draws `___` as a blank line, so this is
    read off the text rather than typed in — one less field to get wrong."""
    return max(1, len(re.findall(r"_{2,}", text)))


def prompt_rows() -> list[dict]:
    """Every prompt, in id order — which is the order they were written in."""
    return [
        {
            "id": prompt_id,
            "text": row.get("text", ""),
            "sets": [s for s in (row.get("sets") or []) if s in SETS],
            "blanks": int(row.get("blanks", 1)),
            "added": row.get("added"),
        }
        for prompt_id, row in sorted(store.prompts().items())
    ]


def prompt_counts() -> dict:
    """How many prompts each mode has, for the Prompts tab's own filter row."""
    rows = store.prompts().values()
    counts = {name: 0 for name in SETS}
    for row in rows:
        for name in row.get("sets") or []:
            if name in counts:
                counts[name] += 1
    counts["all"] = len(list(rows))
    return counts


def clean_prompt(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        raise ValueError("a prompt needs some words")
    if len(text) > MAX_PROMPT_CHARS:
        raise ValueError(f"that's over {MAX_PROMPT_CHARS} characters — it won't fit on a card")
    return text


def save_prompt(prompt_id: str | None, text: str, sets: list[str]) -> dict:
    """Write a prompt, new or existing. Returns it in the shape the tab renders."""
    text = clean_prompt(text)
    wanted = [s for s in sets if s in SETS]
    fields = {"text": text, "sets": wanted, "blanks": _count_blanks(text)}
    if prompt_id:
        if prompt_id not in store.prompts():
            raise KeyError(prompt_id)
        row = store.put_prompt(prompt_id, fields)
    else:
        prompt_id, row = store.add_prompt(text, wanted, fields["blanks"])
    return {
        "id": prompt_id,
        "text": row["text"],
        "sets": list(row.get("sets") or []),
        "blanks": int(row.get("blanks", 1)),
        "added": row.get("added"),
    }


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


def build_app(library: Library, source, password: str) -> Flask:
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
            sets=SETS,
            # Explicitly, because Flask's JSON provider sorts keys — relying on the dict
            # would put the buttons in the order 18+ / Normal.
            set_order=list(SETS),
            counts=library.counts(),
        )

    @app.get("/api/library")
    def api_library():
        return jsonify(cards=library.cards(), ignored=library.ignored_cards(), counts=library.counts())

    @app.post("/api/reject")
    def api_reject():
        """The ✕ — out of the game, and search stops offering it."""
        data = request.get_json(silent=True) or {}
        source_id = data.get("source_id")
        if not source_id:
            return jsonify(error="bad request"), 400
        try:
            library.reject(source_id, (data.get("title") or "").strip(), data.get("page"))
        except Exception as exc:  # noqa: BLE001
            return jsonify(error=str(exc)), 502
        return jsonify(ok=True, counts=library.counts())

    @app.post("/api/unignore")
    def api_unignore():
        data = request.get_json(silent=True) or {}
        source_id = data.get("source_id")
        if not source_id:
            return jsonify(error="bad request"), 400
        store.unignore(source_id)
        return jsonify(ok=True, counts=library.counts())

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
        """Take a pasted Tenor/Discord/whatever link and turn it into a taggable card."""
        data = request.get_json(silent=True) or {}
        added, failed = [], []
        for raw in (data.get("links") or []):
            try:
                added.append(library.candidate_from_link(raw))
            except Exception as exc:  # noqa: BLE001
                failed.append({"link": raw, "why": str(exc)})
        return jsonify(ok=True, candidates=added, failed=failed, counts=library.counts())

    @app.get("/api/search")
    def api_search():
        """One search, one page of results — whatever the API returns, unfiltered.

        There is no queue and no invented search terms any more. The tool used to pick
        from a hardcoded list of ~100 moods when nothing was typed, which produced 24% of
        the deck while your own searches produced 76%: it was guessing at taste it had no
        way to know. Now it asks, and shows you the lot.
        """
        term = (request.args.get("q") or "").strip()
        offset = max(0, _as_int(request.args.get("offset")))
        try:
            found = source.fetch(term, offset)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode()[:160]
            except Exception:  # noqa: BLE001
                pass
            if exc.code in (401, 403):
                return jsonify(error=f"The API rejected the key ({exc.code}). Check {source.key_env}. {body}"), 502
            if exc.code == 429:
                return jsonify(error="Rate limited — wait a minute, then try again."), 502
            return jsonify(error=f"API error {exc.code}: {body}"), 502
        except Exception as exc:  # noqa: BLE001
            return jsonify(error=f"{type(exc).__name__}: {exc}"), 502

        # Anything you rejected is gone from the results entirely — that is the whole
        # job of ignored.json. Cards already in the library stay, with their sets lit up,
        # because knowing "this one is already in Normal" is useful while you search.
        hidden = store.ignored()
        in_game = store.library()
        results = []
        for candidate in found:
            if candidate["source_id"] in hidden:
                continue
            candidate["sets"] = list((in_game.get(candidate["source_id"]) or {}).get("sets") or [])
            candidate["known"] = candidate["source_id"] in in_game
            results.append(candidate)
        return jsonify(
            results=results,
            counts=library.counts(),
            term=term,
            offset=offset,
            skipped=len(found) - len(results),
        )

    # -- prompts ---------------------------------------------------------
    @app.get("/api/prompts")
    def api_prompts():
        return jsonify(prompts=prompt_rows(), counts=prompt_counts())

    @app.post("/api/prompt-save")
    def api_prompt_save():
        """Create or edit one prompt. No id means new; an id means edit in place, which
        is also how the set chips are toggled — the same write either way."""
        data = request.get_json(silent=True) or {}
        wanted = data.get("sets")
        if not isinstance(wanted, list):
            return jsonify(error="bad request"), 400
        try:
            row = save_prompt((data.get("id") or "").strip() or None, data.get("text") or "", wanted)
        except KeyError:
            return jsonify(error="no such prompt"), 404
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(ok=True, prompt=row, counts=prompt_counts())

    @app.post("/api/prompt-drop")
    def api_prompt_drop():
        """Delete a prompt for good. Unlike a GIF there is nothing to un-reject: the text
        is the whole card, and curation/prompts.json is in git if you want it back."""
        data = request.get_json(silent=True) or {}
        prompt_id = (data.get("id") or "").strip()
        if not prompt_id:
            return jsonify(error="bad request"), 400
        if not store.drop_prompt(prompt_id):
            return jsonify(error="no such prompt"), 404
        return jsonify(ok=True, counts=prompt_counts())

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

    return app


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", choices=sorted(SOURCES), default="giphy")
    parser.add_argument("--api-key", default=None, help="defaults to $GIPHY_API_KEY / $TENOR_API_KEY or .env")
    parser.add_argument("--rating", default="r", help="giphy: g, pg, pg-13, r (default r — you're curating by hand)")
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
    counts = library.counts()

    written = prompt_counts()
    print(f"\n  🃏 Deck curator — {args.source}, rating {source.rating}")
    print(f"      open:   http://{args.host}:{args.port}{args.prefix}")
    print(f"      into:   {GIF_DIR}")
    print(
        "      gifs:   "
        + "   ".join(f"{meta['label']} {counts['sets'][name]}/{CARDS_PER_DECK}" for name, meta in SETS.items())
    )
    print(
        "      words:  "
        + "   ".join(f"{meta['label']} {written[name]}" for name, meta in SETS.items())
    )

    app = build_app(library, source, password)
    app.wsgi_app = PrefixMiddleware(app.wsgi_app, args.prefix)
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
