#!/usr/bin/env python3
"""Import the real Cards Against Humanity prompts (their black cards) into the deck.

    python scripts/import_cah_prompts.py --check     # say what would happen
    python scripts/import_cah_prompts.py             # do it

Cards Against Humanity publish their own game under Creative Commons
**BY-NC-SA 4.0** — the licence is on cardsagainsthumanity.com next to the free PDF
download. That is what makes this legal, and it comes with three conditions this
script and the repo have to keep:

    Attribution    the cards are credited to Cards Against Humanity LLC, in
                   LICENSE-PROMPTS.md and in every row's `source` field
    NonCommercial  fine for a party game nobody charges for; not fine if that changes
    ShareAlike     the prompt data is redistributed under the same licence

Two filters decide what comes in, and both matter:

  * **Official packs only.** The source dataset bundles fan-made packs alongside CAH's
    own. Those are somebody else's work and are not covered by CAH's licence grant, so
    they are skipped.
  * **One blank only.** A "pick 2" black card wants two white cards played together, and
    this game deals one GIF per person per round — there is nowhere to put the second
    answer. Those cards are counted and skipped rather than silently mangled.

Everything imported lands in the **18+** pile, because that is what these cards are.
They only appear when the host turns off "keep it clean". Retag any you disagree with in
the curator, the same as any other prompt.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import curation_store as store  # noqa: E402 — needs the path above

SOURCE_URL = "https://raw.githubusercontent.com/crhallberg/json-against-humanity/latest/cah-all-full.json"
CREDIT = "cah"


def normalise(text: str) -> str:
    """CAH's blank is a run of underscores; ours is exactly three, which is what the
    client draws a ruled line for. Collapse whitespace while we're here."""
    text = re.sub(r"_+", "___", text or "")
    text = text.replace("<br>", " ").replace("<br/>", " ")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def key(text: str) -> str:
    """For spotting a prompt we already have, whoever wrote it."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def fetch(path: Path | None) -> list:
    if path:
        return json.loads(path.read_text())
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "gifs-against-humanity/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report without writing anything")
    parser.add_argument("--from-file", type=Path, default=None, help="a downloaded copy, instead of fetching")
    parser.add_argument("--sets", default="adult", help="comma-separated piles (default: adult)")
    args = parser.parse_args()

    packs = fetch(args.from_file)
    official = [p for p in packs if p.get("official")]
    skipped_packs = len(packs) - len(official)

    wanted, multi_blank = [], 0
    for pack in official:
        for card in pack.get("black") or []:
            if int(card.get("pick", 1)) != 1:
                multi_blank += 1
                continue
            text = normalise(card.get("text", ""))
            if text:
                wanted.append((text, pack["name"]))

    # Two different kinds of duplicate, worth counting apart: one means the deck already
    # had that line, the other means CAH ship the same card in several of their packs.
    existing = store.prompts()
    already = {key(row.get("text", "")) for row in existing.values()}
    seen = set()
    fresh, in_deck, repeated = [], 0, 0
    for text, pack_name in wanted:
        marker = key(text)
        if marker in already:
            in_deck += 1
            continue
        if marker in seen:
            repeated += 1
            continue
        seen.add(marker)
        fresh.append((text, pack_name))

    sets = [s.strip() for s in args.sets.split(",") if s.strip()]
    print(f"\n  🃏 Cards Against Humanity — CC BY-NC-SA 4.0, official packs only")
    print(f"      packs:      {len(official)} official, {skipped_packs} fan-made packs skipped")
    print(f"      prompts:    {len(wanted)} with a single blank")
    print(f"      skipped:    {multi_blank} needing two or three answers this game can't deal")
    print(f"      repeats:    {repeated} cards CAH ship in more than one pack")
    print(f"      already in: {in_deck} the deck already had")
    print(f"      to import:  {len(fresh)} into {', '.join(sets)}")
    if args.check:
        print("\n  (--check, nothing written)\n")
        return 0
    if not fresh:
        print("\n  nothing to do\n")
        return 0

    stamp = store.now()

    def add(rows: dict) -> None:
        number = 1
        for text, pack_name in fresh:
            while f"p{number:03d}" in rows:
                number += 1
            rows[f"p{number:03d}"] = {
                "text": text,
                "blanks": 1,
                "sets": list(sets),
                "added": stamp,
                # Kept so these are always separable from the originals — which is what
                # makes the share-alike term something you can honour later if the two
                # ever need to live in different files.
                "source": CREDIT,
                "pack": pack_name,
            }

    store.update_prompts(add)
    print(f"\n  imported — the deck now has {len(store.prompts())} prompts\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
