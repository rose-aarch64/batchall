"""Formation definitions and mix constraints.

Sizes follow BattleTechWiki (Sarna): a lance is four units, an augmented lance adds
two vehicles or battle armor squads to four BattleMechs, a Star is five, a Binary is
two Stars, and a ComStar/Word of Blake Level II is six Level I units. The company
size (three lances) is the standard Inner Sphere figure.
"""

import re
from dataclasses import dataclass

from .kinds import normalize_kind


@dataclass(frozen=True)
class Formation:
    key: str
    label: str
    size: int
    mix: dict  # kind -> (min, max)
    note: str


FORMATIONS = {
    f.key: f
    for f in (
        Formation("lance", "Lance", 4, {}, "Inner Sphere lance of four units."),
        Formation(
            "augmented-lance",
            "Augmented lance",
            6,
            {"mech": (4, 4)},
            "Four BattleMechs plus two combat vehicles or battle armor squads.",
        ),
        Formation("star", "Star", 5, {}, "Clan Star of five units."),
        Formation("level2", "Level II", 6, {}, "ComStar and Word of Blake Level II of six units."),
        Formation("binary", "Binary", 10, {}, "Clan Binary of two Stars."),
        Formation("company", "Company", 12, {}, "Inner Sphere company of three lances."),
    )
}

FORMATION_ALIASES = {
    "level-2": "level2",
    "level_2": "level2",
    "level ii": "level2",
    "levelii": "level2",
    "aug-lance": "augmented-lance",
    "augmented": "augmented-lance",
    "augmented lance": "augmented-lance",
}


def get_formation(key):
    text = str(key).strip().lower()
    text = FORMATION_ALIASES.get(text, text)
    if text not in FORMATIONS:
        raise ValueError(f"unknown formation {key!r}; expected one of {', '.join(FORMATIONS)}")
    return FORMATIONS[text]


def describe_all():
    return [
        {
            "key": f.key,
            "label": f.label,
            "size": f.size,
            "mix": {k: list(v) for k, v in f.mix.items()},
            "note": f.note,
        }
        for f in FORMATIONS.values()
    ]


_MIX_PART = re.compile(r"^\s*([A-Za-z-]+)\s*(>=|<=|=)\s*(\d+)\s*$")


def parse_mix(text):
    """Parse ``mech=2,vehicle>=1,ba<=1`` into ``{kind: (min, max)}``; max ``None`` means open."""
    bounds = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        m = _MIX_PART.match(part)
        if not m:
            raise ValueError(f"cannot read mix term {part.strip()!r}; use kind=N, kind>=N or kind<=N")
        kind = normalize_kind(m.group(1))
        op, n = m.group(2), int(m.group(3))
        lo, hi = bounds.get(kind, (0, None))
        if op == "=":
            lo, hi = n, n
        elif op == ">=":
            lo = n
        else:
            hi = n
        bounds[kind] = (lo, hi)
    return bounds


def merge_mix(base, override):
    """Overlay ``override`` bounds on ``base``; a kind named in override replaces the base bound."""
    merged = dict(base or {})
    merged.update(override or {})
    return merged
