---
name: glossary-forge
description: Builds a translation glossary (term base) from translations you already have — item and character names, places, recurring proper nouns — by mining name keys, "translation (Original)" pairs and statistical alignment that also works for languages without spaces such as Thai, Japanese and Chinese; then finds terms translated inconsistently across files and exports CSV, TBX or JSON. Use when someone wants a glossary or term base for a game mod, app or localization project, wants names translated consistently, or asks why the same term is translated differently in different places.
---

# Glossary Forge

Turn an existing translation into a term base — and find every place where the same term was translated differently.

Part of the Localization Kit. Reads `.l10n.json` (create it with `loc-translate init`, or let this skill auto-detect) and writes the shared `l10n/glossary.csv` that `loc-translate` and `loc-qa` use.

## Non-negotiable rules

1. **Candidates are suggestions.** Never mark a term `approved` unless the user approves it, or the evidence is overwhelming (name-key pairs used consistently everywhere) and you say so.
2. Never rewrite translation files from this skill. It reports inconsistencies; fixes go through `loc-translate` (re-translate specific keys) or the user.
3. Keep the user's approved terms — `mine` never overwrites them.

## 1. Mine

```bash
python3 scripts/glossary.py mine
```

Three sources, in order of confidence:
1. **Name keys** (`Item.Name`, `*.DisplayName`, `title`): the whole value is a term — e.g. *Coconut milk → นมมะพร้าว*.
2. **"Translation (Original)" pairs** left inside translations — e.g. *อีไล (Eli)*. In Thai/Japanese/Chinese the span may include extra words; check the boundary.
3. **Recurring proper nouns** (character, place and faction names, used ≥ 3 times) aligned to the target text by contrast: the rendering that appears in nearly every translation of a string containing the term, and rarely elsewhere. Works with or without spaces.

New rows are added to `l10n/glossary.csv` with `status=candidate` and a note showing how each was found, its confidence and coverage.

## 2. Review with the user

```bash
python3 scripts/glossary.py report          # l10n/glossary.html — searchable, sortable, consistency bars
```

Walk the user through the candidates that matter most (most used first). Approve, correct or reject:

```bash
python3 scripts/glossary.py add "Javi" "ฮาวี" --type character
python3 scripts/glossary.py add "Leo" --status rejected
```

Or edit `l10n/glossary.csv` directly (columns: `source,target,type,status,case_sensitive,note,count`).

## 3. Check consistency

```bash
python3 scripts/glossary.py check                 # candidates + approved
python3 scripts/glossary.py check --approved-only --strict   # CI: exit 1 on any inconsistency
```

For each term it lists how often the agreed translation is used (e.g. 3/5), the other renderings it found, and the exact strings that differ. Summarise these for the user as decisions to make ("*Difficulty* is ระดับความยาก in 3 places but ความโหด in 2 — which one?"). Once decided, re-translate the affected keys with `loc-translate prepare --keys …`.

## 4. Export

```bash
python3 scripts/glossary.py export --format tbx    # for CAT tools (Trados, memoQ, OmegaT…)
python3 scripts/glossary.py export --format json   # simple source → target map
```

## Deliver

1. How many terms were mined, by type, and how many need review.
2. The top inconsistencies as decisions, with examples.
3. Files: `l10n/glossary.csv`, `l10n/glossary.html`, and any exports.
4. Next step: approve terms, then `loc-translate` uses them automatically and `loc-qa` enforces them.
