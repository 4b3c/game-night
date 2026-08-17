#!/usr/bin/env python3
"""Rebuild app/static/gifs/manifest.json from whatever .gif files are in the folder.

Run this after dropping your own real GIFs in:

    python scripts/scan_gifs.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GIF_DIR = ROOT / "app" / "static" / "gifs"


def prettify(stem: str) -> str:
    words = re.split(r"[\s_\-]+", stem)
    return " ".join(w.capitalize() for w in words if w) or stem


def main() -> None:
    if not GIF_DIR.exists():
        raise SystemExit(f"{GIF_DIR} does not exist")

    files = sorted(p for p in GIF_DIR.iterdir() if p.suffix.lower() == ".gif")
    if not files:
        raise SystemExit(f"no .gif files found in {GIF_DIR}")

    manifest_path = GIF_DIR / "manifest.json"
    # Keep what the curator wrote: `sets` is what puts a card in the Normal or 18+ pile,
    # and a rescan must never silently reset it.
    known: dict[str, dict] = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text())
            for entry in previous.get("gifs", []):
                if entry.get("file"):
                    known[entry["file"]] = entry
        except json.JSONDecodeError:
            print("⚠️  existing manifest.json was unreadable — starting fresh")

    entries = []
    for path in files:
        old = known.get(path.name, {})
        sets = old.get("sets") or ["adult" if old.get("rating") == "adult" else "normal"]
        entry = {
            "id": old.get("id") or path.stem,
            "file": path.name,
            "label": old.get("label") or prettify(path.stem),
            "sets": sets,
        }
        if old.get("source"):
            entry["source"] = old["source"]
        entries.append(entry)

    manifest = {"generated": False, "count": len(entries), "gifs": entries}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    per_mode: dict[str, int] = {}
    for entry in entries:
        for name in entry["sets"]:
            per_mode[name] = per_mode.get(name, 0) + 1
    print(f"manifest.json rebuilt: {len(entries)} gifs")
    for mode, count in sorted(per_mode.items()):
        flag = "" if count >= 56 else "   (needs 56 for 8 players)"
        print(f"  {mode:<11} {count}{flag}")
    if len(entries) < 40:
        print("⚠️  fewer than 40 gifs — with 8 players (56 cards in hands at once) the deck will recycle often")


if __name__ == "__main__":
    main()
