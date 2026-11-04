# Box catalog: design

## Intent

The collection has a free-text `box` label on each entry. This feature adds a catalog of official
BattleTech boxes and ForcePacks so that typing "Clan inva" in a box field suggests
"BattleTech: Clan Invasion", and picking it in the Add units panel adds every miniature that box
contains. The point is to stop typing in a box's minis one at a time.

## Goals

- Suggest official box and ForcePack names (plus the user's own labels) in every box field.
- In the Add units panel, picking an official box adds all its minis, one entry per mini, labeled
  with the box name.
- Never guess silently. A mini that cannot be matched to exactly one MekBay unit is shown in a
  review step where the user picks a variant or skips it.
- Let the user swap any owned mini to another variant of the same chassis, because a plastic mini
  can stand for any variant.
- Keep the catalog local and refreshable with one command.

## Non-goals

- Official completeness. The data comes from BattleTechWiki (Sarna), a fan wiki. It can be wrong or
  out of date, so every box links to its source page.
- Prices, SKUs beyond what the page shows, release tracking.
- Alpha Strike card data, pilot cards, rulebooks or other box contents.
- Splitting copies of one unit across different boxes (one box label per collection entry stays).

## Source and findings

- `https://www.sarna.net/wiki/ForcePack` lists every ForcePack grouped by type (63 at the time of
  testing). Each links to a page with a "Contents" section.
- Core boxed sets have no machine-readable list. A short seed list is kept in code: A Game of Armored
  Combat, Beginner Box, Clan Invasion, Mercenaries. Each has a "Contents" section in the same layout.
- A pack page's Contents section is a nested list: a "... plastic miniatures" item whose children are
  the minis, and a dry-erase cards item whose children are card names written
  `First variant/Second variant`. Clan cards read `(Loki) Hellbringer Prime`, Inner Sphere cards read
  `FS9-H Firestarter`.
- Spike result over the 61 ForcePacks that parsed (267 minis): 79% resolved to exactly one MekBay unit
  using the first card variant, 1% more with a trailing parenthetical dropped, 7% a known chassis with
  no card, 12% no match (named-character minis, Elemental points, unusual vehicle names).

## Matching rules

Names are compared as sets of lowercase alphanumeric tokens, so "(Loki) Hellbringer Prime" equals
"Hellbringer (Loki) Prime".

For each mini on a page:

1. Strip a leading quantity: `(2) Heavy Tank Demolisher` is quantity 2. Number words in
   `Two Points ... of Elemental Battle Armor` give quantity 2.
2. Find the first card variant whose tokens contain all the mini's tokens.
3. Look for exactly one MekBay unit with the same token set as that card name. Match: `resolved`.
4. Otherwise drop a trailing `(...)` group from the card and try again: `approx`.
5. Otherwise, if the card names a whole chassis and that chassis has exactly one standard-model unit
   (empty model or "Standard"), use it: `resolved`.
6. Otherwise, if the mini's name matches a MekBay chassis: `chassis_only`. Candidates are every
   variant of that chassis.
7. Otherwise, `no_match`. Candidates are units whose name contains all the mini's tokens, capped.

Only `resolved` and `approx` units are added without review. `approx` is added, but flagged so the
user can see it.

## Data model

- `boxes(id, name, kind, section, source_url, imported_at)`. `kind` is `core` or `forcepack`.
- `box_items(id, box_id, position, mini_name, card_variant, qty, unit_uuid, status, note)`.
  `unit_uuid` is set only for `resolved` and `approx`.
- Page HTML is cached in `data/box_pages/` so a re-parse needs no network. Importing replaces all
  rows. Collection rows are never touched by an import.

## Flows

**Refresh.** `python3 -m bt boxes import` fetches the list page and seed pages (pausing between
requests), parses, matches and stores. It prints a summary and the packs that did not parse.

**Add a box.** `POST /api/box-catalog/{id}/add` has two modes:
- `{}` processes every item. Resolved and approx items are added with the box name as the label and
  the item's quantity. Every other item is returned in `review` with candidates and is not added.
- `{"only": {item_id: unit_uuid | null}}` processes just those items, so the second call never adds
  the matched items again. A unit adds that item with that unit. `null` skips it.
- A non-empty `review` is a normal result. The UI shows the review panel and posts the choices.
- Adding the same box twice adds the quantities again. The UI asks before repeating an add.

**Card variants.** Every card variant that names a mini, on either side of the card, is stored with
the box item (`box_items.card_units`, a JSON list of unit uuids). The variant picker lists these first,
flagged `on_card`, when the entry's box label is an official box whose item has the same chassis.
Each mini still counts once. The other side of a card is an alternative for that one mini, not a
second mini, so nothing is added twice and BV totals stay honest. Treating a mini as flexible between
its card sides in the builder is a possible later step and is not built.

**Swap variant.** `GET /api/collection/{id}/variants` lists units with the same chassis.
`POST /api/collection/{id}/swap` with `unit_uuid` moves the entry to that unit. If the user already
owns the target, quantities and painted counts are merged into it and the old entry is removed.
Saved forces that used the old entry move to the target.

**Suggestions.** Box fields show official box names and the user's own labels. In the Add panel's box
field, an official entry adds the box when picked. A user label only fills the field. In the table
rows and the toolbar filter, an official name only fills the field.

## Constraints and decisions made by the builder

- Python standard library and the existing stack only. No new dependencies.
- Requests to Sarna identify the tool, wait about 0.4 seconds apart, and use the page cache.
- The seed list of core boxes and the "standard model" rule are my decisions, not the user's.
- Official and own suggestions are visually distinguished and sorted official-first when the typed
  text matches both.

## As built: what the real pages changed

- Some core box pages list their minis in `<dl><dd>` instead of nested bullets, so both are read as
  list items.
- Card sides: the first side of every card is tried first. A mini with no first-side card (the
  Awesome in A Game of Armored Combat shares a card with the BattleMaster) falls back to a second side.
- Quantity can be written `(2) Heavy Tank Demolisher`, `two Galleon Light Tanks` or
  `Two Points (Bases) of Elemental Battle Armor`. Prose notes in the minis list are skipped.
- Candidates for a review item come from the chassis, then from units containing all the mini's
  words, then from units sharing its first word.
- Result on the cached pages: 64 boxes and 293 minis. 243 resolved, 3 approx, 27 chassis only, 20 no
  match. Three pages list no minis in a readable form and are skipped: Inner Sphere Battle Armor
  Platoon, Clan Elemental Star and Battlefield Support: Emplacements.
- The suggestion lists ignore Enter and the arrow keys until the list matches what was typed, so a
  fast Enter cannot pick from an out-of-date list.

## Left to the builder

Exact UI wording, review panel layout, the candidate cap, and the token tie-breaking details.
