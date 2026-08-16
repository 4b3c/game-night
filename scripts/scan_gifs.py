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

    entries = [{"id": p.stem, "file": p.name, "label": prettify(p.stem)} for p in files]
    manifest = {"generated": False, "count": len(entries), "gifs": entries}
    (GIF_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest.json rebuilt: {len(entries)} gifs")
    if len(entries) < 40:
        print("⚠️  fewer than 40 gifs — with 8 players (56 cards in hands at once) the deck will recycle often")


if __name__ == "__main__":
    main()
