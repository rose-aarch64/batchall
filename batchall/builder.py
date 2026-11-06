"""Force builder: choose owned units that fit a BV budget.

The search is exact rather than greedy. For each unit kind it runs a 0/1 knapsack over
(count, total BV) that keeps the highest "theme score" for every reachable total. Kinds are
then merged for each allowed mix of kinds. A build is the best set whose BV is within
``slack_pct`` below the target. With a theme, the highest theme score wins and BV breaks
ties; with no theme, the highest BV wins. If nothing lands in that window, the build with
the highest BV at or under the target wins. Extra variants are found by removing one model
used by an earlier build and searching again.

Theme is just a per-unit weight. The builder knows nothing about what a theme means; the
caller (the skill, or a UI filter) decides which units are favored.
"""

import math
import random
from dataclasses import dataclass

from . import availability, formations
from .catalog import unit_dict
from .collection import COLLECTION_SELECT, _row
from .collection import get as get_entry
from .kinds import KINDS


class BuildError(ValueError):
    pass


@dataclass
class PoolItem:
    collection_id: int
    unit: dict
    copies: int
    weight: float = 1.0
    # With proxying, the variants one owned mini can stand in for. None means just ``unit``.
    options: list = None

    def choices(self):
        return self.options if self.options else [self.unit]


# --- pool ---------------------------------------------------------------------------------


def make_pool(
    conn,
    faction=None,
    era=None,
    tech_base=None,
    painted_only=False,
    weights=None,
    exclude=None,
    kinds=None,
    box=None,
    proxy=False,
    c3=None,
):
    """Collect usable units from the collection.

    ``weights`` maps collection id to a theme weight (default 1.0). ``exclude`` is a list
    of collection ids to leave out. ``box`` keeps only entries whose box label contains the
    text. With a faction or era filter, units whose availability is unknown (no MUL id) are
    left out and counted in the returned info.

    With ``proxy``, every owned mini may stand in for any variant of its chassis and kind.
    The tech base, faction and era filters apply to each variant instead of the owned one.

    ``c3`` keeps only units that carry that network ("C3", "C3i", "Nova CEWS", "Naval C3"),
    matched ignoring case.
    """
    box = (box or "").strip().lower()
    c3 = (c3 or "").strip().lower()
    if c3 and conn.execute("SELECT 1 FROM units WHERE c3 IS NOT NULL LIMIT 1").fetchone() is None:
        raise BuildError("the catalog has no C3 data yet; run `catalog import` once to load it")
    faction_row = availability.resolve_faction(conn, faction)
    era_row = availability.resolve_era(conn, era)
    allowed = availability.available_mul_ids(
        conn, faction_row["id"] if faction_row else None, era_row["id"] if era_row else None
    )
    weights = {int(k): float(v) for k, v in (weights or {}).items()}
    exclude = {int(x) for x in (exclude or [])}
    kinds = set(kinds) if kinds else None

    pool = []
    unknown_availability = 0
    for row in conn.execute(COLLECTION_SELECT + " ORDER BY u.kind, u.chassis, u.model"):
        entry = _row(row)
        if entry["id"] in exclude:
            continue
        if kinds and entry["kind"] not in kinds:
            continue
        if box and box not in entry["box"].lower():
            continue
        copies = entry["painted"] if painted_only else entry["qty"]
        if copies <= 0:
            continue
        candidates = [entry]
        if proxy:
            candidates = [
                {**unit_dict(v), "box": entry["box"]}
                for v in conn.execute(
                    "SELECT * FROM units WHERE chassis = ? AND kind = ? ORDER BY model",
                    (entry["chassis"], entry["kind"]),
                )
            ]
        usable = []
        for cand in candidates:
            if tech_base and cand["tech_base"] != tech_base:
                continue
            if c3 and (cand.get("c3") or "").lower() != c3:
                continue
            if allowed is not None:
                if cand["mul_id"] is None:
                    if not proxy:  # a proxy mini only counts when it has no variant left at all
                        unknown_availability += 1
                    continue
                if cand["mul_id"] not in allowed:
                    continue
            usable.append(cand)
        if not usable:
            continue
        if proxy:
            # Variants with the same BV are interchangeable to the search. Keep one per BV, and
            # keep the owned variant over a look-alike so it is not reported as a stand-in.
            usable.sort(key=lambda c: c["uuid"] != entry["unit_uuid"])
            by_bv = {}
            for cand in usable:
                by_bv.setdefault(cand["bv"], cand)
            usable = sorted(by_bv.values(), key=lambda c: (c["bv"], c["name"]))
        pool.append(
            PoolItem(
                entry["id"], entry, copies, weights.get(entry["id"], 1.0), usable if proxy else None
            )
        )

    info = {
        "faction": faction_row,
        "era": era_row,
        "tech_base": tech_base,
        "box": box or None,
        "painted_only": bool(painted_only),
        "proxy": bool(proxy),
        "c3": c3 or None,
        "excluded_unknown_availability": unknown_availability,
        "pool_units": len(pool),
        "pool_models": sum(p.copies for p in pool),
    }
    return pool, info


# --- search -------------------------------------------------------------------------------


def _kind_tables(items, weights, max_count, target):
    """tables[c] maps total BV to (score, node) for the best c-item subset of ``items``.

    ``node`` is a linked list ``((item_index, option_index), parent_node)`` so subsets can be
    rebuilt. An item with several options (a proxy mini) is used at most once, as one option:
    every option reads the tables as they were before this item, because counts go downward
    and ``tables[c]`` is not written until the pass for ``c - 1``.
    """
    tables = [{0: (0.0, None)}] + [{} for _ in range(max_count)]
    for idx, item in enumerate(items):
        w = weights[idx]
        choices = item.choices()
        for c in range(max_count - 1, -1, -1):
            src, dst = tables[c], tables[c + 1]
            for opt, unit in enumerate(choices):
                bv = unit["bv"]
                for total, (score, node) in src.items():
                    new_total = total + bv
                    if new_total > target:
                        continue
                    cand = score + w
                    cur = dst.get(new_total)
                    if cur is None or cand > cur[0]:
                        dst[new_total] = (cand, ((idx, opt), node))
    return tables


COARSE_FROM_BV = 10_000  # proxy builds at or above this target group totals into buckets
COARSE_BUCKETS = 2_500  # about this many totals are kept apart per count


def _kind_tables_coarse(items, weights, max_count, target, grain):
    """Like ``_kind_tables`` but keeps one subset per ``grain`` BV of total.

    Mini variants multiply the number of reachable totals, which makes an exact search over a
    big target slow. Here a bucket keeps the best-scoring subset that reached it. Each subset's
    total is still exact and never above ``target``; only near-equal alternatives are dropped.
    The result is keyed by exact total, like ``_kind_tables``.
    """
    tables = [{0: (0.0, 0, None)}] + [{} for _ in range(max_count)]
    for idx, item in enumerate(items):
        w = weights[idx]
        choices = item.choices()
        for c in range(max_count - 1, -1, -1):
            src, dst = tables[c], tables[c + 1]
            for opt, unit in enumerate(choices):
                bv = unit["bv"]
                for score, total, node in src.values():
                    new_total = total + bv
                    if new_total > target:
                        continue
                    cand = score + w
                    key = new_total // grain
                    cur = dst.get(key)
                    if cur is None or (cand, new_total) > (cur[0], cur[1]):
                        dst[key] = (cand, new_total, ((idx, opt), node))
    return [{total: (score, node) for score, total, node in t.values()} for t in tables]


def _merge(left, right, kind_index, target):
    out = {}
    for t1, (s1, parts1) in left.items():
        for t2, (s2, node2) in right.items():
            total = t1 + t2
            if total > target:
                continue
            score = s1 + s2
            cur = out.get(total)
            if cur is None or score > cur[0]:
                out[total] = (score, parts1 + ((kind_index, node2),))
    return out


def _compositions(bounds, size):
    kinds = list(KINDS)
    results = []

    def rec(i, remaining, current):
        if i == len(kinds):
            if remaining == 0:
                results.append({k: c for k, c in current.items() if c})
            return
        lo, hi = bounds[kinds[i]]
        for c in range(lo, min(hi, remaining) + 1):
            current[kinds[i]] = c
            rec(i + 1, remaining - c, current)
        current.pop(kinds[i], None)

    rec(0, size, {})
    return results


def _expand(pool):
    by_kind = {k: [] for k in KINDS}
    for item in pool:
        for _ in range(item.copies):
            by_kind[item.unit["kind"]].append(item)
    return by_kind


def build_once(pool, target_bv, size, mix=None, slack_pct=3.0, seed=None, theme_first=True):
    """Return the single best build for one seed. Raises BuildError if none exists.

    Inside the slack window, ``theme_first`` ranks by theme score and then BV. Without a
    theme it ranks by BV first, so a plain request gets the closest fit to the target.
    """
    if size < 1:
        raise BuildError("formation size must be at least 1")
    if target_bv < 1:
        raise BuildError("target BV must be positive")
    rng = random.Random(seed)
    by_kind = _expand(pool)

    bounds = {}
    for kind in KINDS:
        lo, hi = (mix or {}).get(kind, (0, None))
        hi = size if hi is None else hi
        available = len(by_kind[kind])
        if lo > available:
            raise BuildError(
                f"mix needs at least {lo} {kind} unit(s) but the pool has {available}"
            )
        bounds[kind] = (lo, min(hi, available, size))

    compositions = _compositions(bounds, size)
    if not compositions:
        raise BuildError(
            f"the pool cannot fill {size} slots under this mix "
            f"({sum(len(v) for v in by_kind.values())} models available)"
        )

    weights = {
        kind: [item.weight + rng.random() * 1e-3 for item in by_kind[kind]] for kind in KINDS
    }
    max_needed = {kind: max(c.get(kind, 0) for c in compositions) for kind in KINDS}
    grain = 1
    if target_bv >= COARSE_FROM_BV and any(item.options for item in pool):
        grain = max(1, target_bv // COARSE_BUCKETS)
    tables = {
        kind: (
            _kind_tables(by_kind[kind], weights[kind], max_needed[kind], target_bv)
            if grain == 1
            else _kind_tables_coarse(by_kind[kind], weights[kind], max_needed[kind], target_bv, grain)
        )
        for kind in KINDS
        if max_needed[kind] > 0
    }
    kind_order = list(KINDS)

    window_low = math.ceil(target_bv * (1 - slack_pct / 100.0))
    best_key, best = None, None
    for comp in compositions:
        parts = sorted(comp, key=lambda k: len(tables[k][comp[k]]))
        if any(not tables[k][comp[k]] for k in parts):
            continue
        merged = {0: (0.0, ())}
        for kind in parts:
            merged = _merge(merged, tables[kind][comp[kind]], kind_order.index(kind), target_bv)
            if not merged:
                break
        for total, (score, picks) in merged.items():
            if total >= window_low:
                key = (1, score, total) if theme_first else (1, total, score)
            else:
                key = (0, total, score)
            if best_key is None or key > best_key:
                best_key, best = key, (total, picks, comp)

    if best is None:
        raise BuildError(f"no combination of {size} units fits within {target_bv} BV")

    total, picks, comp = best
    chosen = {}
    for kind_index, node in picks:
        kind = kind_order[kind_index]
        while node is not None:
            (idx, opt), node = node
            item = by_kind[kind][idx]
            key = (item.collection_id, opt)
            chosen[key] = chosen.get(key, 0) + 1
    by_id = {p.collection_id: p for p in pool}
    units = []
    for (cid, opt), qty in chosen.items():
        owned = by_id[cid].unit
        u = by_id[cid].choices()[opt]
        entry = {
            "collection_id": cid,
            "qty": qty,
            "name": u["name"],
            "kind": u["kind"],
            "tons": u["tons"],
            "bv": u["bv"],
            "role": u["role"],
            "tech_base": u["tech_base"],
            "box": u["box"],
            "weight": by_id[cid].weight,
        }
        if u["name"] != owned["name"]:
            entry["owned_as"] = owned["name"]  # the mini on the shelf, standing in for this variant
        units.append(entry)
    units.sort(key=lambda x: (KINDS.index(x["kind"]), -x["bv"], x["name"]))
    return {
        "target_bv": target_bv,
        "total_bv": total,
        "gap": target_bv - total,
        "size": size,
        "in_window": total >= window_low,
        "slack_pct": slack_pct,
        "composition": comp,
        "units": units,
    }


def _without_one_used(pool, rng, builds):
    """Copy of ``pool`` with one copy of a random unit from a random earlier build removed."""
    earlier = rng.choice(builds)
    victim = rng.choice(earlier["units"])["collection_id"]
    trimmed = []
    for item in pool:
        copies = item.copies - 1 if item.collection_id == victim else item.copies
        if copies > 0:
            trimmed.append(PoolItem(item.collection_id, item.unit, copies, item.weight, item.options))
    return trimmed


def _included_entries(conn, include, final_size):
    """Owned entries that must be in the build, as ``[(entry, count)]``.

    An id listed twice asks for two copies. Faction, era, tech base, box and kind filters do not
    apply to them, because naming a mini is an explicit choice.
    """
    counts = {}
    for cid in include or []:
        counts[int(cid)] = counts.get(int(cid), 0) + 1
    found = []
    for cid, n in counts.items():
        entry = get_entry(conn, cid)
        if n > entry["qty"]:
            raise BuildError(f"{entry['name']} (id {cid}) is included {n} times but you own {entry['qty']}")
        found.append((entry, n))
    if sum(n for _, n in found) > final_size:
        raise BuildError(f"{sum(n for _, n in found)} included units do not fit in {final_size} slots")
    return found


def _mix_after_includes(mix, found):
    """The mix bounds left for the search once the included units have taken their slots."""
    taken = {}
    for entry, n in found:
        taken[entry["kind"]] = taken.get(entry["kind"], 0) + n
    left = dict(mix)
    for kind, n in taken.items():
        lo, hi = left.get(kind, (0, None))
        if hi is not None and hi < n:
            raise BuildError(f"the mix allows at most {hi} {kind}, but {n} are included")
        left[kind] = (max(0, lo - n), None if hi is None else hi - n)
    return left


def _with_includes(result, found, target_bv, size, slack_pct):
    """Add the included units to a finished search result and restate its totals."""
    by_key = {(u["collection_id"], u["name"]): u for u in result["units"]}
    composition = dict(result["composition"])
    added_bv = 0
    for entry, n in found:
        added_bv += entry["bv"] * n
        composition[entry["kind"]] = composition.get(entry["kind"], 0) + n
        row = by_key.get((entry["id"], entry["name"]))
        if row is None:
            row = {
                "collection_id": entry["id"],
                "qty": 0,
                "name": entry["name"],
                "kind": entry["kind"],
                "tons": entry["tons"],
                "bv": entry["bv"],
                "role": entry["role"],
                "tech_base": entry["tech_base"],
                "box": entry["box"],
                "weight": 1.0,
            }
            result["units"].append(row)
            by_key[(entry["id"], entry["name"])] = row
        row["qty"] += n
        row["included"] = row.get("included", 0) + n
    result["units"].sort(key=lambda x: (KINDS.index(x["kind"]), -x["bv"], x["name"]))
    total = result["total_bv"] + added_bv
    result.update(
        target_bv=target_bv,
        total_bv=total,
        gap=target_bv - total,
        size=size,
        in_window=total >= math.ceil(target_bv * (1 - slack_pct / 100.0)),
        composition=composition,
    )
    return result


def build(
    conn,
    target_bv,
    formation="lance",
    size=None,
    mix=None,
    faction=None,
    era=None,
    tech_base=None,
    painted_only=False,
    weights=None,
    exclude=None,
    kinds=None,
    slack_pct=3.0,
    variants=1,
    seed=None,
    box=None,
    proxy=False,
    include=None,
    c3=None,
):
    """Build one or more distinct forces from the collection. Returns a result dict.

    ``proxy`` lets every owned mini stand in for any variant of its chassis. A unit that
    does is reported under the variant it fields as, with ``owned_as`` naming the mini.

    ``include`` is a list of collection ids that must be in every build (an id listed twice
    asks for two copies). They count toward the size, the mix and the BV target, and ignore
    the faction, era, tech base, box and painted filters. They are always fielded as the
    variant owned, even with ``proxy``. With ``c3`` they must carry that network.

    ``c3`` keeps the search to units carrying that network, so every unit in the build can
    join one ("C3i" for an Improved C3 network).
    """
    fm = formations.get_formation(formation) if formation else None
    final_size = size if size else (fm.size if fm else None)
    if not final_size:
        raise BuildError("give a formation or a size")
    base_mix = dict(fm.mix) if fm and not size else {}
    final_mix = formations.merge_mix(base_mix, mix)

    found = _included_entries(conn, include, int(final_size))
    for entry, _ in found:
        if c3 and (entry.get("c3") or "").lower() != c3.strip().lower():
            raise BuildError(f"{entry['name']} (id {entry['id']}) does not carry {c3}")
    included_bv = sum(entry["bv"] * n for entry, n in found)
    included_count = sum(n for _, n in found)
    if included_bv > int(target_bv):
        raise BuildError(f"the included units cost {included_bv} BV, over the {int(target_bv)} target")
    search_mix = _mix_after_includes(final_mix, found) if found else final_mix
    search_size = int(final_size) - included_count
    search_target = int(target_bv) - included_bv

    pool, info = make_pool(
        conn,
        faction=faction,
        era=era,
        tech_base=tech_base,
        painted_only=painted_only,
        weights=weights,
        exclude=exclude,
        kinds=kinds,
        box=box,
        proxy=proxy,
        c3=c3,
    )
    if found:  # the included minis are spoken for, so the search cannot use them again
        taken = {entry["id"]: n for entry, n in found}
        pool = [
            PoolItem(p.collection_id, p.unit, p.copies - taken.get(p.collection_id, 0), p.weight, p.options)
            for p in pool
        ]
        pool = [p for p in pool if p.copies > 0]
        info["pool_units"] = len(pool)
        info["pool_models"] = sum(p.copies for p in pool)
        info["included"] = [
            {"collection_id": entry["id"], "name": entry["name"], "qty": n} for entry, n in found
        ]
    if search_size == 0:
        build_result = {
            "target_bv": int(target_bv), "total_bv": 0, "gap": 0, "size": 0, "in_window": True,
            "slack_pct": slack_pct, "composition": {}, "units": [], "seed": seed or 0,
        }
        return {
            "formation": fm.key if fm else None,
            "size": int(final_size),
            "mix": {k: list(v) for k, v in final_mix.items()},
            "pool": info,
            "builds": [_with_includes(build_result, found, int(target_bv), int(final_size), slack_pct)],
        }
    if not pool:
        raise BuildError("no usable units in the collection for these filters")

    base_seed = seed if seed is not None else random.SystemRandom().randrange(1_000_000)
    rng = random.Random(base_seed)
    theme_first = len({p.weight for p in pool}) > 1
    builds, seen = [], set()
    for attempt in range(max(variants * 4, 4)):
        if len(builds) >= variants:
            break
        # Later variants drop one model used by an earlier build, which forces a different
        # but still near-best answer.
        trial_pool = _without_one_used(pool, rng, builds) if builds else pool
        try:
            result = build_once(
                trial_pool,
                search_target,
                search_size,
                search_mix,
                slack_pct,
                base_seed + attempt,
                theme_first,
            )
        except BuildError:
            if not builds:
                raise
            continue
        fingerprint = tuple(sorted((u["collection_id"], u["name"], u["qty"]) for u in result["units"]))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result["seed"] = base_seed + attempt
        builds.append(result)

    if found:  # restate each search result as a whole force: included units, full size, real target
        builds = [_with_includes(b, found, int(target_bv), int(final_size), slack_pct) for b in builds]

    return {
        "formation": fm.key if fm else None,
        "size": int(final_size),
        "mix": {k: list(v) for k, v in final_mix.items()},
        "pool": info,
        "builds": builds,
    }
