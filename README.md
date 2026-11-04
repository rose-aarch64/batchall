# BattleTech Collection

Track the BattleTech miniatures you own and build forces from them at a target Battle Value (BV).
Python 3.12 standard library only. No packages to install.

## Setup

```
python3 -m bt catalog import     # downloads unit, faction and era data from MekBay (about 27 MB)
python3 -m bt boxes import       # builds the official box and ForcePack catalog from BattleTechWiki
python3 -m bt serve              # web UI at http://localhost:8765/
```

`boxes import` needs the unit catalog first. It pauses between wiki requests and caches the pages in
`data/box_pages/`, so running it again is fast. Use `--refresh-pages` to download them again.

The database lives in `data/battletech.db`. Set `BT_DB` to use a different file, or `BT_DATA_DIR`
to move the whole data folder.

## Running the server

```
python3 -m bt serve                          # http://localhost:8765/
python3 -m bt serve --port 9000              # different port
python3 -m bt serve --host 0.0.0.0           # reachable from other machines (see warning)
BT_DB=/path/to/other.db python3 -m bt serve  # use a different database
```

The server prints its address and runs until you press Ctrl+C. It uses only the standard library, so
there is nothing to install. The default port is 8765 and the default host is `127.0.0.1`, which
means only the machine running it can connect.

Run `catalog import` once before the first start. Without it the UI loads but the catalog is empty
and the UI shows "Catalog not loaded". The collection is kept when you re-import the catalog.

The server has no login. Only use `--host 0.0.0.0` on a network you trust, since anyone who can reach
the port can read and change the collection.

The `serve` command ignores the global `--db` option. To point the server at another database, set
`BT_DB` (or `BT_DATA_DIR`) as shown above.

### Importing in the UI

The Import tab accepts a pasted list or a file (`.txt`, `.csv`, `.tsv`) that fills the text box so you
can review it first. Two list formats are supported:

- **Unit names**, one per line: `Atlas AS7-D`, `2x Locust LCT-1V`, `Shadow Hawk SHD-2H x3`.
- **MUL ids or links**, one per line: `1234`, `2x MUL 1234`, or a masterunitlist.info
  `/Unit/Details/1234/...` link. A CSV row that starts with the id also works. About 1,500 catalog
  units have no MUL id and can only be imported by name.

Near matches, ids shared by several catalog entries, and lines with no match are listed for you to
fix. Nothing is guessed.

## What it does

- **Collection.** Unit, quantity owned, quantity painted, and an optional box label. Add from the
  full catalog or import a plain list (`2x Locust LCT-1V`). Near matches are suggested, never guessed.
- **Adding in the UI.** Type a unit name in the Add units panel and pick from the suggestion list
  (arrow keys plus Enter, or click). Each pick adds one copy and tags it with the panel's box label.
- **Box labels.** A label per entry for the box or set the model came in. Type your own, or let an
  official box set it. Filter the table by box, and build a force from one box with `--box`. One
  label per entry: copies of a unit from two different boxes share a label.
- **Official boxes.** Typing in a box field suggests official boxes and ForcePacks next to your own
  labels. In the Add units panel, picking an official box adds every mini that comes in it. Minis
  that do not match exactly one unit go to a review step where you pick a variant or skip. In a
  table row or the filter, an official name only fills the label.
- **Views.** Switch between Table and Cards. The Grouping Off/On switch turns grouping on and off,
  and the dropdown beside it picks the field. Grouping splits either view into collapsible sections with
  counts, total BV and a painted progress bar. The fields are box, era (by debut year), era (every era the unit is available in, so a mini can appear under many), faction
  group, weight class, type, tech base, role and paint status. Turning grouping off keeps your field,
  and picking a field turns grouping on. Faction groups use the factions that field a unit in the
  first era it appears, so later captures do not move a Clan mech into the Inner Sphere. Clan-tech
  units also join every Clan group that ever fields them. A mini can sit in several groups, since
  factions share units. Sort by name, type, BV, tons or debut year in either
  view. Compact density shrinks the table rows. Your choices are remembered.
- **Named minis.** Open a unit's details and use Your copies to name individual minis. Each gets a
  note, a faction and a company for the force it is painted for, and an optional tag. On a card a
  named mini has a crimson strip with its company and faction, and the pencil opens its edit boxes.
  The Copy tag, Mini faction and Mini company boxes filter by them. Mini faction is the force you
  painted a mini for, which is separate from the Faction dropdown (who fields a unit in the game data).
  A named mini can also be marked painted, kept in its own box (blank means its unit's box), and
  switched to another variant on its own with its Variant button. The other minis of that unit stay
  as they were. Box filters and box grouping still go by the unit's box.
- **Artwork.** Cards show each unit's illustration, downloaded once from MekBay and cached under
  `data/art/`. Units with no art show a placeholder. `python3 -m bt art prefetch` downloads the images
  for your whole collection ahead of time.
- **Variants.** The Variant button on a row switches a mini to another variant of the same chassis,
  since a plastic mini can stand for any variant. If you already own the target, the two merge. For a
  mini that came from an official box, the variants on that box's cards (both sides) are listed first
  under "On your box's card".
- **Force builder.** Pick units you own to fit a BV ceiling for a lance (4), augmented lance (6),
  Star (5), Level II (6), Binary (10) or company (12), or any custom size. Optional filters: faction,
  era, tech base, painted only, and a mix of unit kinds (`mech=2,vehicle=1,ba=1`).
- **Combined arms.** Mechs, vehicles (tanks and VTOLs), battle armor, conventional infantry,
  ProtoMechs and aerospace fighters are separate kinds that can be mixed freely.
- **Saved forces.** Named forces with a formation, target and units, shared by the UI and the CLI.
- **Themes.** The `battletech` Claude skill turns a plain-language theme into per-unit weights and
  passes them to the builder. The UI has the same builder without themes.

## How the builder chooses

The search is exact, not greedy. For each unit kind it solves a 0/1 knapsack over (unit count,
total BV), then combines kinds for every mix that fits the formation. A build never exceeds the
target. It lands within `--slack` percent below the target (default 3) when possible. With no theme
the highest BV in that window wins; with a theme the highest weighted score wins. If nothing lands
in the window, the best fit at or under the target is returned and flagged.

## CLI

All commands print JSON. Add `--text` to `build` and `collection list` for readable output.

```
python3 -m bt collection list --text
python3 -m bt collection add --name "Atlas AS7-D" --qty 1 --painted 1
python3 -m bt collection import --file mylist.txt
python3 -m bt collection import --mul --file mul-ids.txt
python3 -m bt build --target 5000 --formation star --mix "mech=3,vehicle=1,ba=1" --variants 3 --text
python3 -m bt build --target 2000 --formation lance --faction "Draconis Combine" --era "Clan Invasion" --text
python3 -m bt forces save --name "Scrappy Lance" --formation lance --target 2000 --units 12:1,15:2
python3 -m bt forces list
```

`python3 -m bt <command> --help` lists every option.

## Data

Unit stats, BV, and faction availability come from MekBay's public data files at `db.mekbay.com`
(MekBay is open source: https://github.com/MegaMek/mekbay). Check MekBay's terms before
redistributing the data. The downloaded files are cached in `data/catalog/` and are not committed.

Formation sizes follow BattleTechWiki: a lance is four units, an augmented lance adds two vehicles or
battle armor squads to four BattleMechs, a Star is five, a Binary is two Stars, and a Level II is six
Level I units.

Unit artwork also comes from MekBay's public host. It covers about 81% of units (nearly all mechs and
vehicles) and belongs to its publishers. It is cached for your own use, kept out of git, and should not
be redistributed. After upgrading from an older version, run `python3 -m bt catalog import` once so
units get their art paths.

Box contents come from BattleTechWiki, a fan-maintained source, so they can be wrong or incomplete.
Each box keeps a link to its page. The variant added for a mini is the first one printed on the box's
Alpha Strike card. In testing, about 83% of minis matched exactly one MekBay unit on their own.
Core boxed sets come from a short list in `bt/boxes.py`; ForcePacks come from the wiki's list.
Design notes are in `docs/box-catalog-design.md`.

## Limits

- Classic BattleTech BV only. Alpha Strike point values are stored but unused.
- BV is the unit's base value with no pilot skill adjustment.
- Battle armor is one entry per squad size (MekBay lists `Sqd4`, `Sqd5`, `Sqd6` separately), and each
  entry fills one slot.
- With a faction or era filter, units with no availability data are skipped and counted in the output.

## License

The code in this repository is MIT licensed (see `LICENSE`). That covers the code only. The MekBay
data, unit artwork, lore text and BattleTechWiki pages the tool downloads belong to their owners, are
not part of this repository, and are not covered by the MIT license.

## Tests

```
python3 -m unittest discover -s tests
```

The builder tests compare results to a brute-force search on a small collection.
