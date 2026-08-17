#!/usr/bin/env python3
"""Rebuild app/static/gifs/ from curation/library.json.

The GIF files are not in git. They are other people's work and this repo is public, so
shipping 47 MB of Fox and Comedy Central clips in the history is both rude and the kind
of thing that gets a repo taken down — code and all. What *is* in git is
curation/library.json: every card you kept and the link it came from. That is the part
that took real effort, and it is a few tens of KB of text that diffs cleanly.

So a fresh clone, or a second laptop, gets its deck back with:

    python scripts/rehydrate_gifs.py

Two kinds of link, one script:

    giphy:<id>     asked for by id through the API, which picks a card-sized rendition
    url:<digest>   fetched straight over https — a Discord attachment, or anything else

Giphy entries deliberately store the id rather than a media URL. An id is permanent;
media.giphy.com URLs are a CDN detail that can be re-pointed under you. Links that came
from somewhere else have nothing but their URL, so that is what gets recorded.

    --check    say what is missing, download nothing
    --force    re-download even files that are already here
    --prune    delete .gif files that no decision refers to any more
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from curate_gifs import (  # noqa: E402 — needs the path above
    GIF_DIR,
    MANIFEST,
    ORIGINALS,
    USER_AGENT,
    GiphySource,
    _pretty_label,
    load_dotenv,
    resolve_media,
)
import curation_store as store  # noqa: E402

# Giphy's by-id endpoint takes up to 100 at a time, which is one round trip for a
# deck of any realistic size.
BATCH = 100


def _fetch_json(url: str) -> dict:
    request_obj = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request_obj, timeout=30) as response:
        return json.loads(response.read())


def _download(url: str, target: Path) -> int:
    """Fetch one GIF, writing only once it has arrived whole.

    A half-written .gif is worse than a missing one: the game would serve it and the
    card would render as a broken image, so the download lands on a temporary name and
    is moved into place at the end.
    """
    request_obj = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request_obj, timeout=60) as response:
        payload = response.read()
    temporary = target.with_suffix(".part")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return len(payload)


def measure(path: Path) -> tuple[int, int]:
    """A GIF's pixel size, or (0, 0) if it isn't here or Pillow isn't installed."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:  # noqa: BLE001 — dimensions are an optimisation, never required
        return 0, 0


def wanted(cards: dict) -> dict[str, dict]:
    """The cards that should exist on disk: everything in the library with a file."""
    return {sid: card for sid, card in cards.items() if card.get("sets") and card.get("file")}


def resolve_giphy(source_ids: list[str], source: GiphySource) -> dict[str, str]:
    """Look up media URLs for a batch of Giphy ids, using the curator's own rendition
    choice so a rehydrated card is byte-for-byte the size it would have been."""
    found: dict[str, str] = {}
    ids = [s.split(":", 1)[1] for s in source_ids]
    for start in range(0, len(ids), BATCH):
        chunk = ids[start : start + BATCH]
        url = "https://api.giphy.com/v1/gifs?" + urllib.parse.urlencode(
            {"api_key": source.api_key, "ids": ",".join(chunk)}
        )
        for item in _fetch_json(url).get("data", []):
            media = source._rendition(item.get("images", {}))
            if media:
                found[f"giphy:{item['id']}"] = media
    return found


def rebuild_manifest(decisions: dict) -> int:
    """Regenerate the manifest from the decisions, so the two can never drift.

    The manifest is what the game reads; library.json is what a human edited. Deriving
    one from the other means a card can't be tagged Millennial in one file and Normal in
    the other.

    Each entry carries the GIF's real pixel size. The cards used to be forced into a 4:3
    box, which cropped anything that wasn't; knowing the true shape is what lets the
    board reserve the right space before the picture has loaded.
    """
    entries = []
    for source_id, entry in sorted(wanted(decisions).items()):
        filename = entry["file"]
        width, height = measure(GIF_DIR / filename)
        if width and height:  # remember it, so a machine without the file still knows
            entry["w"], entry["h"] = width, height
        item = {
            "id": Path(filename).stem,
            "file": filename,
            "label": _pretty_label(entry.get("title"), filename),
            "sets": entry["sets"],
            "source": source_id,
        }
        if entry.get("w") and entry.get("h"):
            item["w"], item["h"] = entry["w"], entry["h"]
        entries.append(item)
    MANIFEST.write_text(
        json.dumps({"generated": False, "count": len(entries), "gifs": entries}, indent=2) + "\n"
    )
    return len(entries)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="report what is missing, fetch nothing")
    parser.add_argument("--force", action="store_true", help="re-download files that are already here")
    parser.add_argument("--prune", action="store_true", help="delete .gif files no decision refers to")
    parser.add_argument("--api-key", default=None, help="defaults to $GIPHY_API_KEY or .env")
    args = parser.parse_args()

    load_dotenv()
    if not store.LIBRARY.exists():
        print(f"  no {store.LIBRARY} — nothing to rebuild from")
        return 2

    decisions = store.library()
    GIF_DIR.mkdir(parents=True, exist_ok=True)
    targets = wanted(decisions)

    missing = {
        source_id: entry
        for source_id, entry in targets.items()
        if args.force or not (GIF_DIR / entry["file"]).exists()
    }

    print(f"\n  🃏 rehydrate — {len(targets)} cards tagged, {len(missing)} to fetch")

    if args.check:
        for source_id, entry in sorted(missing.items()):
            print(f"      missing  {entry['file']}  ({source_id})")
        rebuilt = rebuild_manifest(decisions)
        print(f"\n      manifest lists {rebuilt} cards\n")
        return 1 if missing else 0

    # Giphy ids resolve in bulk; everything else already carries its own URL.
    giphy_ids = [s for s in missing if s.startswith("giphy:")]
    media: dict[str, str] = {}
    if giphy_ids:
        api_key = args.api_key or os.environ.get("GIPHY_API_KEY", "").strip()
        if not api_key:
            print("\n  No API key. Put GIPHY_API_KEY=... in .env, or pass --api-key.")
            print("  developers.giphy.com -> Create an App -> pick 'API' -> copy the API Key\n")
            return 2
        try:
            media = resolve_giphy(giphy_ids, GiphySource(api_key, "r"))
        except urllib.error.HTTPError as exc:
            print(f"\n  Giphy rejected the lookup ({exc.code}). Check GIPHY_API_KEY.\n")
            return 2

    fetched = copied = failed = 0
    total_bytes = 0
    for source_id, entry in sorted(missing.items()):
        # A card whose link expires keeps its bytes in curation/originals/. That copy is
        # authoritative: following a dead Discord URL would only 404.
        kept = ORIGINALS / entry["file"]
        if kept.is_file():
            (GIF_DIR / entry["file"]).write_bytes(kept.read_bytes())
            copied += 1
            print(f"      ● {entry['file']}  (kept copy)")
            continue

        url = media.get(source_id) or entry.get("url")
        if not url:
            # A Giphy id that the API no longer knows: the uploader deleted it. Nothing
            # to do about that here, but say so rather than failing silently.
            reason = "no longer on Giphy" if source_id.startswith("giphy:") else "no url recorded"
            print(f"      ✗ {entry['file']}  ({reason})")
            failed += 1
            continue
        try:
            # What the library remembers for a pasted link is the *page* — a Tenor
            # URL is HTML wrapping the GIF, so it has to be resolved the same way the
            # curator resolved it when the link was first pasted.
            if source_id.startswith("url:"):
                url = resolve_media(url)[0]
            total_bytes += _download(url, GIF_DIR / entry["file"])
            fetched += 1
            print(f"      ✓ {entry['file']}")
        except Exception as exc:  # noqa: BLE001 — one dead link must not stop the rest
            print(f"      ✗ {entry['file']}  ({type(exc).__name__}: {exc})")
            failed += 1

    pruned = 0
    if args.prune:
        keep = {entry["file"] for entry in targets.values()}
        for path in sorted(GIF_DIR.glob("*.gif")):
            if path.name not in keep:
                path.unlink()
                pruned += 1

    rebuilt = rebuild_manifest(decisions)
    # Measuring fills in sizes the library didn't have yet; keep them.
    store.update_library(lambda cards: [
        cards[sid].update({"w": c["w"], "h": c["h"]})
        for sid, c in decisions.items() if sid in cards and c.get("w")
    ])
    print(
        f"\n      {fetched} fetched ({total_bytes / 1_000_000:.1f} MB)"
        + (f", {copied} from kept copies" if copied else "")
        + (f", {failed} failed" if failed else "")
        + (f", {pruned} pruned" if pruned else "")
        + f" — manifest lists {rebuilt} cards\n"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
