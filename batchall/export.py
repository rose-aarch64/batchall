"""Hand a saved force to MekBay: a link that opens it there, or a MegaMek .mul file.

The link form (``?units=<uuid>,<uuid>&name=...``) was read from MekBay's source. The unit ids are
the same catalog uuids stored here. The .mul layout is reconstructed from MekBay's parser: a ``unit``
root with one ``entity`` per unit, matched on chassis and model. Pilots are not stored in the
collection, so each gets MekBay's default skills of gunnery 4 and piloting 5.
"""

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

MEKBAY_URL = "https://mekbay.com/"
DEFAULT_GUNNERY, DEFAULT_PILOTING = 4, 5


class ExportError(ValueError):
    pass


def _copies(force):
    """One dict per physical mini, in the force's own order."""
    copies = [u for u in force["units"] for _ in range(u["qty"])]
    if not copies:
        raise ExportError(f"{force['name']} has no units to export")
    return copies


def mekbay_link(force):
    """A link that opens the force in MekBay."""
    units = ",".join(u["unit_uuid"] for u in _copies(force))
    return MEKBAY_URL + "?" + urlencode({"units": units, "name": force["name"]})


def _filename(name):
    stem = re.sub(r"[^\w\- ]+", "", name).strip(" ._-")
    return (stem or "force") + ".mul"


def mul_file(force):
    """A MegaMek .mul file for the force, as {"filename", "content"}."""
    root = ET.Element("unit", {"version": "battletech-collection"})
    for u in _copies(force):
        entity = ET.SubElement(root, "entity", {"chassis": u["chassis"], "model": u["model"], "commander": "false"})
        ET.SubElement(
            entity,
            "pilot",
            {"size": "1", "name": "", "gunnery": str(DEFAULT_GUNNERY), "piloting": str(DEFAULT_PILOTING)},
        )
    ET.indent(root)
    body = ET.tostring(root, encoding="unicode")
    return {"filename": _filename(force["name"]), "content": '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"}


def export(force):
    """Everything the UI and CLI need for one force."""
    return {"url": mekbay_link(force), **mul_file(force)}
