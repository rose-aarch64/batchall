# Collection views and unit artwork: design

## Intent

A bigger collection needs ways to scan it that a flat table does not give, and each mini deserves a
picture. This adds three views of the same filtered collection and a default picture for every unit.

## What was built

- **Views.** Table (unchanged), Grouped by box, and Cards. A Compact density toggle applies to the two
  table views. The chosen view, the density and which groups are collapsed are remembered in the browser.
- **Grouped by box.** One collapsible header per box, with entry count, model count, total BV and a
  painted progress bar. Entries with no box come last. The Box column is hidden here because the header
  already names the box.
- **Cards.** A card per entry with the unit's artwork, name, type, tonnage, BV, quantity, painted count
  and box. Each card has `+ tray` and `Variant` buttons. Quantity, painted and box are edited in the
  table views. A sort control appears in this view, since there are no column headers to click.
- **Artwork.** Each unit's art comes from MekBay's public host. The catalog stores the art path. The
  server downloads an image the first time it is needed and caches it on disk. `/api/art/{unit uuid}`
  serves it. A unit with no art, or art that fails to load, shows a placeholder.

## Decisions and why

- **Table stays the default.** In a 900 px window a table shows about 12 rows, a compact table about 25
  and a card grid about 10. For scanning, sorting and editing a large collection the table views win.
  Cards are for browsing and for picking minis for a force.
- **Cache by art path, not by unit.** Variants of one chassis share a picture, so 231 sample units
  needed 204 images. This means fewer requests to the host and less disk.
- **Two downloads at a time, and a short memory of failures.** A page of cards asks for many images at
  once. The gate keeps that polite, and a broken link is not retried for five minutes.
- **Only PNG and JPEG are accepted,** checked by file signature, at most 5 MB, and only from the fixed
  MekBay art address. Art paths containing `..`, a leading slash, a backslash or a null are dropped at
  import and refused at download. The cache file name is a hash, never the path text.
- **Responses carry `X-Content-Type-Options: nosniff`** and the host check that guards the rest of the
  API also guards images.
- **The art path never leaves the server.** The API reports `has_art` only.
- **Images are lazy-loaded and have an empty `alt`,** since the name sits right beside them.
- **Browser storage is optional.** Every read and write is wrapped, and the page works with it blocked.
- **The unit art is a default, not the end goal.** A per-mini photo of the user's own painted model
  would be a separate feature (upload and storage). Nothing here prevents it.

## Limits

- Coverage is about 81% of all units. In MekBay's data that is 4,265 of 4,295 mechs, 1,227 of 1,256
  vehicles and 1,324 of 3,008 infantry entries. Units without art show a placeholder.
- The artwork belongs to its publishers. It is cached for personal viewing, `data/` is ignored by git,
  and it should not be redistributed.
- Variants of one chassis show the same picture.
- On a phone the table views scroll sideways inside their own box. The cards view, two across, is the
  phone-friendly one.

## Commands

```
python3 -m batchall art status      # how many images the collection needs and how many are cached
python3 -m batchall art prefetch    # download the missing ones ahead of time
python3 -m batchall catalog import  # re-run once after upgrading so units get their art paths
```
