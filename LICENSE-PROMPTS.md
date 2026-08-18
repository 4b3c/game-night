# Where the prompts come from, and how they're licensed

`curation/prompts.json` holds two different things, and they are not under the same
terms. Every row records which it is, in its `source` field.

## The originals — no `source` field

Written for this game. They are covered by the repository's own licence.

## Cards Against Humanity — `"source": "cah"`

The black cards from Cards Against Humanity's **official** packs, imported by
`scripts/import_cah_prompts.py`. Each row also records the `pack` it came from.

> Cards Against Humanity is © Cards Against Humanity LLC, released under a
> [Creative Commons BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)
> licence. This project is not affiliated with or endorsed by Cards Against Humanity LLC.

That licence is what makes the import legal, and it carries three conditions:

| | |
|---|---|
| **Attribution** | the credit above, kept with the cards |
| **NonCommercial** | this game is free to play. Charging for it — or for anything built on this prompt data — is not permitted under this licence |
| **ShareAlike** | the prompt data is redistributed under CC BY-NC-SA 4.0, the same licence it arrived under |

Only CAH's own packs were imported. The dataset the script reads also bundles fan-made
packs; those are other people's work and are **not** covered by CAH's licence grant, so
the script skips them. It also skips black cards that call for two or three white cards,
because this game deals one GIF per person per round.

If you ever want the two kinds of prompt in separate files — so the share-alike term
stops reaching across your own writing — the `source` field is what makes that a
one-line filter.
