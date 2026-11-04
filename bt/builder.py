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
from .collection import COLLECTION_SELECT, _row
from .kinds import KINDS


class BuildError(ValueError):
    pass


@dataclass
class PoolItem:
    collection_id: int
    unit: dict
    copies: int
    weight: float = 1.0


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
):
    """Collect usable units from the collection.

    ``weights`` maps collection id to a theme weight (default 1.0). ``exclude`` is a list
    of collection ids to leave out. ``box`` keeps only entries whose box label contains the
    text. With a faction or era filter, units whose availability is unknown (no MUL id) are
    left out and counted in the returned info.
    """
    box = (box or "").strip().lower()
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
        if tech_base and entry["tech_base"] != tech_base:
            continue
        if allowed is not None:
            if entry["mul_id"] is None:
                unknown_availability += 1
                continue
            if entry["mul_id"] not in allowed:
                continue
        copies = entry["painted"] if painted_only else entry["qty"]
        if copies <= 0:
            continue
        pool.append(PoolItem(entry["id"], entry, copies, weights.get(entry["id"], 1.0)))

    info = {
        "faction": faction_row,
        "era": era_row,
        "tech_base": tech_base,
        "box": box or None,
        "painted_only": bool(painted_only),
        "excluded_unknown_availability": unknown_availability,
        "pool_units": len(pool),
        "pool_models": sum(p.copies for p in pool),
    }
    return pool, info


# --- search -------------------------------------------------------------------------------


def _kind_tables(items, weights, max_count, target):
    """tables[c] maps total BV to (score, node) for the best c-item subset of ``items``.

    ``node`` is a linked list ``(item_index, parent_node)`` so subsets can be rebuilt.
    """
    tables = [{0: (0.0, None)}] + [{} for _ in range(max_count)]
    for idx, item in enumerate(items):
        bv = item.unit["bv"]
        w = weights[idx]
        for c in range(max_count - 1, -1, -1):
            src, dst = tables[c], tables[c + 1]
            for total, (score, node) in src.items():
                new_total = total + bv
                if new_total > target:
                    continue
                cand = score + w
                cur = dst.get(new_total)
                if cur is None or cand > cur[0]:
                    dst[new_total] = (cand, (idx, node))
    return tables


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
    tables = {
        kind: _kind_tables(by_kind[kind], weights[kind], max_needed[kind], target_bv)
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
            idx, node = node
            item = by_kind[kind][idx]
            chosen[item.collection_id] = chosen.get(item.collection_id, 0) + 1
    by_id = {p.collection_id: p for p in pool}
    units = []
    for cid, qty in chosen.items():
        u = by_id[cid].unit
        units.append(
            {
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
        )
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
            trimmed.append(PoolItem(item.collection_id, item.unit, copies, item.weight))
    return trimmed


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
):
    """Build one or more distinct forces from the collection. Returns a result dict."""
    fm = formations.get_formation(formation) if formation else None
    final_size = size if size else (fm.size if fm else None)
    if not final_size:
        raise BuildError("give a formation or a size")
    base_mix = dict(fm.mix) if fm and not size else {}
    final_mix = formations.merge_mix(base_mix, mix)

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
    )
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
                int(target_bv),
                int(final_size),
                final_mix,
                slack_pct,
                base_seed + attempt,
                theme_first,
            )
        except BuildError:
            if not builds:
                raise
            continue
        fingerprint = tuple(sorted((u["collection_id"], u["qty"]) for u in result["units"]))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result["seed"] = base_seed + attempt
        builds.append(result)

    return {
        "formation": fm.key if fm else None,
        "size": int(final_size),
        "mix": {k: list(v) for k, v in final_mix.items()},
        "pool": info,
        "builds": builds,
    }
