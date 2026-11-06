"""Command line interface. Every command prints JSON unless ``--text`` is given.

The skill drives this interface, so output shapes are part of the contract.
"""

import argparse
import json
import sys
from pathlib import Path

from . import art, availability, boxes, builder, catalog, collection, config, copies, db, export, formations, forces
from .kinds import KIND_LABELS, KINDS


def _emit(data):
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def _read_weights(text):
    if not text:
        return None
    raw = Path(text[1:]).read_text(encoding="utf-8") if text.startswith("@") else text
    weights = json.loads(raw)
    if not isinstance(weights, dict):
        raise ValueError("weights must be a JSON object mapping collection id to a number")
    return weights


def _csv_ints(text):
    return [int(x) for x in text.split(",") if x.strip()] if text else []


def _formation_key(name):
    """Canonical key for a known formation; free-form names are kept as typed."""
    try:
        return formations.get_formation(name).key
    except ValueError:
        return name


def _unit_line(u):
    qty = f"{u['qty']}x " if u.get("qty", 1) != 1 else ""
    return f"{qty}{u['name']} [{u['kind']}, {u['tons']:g}t, {u['bv']} BV]"


def _render_build(result):
    lines = []
    pool = result["pool"]
    scope = []
    if pool["faction"]:
        scope.append(pool["faction"]["name"])
    if pool["era"]:
        scope.append(pool["era"]["name"])
    if pool["painted_only"]:
        scope.append("painted only")
    if pool.get("proxy"):
        scope.append("proxying variants")
    if pool.get("c3"):
        scope.append(f"{pool['c3']} only")
    head = f"{result['formation'] or 'custom'} ({result['size']} units)"
    if scope:
        head += " | " + ", ".join(scope)
    lines.append(head)
    for i, b in enumerate(result["builds"], 1):
        lines.append("")
        lines.append(
            f"build {i}: {b['total_bv']} / {b['target_bv']} BV "
            f"(gap {b['gap']}, seed {b['seed']}"
            + ("" if b["in_window"] else ", outside slack window")
            + ")"
        )
        for u in b["units"]:
            proxied = f"  (your {u['owned_as']})" if u.get("owned_as") else ""
            kept = u.get("included", 0)
            if kept:
                proxied += "  (included)" if kept == u["qty"] else f"  ({kept} included)"
            lines.append("  " + _unit_line(u) + proxied + f"  id {u['collection_id']}")
    if pool["excluded_unknown_availability"]:
        lines.append("")
        lines.append(
            f"note: {pool['excluded_unknown_availability']} owned unit(s) skipped, "
            "no faction availability data"
        )
    return "\n".join(lines)


def cmd_catalog(args, conn):
    if args.action == "import":
        if args.from_dir is None:
            catalog.download(progress=lambda m: print(m, file=sys.stderr))
        counts = catalog.load(conn, args.from_dir)
        _emit({"imported": counts, "status": catalog.status(conn)})
        print("Optional next step: python3 -m batchall art prefetch  "
              "(downloads unit art and faction logos ahead of time)", file=sys.stderr)
    elif args.action == "status":
        _emit(catalog.status(conn))
    else:
        _emit(catalog.search(conn, " ".join(args.query), kind=args.kind, limit=args.limit))


def cmd_meta(args, conn):
    _emit(
        {
            "catalog": catalog.status(conn),
            "kinds": {k: KIND_LABELS[k] for k in KINDS},
            "formations": formations.describe_all(),
            "factions": availability.list_factions(conn),
            "eras": availability.list_eras(conn),
        }
    )


MATERIALS = ("CGL Plastic", "Resin", "Metal")


def _material(text):
    """Match a material name ignoring case. "none" or an empty string clears it. None leaves it alone."""
    if text is None:
        return None
    wanted = text.strip().lower()
    if wanted in ("", "none", "no material"):
        return ""
    for name in MATERIALS:
        if name.lower() == wanted:
            return name
    raise ValueError(f"material must be one of {', '.join(MATERIALS)}, or none to clear it")


def cmd_collection(args, conn):
    a = args.action
    if a == "list":
        items = collection.list_collection(
            conn,
            q=args.q,
            kind=args.kind,
            painted_only=args.painted_only,
            faction=args.faction,
            era=args.era,
            tech_base=args.tech_base,
            box=args.box,
        )
        if args.text:
            for i in items:
                box = f"  <{i['box']}>" if i["box"] else ""
                print(
                    f"{i['id']:>4}  {i['qty']}x ({i['painted']} painted)  "
                    f"{_unit_line({**i, 'qty': 1})}{box}"
                )
        else:
            _emit(items)
    elif a == "boxes":
        _emit(collection.list_boxes(conn))
    elif a == "variants":
        _emit(collection.variants(conn, args.id))
    elif a == "swap":
        target = args.uuid
        if not target:
            wanted = (args.name or "").strip().lower()
            matches = [v for v in collection.variants(conn, args.id) if v["name"].lower() == wanted]
            if len(matches) != 1:
                _emit({"error": "name did not match exactly one variant", "variants": [v["name"] for v in collection.variants(conn, args.id)]})
                sys.exit(2)
            target = matches[0]["uuid"]
        _emit(collection.swap(conn, args.id, target))
    elif a == "add":
        uuid = args.uuid
        if not uuid:
            hits = catalog.search(conn, args.name, limit=10)
            exact = [h for h in hits if h["name"].lower() == args.name.strip().lower()]
            hits = exact or hits
            if len(hits) != 1:
                _emit({"error": "name did not match exactly one unit", "candidates": [h["name"] for h in hits]})
                sys.exit(2)
            uuid = hits[0]["uuid"]
        _emit(
            collection.add(
                conn,
                uuid,
                qty=1 if args.qty is None else args.qty,
                painted=args.painted or 0,
                notes=args.notes or "",
                box=args.box or "",
            )
        )
    elif a == "set":
        _emit(
            collection.update(
                conn, args.id, qty=args.qty, painted=args.painted, notes=args.notes, box=args.box
            )
        )
    elif a == "mini":
        if args.id is None:
            raise ValueError("mini needs --id with a mini id (the ids in the minis list of collection list)")
        fields = {
            "note": args.note,
            "tag": args.tag,
            "faction": args.faction,
            "company": args.company,
            "box": args.box,
            "material": _material(args.material),
            "painted": None if args.painted is None else bool(args.painted),
        }
        _emit(copies.update(conn, args.id, **fields))
    elif a == "remove":
        collection.remove(conn, args.id)
        _emit({"removed": args.id})
    elif a == "import":
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
        _emit(
            collection.import_text(
                conn, text, painted=bool(args.painted), box=args.box or "", mode="mul" if args.mul else "name"
            )
        )


def cmd_art(args, conn):
    """Unit artwork and faction logos. The logos are reported under their own key, beside the unit numbers."""
    catalog.fill_missing_faction_icons(conn)
    progress = lambda m: print(m, file=sys.stderr)  # noqa: E731
    if args.action == "status":
        _emit({**art.status(conn), "faction_icons": art.faction_icon_status(conn)})
    else:
        units = art.prefetch(conn, progress=progress)
        _emit({**units, "faction_icons": art.prefetch_faction_icons(conn, progress=progress)})


def _resolve_box_id(conn, args):
    if args.id:
        return args.id
    hits = boxes.search_boxes(conn, args.name or "", limit=10)
    exact = [h for h in hits if h["name"].lower() == (args.name or "").strip().lower()]
    hits = exact or hits
    if len(hits) != 1:
        _emit({"error": "give --id or a --name that matches exactly one box", "candidates": [h["name"] for h in hits]})
        sys.exit(2)
    return hits[0]["id"]


def cmd_boxes(args, conn):
    a = args.action
    if a == "import":
        fetcher = boxes.Fetcher(refresh=args.refresh_pages)
        _emit(boxes.import_boxes(conn, fetcher, progress=lambda m: print(m, file=sys.stderr)))
    elif a == "list":
        _emit(boxes.search_boxes(conn, " ".join(args.query), limit=args.limit))
    elif a == "show":
        box = boxes.get_box(conn, _resolve_box_id(conn, args))
        if args.text:
            print(f"{box['name']}  ({box['kind']}, {box['section']})\n{box['source_url']}")
            for item in box["items"]:
                unit = item["unit"]["name"] if item["unit"] else "-"
                print(f"  {item['qty']}x {item['mini_name']:<34} {item['status']:<13} {unit}")
        else:
            _emit(box)
    elif a == "add":
        only = json.loads(args.only) if args.only else None
        _emit(boxes.add_box(conn, _resolve_box_id(conn, args), only=only))


def cmd_build(args, conn):
    result = builder.build(
        conn,
        target_bv=args.target,
        formation=args.formation,
        size=args.size,
        mix=formations.parse_mix(args.mix) if args.mix else None,
        faction=args.faction,
        era=args.era,
        tech_base=args.tech_base,
        painted_only=args.painted_only,
        weights=_read_weights(args.weights),
        exclude=_csv_ints(args.exclude),
        kinds=[x.strip() for x in args.kinds.split(",")] if args.kinds else None,
        slack_pct=args.slack,
        variants=args.variants,
        seed=args.seed,
        box=args.box,
        proxy=args.proxy,
        include=_csv_ints(args.include),
        c3=args.c3,
    )
    if args.text:
        print(_render_build(result))
    else:
        _emit(result)


def cmd_forces(args, conn):
    a = args.action
    if a == "list":
        _emit(forces.list_forces(conn))
    elif a == "show":
        _emit(forces.get(conn, args.id))
    elif a == "delete":
        forces.delete(conn, args.id)
        _emit({"deleted": args.id})
    elif a == "export":
        out = export.export(forces.get(conn, args.id))
        if args.format != "mul":
            _emit({"url": out["url"]})
        elif args.out:
            Path(args.out).write_text(out["content"], encoding="utf-8")
            _emit({"wrote": args.out})
        else:
            sys.stdout.write(out["content"])
    elif a == "save":
        units = []
        for part in args.units.split(","):
            if not part.strip():
                continue
            cid, _, qty = part.partition(":")
            units.append({"collection_id": int(cid), "qty": int(qty or 1)})
        faction = availability.resolve_faction(conn, args.faction)
        era = availability.resolve_era(conn, args.era)
        _emit(
            forces.save(
                conn,
                args.name,
                _formation_key(args.formation),
                units,
                target_bv=args.target,
                faction_id=faction["id"] if faction else None,
                era_id=era["id"] if era else None,
                notes=args.notes or "",
                force_id=args.id,
            )
        )


def cmd_serve(args, conn):
    from . import server

    conn.close()
    server.serve(port=args.port, host=args.host, db_path=args.db)


def make_parser():
    p = argparse.ArgumentParser(prog="batchall", description="Batchall: a BattleTech miniature collection manager and force builder")
    p.add_argument("--db", help=f"database path (default {config.DB_PATH})")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("catalog", help="manage the unit catalog")
    c.add_argument("action", choices=["import", "status", "search"])
    c.add_argument("query", nargs="*", help="search terms")
    c.add_argument("--from-dir", help="load from already-downloaded files instead of downloading")
    c.add_argument("--kind", choices=KINDS)
    c.add_argument("--limit", type=int, default=25)
    c.set_defaults(func=cmd_catalog)

    m = sub.add_parser("meta", help="formations, factions, eras, kinds")
    m.set_defaults(func=cmd_meta)

    col = sub.add_parser("collection", help="manage owned units")
    col.add_argument(
        "action", choices=["list", "add", "set", "mini", "remove", "import", "boxes", "variants", "swap"]
    )
    col.add_argument("--id", type=int, help="collection entry id (set, remove); mini id for mini")
    col.add_argument("--note", help="mini: paint note")
    col.add_argument("--tag", help="mini: copy tag")
    col.add_argument("--company", help="mini: company it is painted for")
    col.add_argument("--material", help="mini: CGL Plastic, Resin, Metal, or none to clear")
    col.add_argument("--uuid", help="catalog uuid (add)")
    col.add_argument("--name", help="exact unit name, e.g. 'Atlas AS7-D' (add)")
    col.add_argument("--qty", type=int, default=None)
    col.add_argument("--painted", type=int, default=None)
    col.add_argument("--notes")
    col.add_argument(
        "--box",
        help="box label: sets it on add, set and import; filters by substring on list",
    )
    col.add_argument("--file", default="-", help="import source, '-' for stdin")
    col.add_argument("--mul", action="store_true", help="import: lines are MUL ids or unit links, not names")
    col.add_argument("--q", help="text filter (list)")
    col.add_argument("--kind", choices=KINDS)
    col.add_argument("--faction")
    col.add_argument("--era")
    col.add_argument("--tech-base", choices=["Inner Sphere", "Clan"])
    col.add_argument("--painted-only", action="store_true")
    col.add_argument("--text", action="store_true", help="readable output (list)")
    col.set_defaults(func=cmd_collection)

    ar = sub.add_parser("art", help="unit artwork and faction logo cache")
    ar.add_argument("action", choices=["status", "prefetch"])
    ar.set_defaults(func=cmd_art)

    bx = sub.add_parser("boxes", help="official box and ForcePack catalog")
    bx.add_argument("action", choices=["import", "list", "show", "add"])
    bx.add_argument("query", nargs="*", help="search words (list)")
    bx.add_argument("--id", type=int, help="box id (show, add)")
    bx.add_argument("--name", help="box name, matched exactly or by unique substring (show, add)")
    bx.add_argument(
        "--only",
        help='add only these items: JSON {"item_id": "unit_uuid" or null}; used after a review',
    )
    bx.add_argument("--limit", type=int, default=15)
    bx.add_argument("--refresh-pages", action="store_true", help="re-download cached wiki pages (import)")
    bx.add_argument("--text", action="store_true")
    bx.set_defaults(func=cmd_boxes)

    b = sub.add_parser("build", help="build a force to a BV target")
    b.add_argument("--target", type=int, required=True, help="BV budget")
    b.add_argument("--formation", default="lance", help=", ".join(formations.FORMATIONS))
    b.add_argument("--size", type=int, help="custom unit count (overrides the formation size and mix)")
    b.add_argument("--mix", help="kind constraints, e.g. mech=2,vehicle>=1,ba<=1")
    b.add_argument("--faction")
    b.add_argument("--era")
    b.add_argument("--tech-base", choices=["Inner Sphere", "Clan"])
    b.add_argument("--painted-only", action="store_true")
    b.add_argument("--box", help="only use units whose box label contains this text")
    b.add_argument("--weights", help="JSON {collection_id: weight} or @file; higher is favored")
    b.add_argument("--exclude", help="comma-separated collection ids to leave out")
    b.add_argument("--c3", help="only units carrying this network: C3, C3i, Nova CEWS or Naval C3")
    b.add_argument(
        "--include",
        help="comma-separated collection ids that must be in the build; repeat an id for more copies",
    )
    b.add_argument("--kinds", help="comma-separated kinds allowed in the pool")
    b.add_argument("--slack", type=float, default=3.0, help="percent below target a build may land")
    b.add_argument("--variants", type=int, default=1)
    b.add_argument("--seed", type=int)
    b.add_argument(
        "--proxy",
        action="store_true",
        help="let every owned mini stand in for any variant of its chassis",
    )
    b.add_argument("--text", action="store_true")
    b.set_defaults(func=cmd_build)

    f = sub.add_parser("forces", help="saved forces")
    f.add_argument("action", choices=["list", "show", "save", "delete", "export"])
    f.add_argument("--format", choices=["link", "mul"], default="link", help="export: MekBay link or .mul file")
    f.add_argument("--out", help="export --format mul: write the file here instead of printing it")
    f.add_argument("--id", type=int)
    f.add_argument("--name")
    f.add_argument("--formation", default="lance")
    f.add_argument("--target", type=int)
    f.add_argument("--units", default="", help="collection_id:qty,collection_id:qty")
    f.add_argument("--faction")
    f.add_argument("--era")
    f.add_argument("--notes")
    f.set_defaults(func=cmd_forces)

    s = sub.add_parser("serve", help="run the web UI")
    s.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    s.add_argument("--host", default="127.0.0.1")
    s.set_defaults(func=cmd_serve)
    return p


def main(argv=None):
    args = make_parser().parse_args(argv)
    conn = db.connect(args.db)
    try:
        args.func(args, conn)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        _emit({"error": str(exc)})
        sys.exit(1)
    finally:
        try:
            conn.close()
        except Exception:
            pass
