"""Local web server: a JSON API plus the static UI in ``web/``.

Binds to loopback unless told otherwise. Mutating requests must send ``Content-Type: application/json``
(which browsers will not send cross-origin without a preflight), and the Host header must
name this machine (localhost, or the exact address the server was started on), which blocks
DNS-rebinding attempts. There is no login, so anyone who can reach a non-loopback address can
change the collection.
"""

import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import (
    art, availability, boxes, builder, catalog, collection, config, copies, db, details, export,
    formations, forces,
)
from .kinds import KIND_LABELS, KINDS

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
WILDCARD_HOSTS = {"0.0.0.0", "::", ""}

# Where cached artwork lives. make_server sets it; None means the default data folder.
art_cache_dir = None


class RawResponse:
    """A handler result that is sent as-is instead of as JSON (used for images)."""

    def __init__(self, data, content_type, cache_seconds=0):
        self.data = data
        self.content_type = content_type
        self.cache_seconds = cache_seconds


class ApiError(Exception):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def _bool(value):
    return str(value).lower() in ("1", "true", "yes", "on")


def _first(query, key, default=None):
    values = query.get(key)
    return values[0] if values and values[0] != "" else default


def _int(value, label):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(f"{label} must be a whole number")


# --- route handlers: each takes (conn, query, body, *path_args) and returns JSON data -----


def get_meta(conn, query, body):
    return {
        "catalog": catalog.status(conn),
        "box_catalog": conn.execute("SELECT COUNT(*) FROM boxes").fetchone()[0],
        "kinds": [{"key": k, "label": KIND_LABELS[k]} for k in KINDS],
        "formations": formations.describe_all(),
        "factions": availability.list_factions(conn),
        "eras": availability.list_eras(conn),
    }


def get_catalog_search(conn, query, body):
    return catalog.search(
        conn,
        _first(query, "q", ""),
        kind=_first(query, "kind"),
        limit=min(_int(_first(query, "limit", 25), "limit"), 100),
    )


def get_collection(conn, query, body):
    return collection.list_collection(
        conn,
        q=_first(query, "q"),
        kind=_first(query, "kind"),
        painted_only=_bool(_first(query, "painted_only", "")),
        faction=_first(query, "faction"),
        era=_first(query, "era"),
        tech_base=_first(query, "tech_base"),
        box=_first(query, "box"),
        tag=_first(query, "tag"),
        copy_faction=_first(query, "copy_faction"),
        copy_company=_first(query, "copy_company"),
        material=_first(query, "material"),
    )


def get_boxes(conn, query, body):
    return collection.list_boxes(conn)


def patch_copy(conn, query, body, copy_id):
    return copies.update(
        conn, int(copy_id), note=body.get("note"), tag=body.get("tag"),
        faction=body.get("faction"), company=body.get("company"),
        painted=body.get("painted"), box=body.get("box"), material=body.get("material"),
    )


MAX_BULK_MINIS = 5000


def patch_minis(conn, query, body):
    """Set the same fields on many minis at once. Nothing changes unless every id is valid."""
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ApiError("ids must be a non-empty list of mini ids")
    if len(ids) > MAX_BULK_MINIS:
        raise ApiError(f"at most {MAX_BULK_MINIS} minis can be changed in one request")
    if any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
        raise ApiError("ids must be whole numbers")
    updated = copies.update_many(
        conn, ids, note=body.get("note"), tag=body.get("tag"), faction=body.get("faction"),
        company=body.get("company"), painted=body.get("painted"), box=body.get("box"),
        material=body.get("material"),
    )
    return {"updated": updated}


def delete_mini(conn, query, body, copy_id):
    copies.delete_mini(conn, int(copy_id))
    return {"removed": int(copy_id)}


def post_copy_swap(conn, query, body, copy_id):
    if not body.get("unit_uuid"):
        raise ApiError("unit_uuid is required")
    return copies.swap(conn, int(copy_id), body["unit_uuid"])


def get_copy_tags(conn, query, body):
    return copies.list_tags(conn)


def get_copy_factions(conn, query, body):
    return copies.list_values(conn, "faction")


def get_copy_companies(conn, query, body):
    return copies.list_values(conn, "company")


def get_copy_materials(conn, query, body):
    return copies.list_values(conn, "material")


def post_collection(conn, query, body):
    uuid = body.get("unit_uuid")
    if not uuid:
        raise ApiError("unit_uuid is required")
    return collection.add(
        conn, uuid, qty=int(body.get("qty", 1)), painted=int(body.get("painted", 0)),
        notes=body.get("notes", ""), box=body.get("box", ""),
    )


def patch_collection(conn, query, body, entry_id):
    return collection.update(
        conn,
        int(entry_id),
        qty=body.get("qty"),
        painted=body.get("painted"),
        notes=body.get("notes"),
        box=body.get("box"),
    )


def delete_collection(conn, query, body, entry_id):
    collection.remove(conn, int(entry_id))
    return {"removed": int(entry_id)}


def get_box_catalog(conn, query, body):
    return boxes.search_boxes(
        conn, _first(query, "q", ""), limit=min(_int(_first(query, "limit", 15), "limit"), 50)
    )


def get_entry_boxes(conn, query, body, entry_id):
    return boxes.for_unit(conn, collection.get(conn, int(entry_id))["unit_uuid"])


def get_box_detail(conn, query, body, box_id):
    return boxes.get_box(conn, int(box_id))


def post_box_add(conn, query, body, box_id):
    only = body.get("only")
    if only is not None and not isinstance(only, dict):
        raise ApiError("only must be an object of item id to unit uuid or null")
    return boxes.add_box(conn, int(box_id), only=only)


def get_unit_art(conn, query, body, unit_uuid):
    found = art.get_art(conn, unit_uuid, cache=art_cache_dir)
    if found is None:
        raise ApiError("no art for this unit", HTTPStatus.NOT_FOUND)
    data, content_type = found
    return RawResponse(data, content_type, cache_seconds=86400)


def get_faction_icon(conn, query, body, faction_id):
    found = art.get_faction_icon(conn, int(faction_id), cache=art_cache_dir)
    if found is None:
        raise ApiError("no logo for this faction", HTTPStatus.NOT_FOUND)
    data, content_type = found
    return RawResponse(data, content_type, cache_seconds=86400)


def get_unit_detail(conn, query, body, unit_uuid):
    unit = details.get(conn, unit_uuid)
    if unit is None:
        raise ApiError("no such unit", HTTPStatus.NOT_FOUND)
    return unit


def get_variants(conn, query, body, entry_id):
    return collection.variants(conn, int(entry_id))


def post_swap(conn, query, body, entry_id):
    if not body.get("unit_uuid"):
        raise ApiError("unit_uuid is required")
    return collection.swap(conn, int(entry_id), body["unit_uuid"])


def post_import(conn, query, body):
    return collection.import_text(
        conn, body.get("text", ""), painted=bool(body.get("painted")), box=body.get("box", ""),
        mode=body.get("mode") or "name",
    )


def post_build(conn, query, body):
    mix = body.get("mix")
    return builder.build(
        conn,
        target_bv=_int(body.get("target_bv"), "target_bv"),
        formation=body.get("formation") or "lance",
        size=int(body["size"]) if body.get("size") else None,
        mix=formations.parse_mix(mix) if isinstance(mix, str) and mix else None,
        faction=body.get("faction") or None,
        era=body.get("era") or None,
        tech_base=body.get("tech_base") or None,
        painted_only=bool(body.get("painted_only")),
        weights=body.get("weights"),
        exclude=body.get("exclude"),
        slack_pct=float(body.get("slack_pct", 3.0)),
        variants=min(int(body.get("variants", 1)), 5),
        seed=body.get("seed"),
        box=body.get("box") or None,
    )


def get_forces(conn, query, body):
    return forces.list_forces(conn)


def post_force(conn, query, body):
    return forces.save(
        conn,
        body.get("name"),
        body.get("formation") or "lance",
        body.get("units") or [],
        target_bv=body.get("target_bv"),
        faction_id=body.get("faction_id"),
        era_id=body.get("era_id"),
        notes=body.get("notes", ""),
    )


def put_force(conn, query, body, force_id):
    return forces.save(
        conn,
        body.get("name"),
        body.get("formation") or "lance",
        body.get("units") or [],
        target_bv=body.get("target_bv"),
        faction_id=body.get("faction_id"),
        era_id=body.get("era_id"),
        notes=body.get("notes", ""),
        force_id=int(force_id),
    )


def get_force_export(conn, query, body, force_id):
    return export.export(forces.get(conn, int(force_id)))


def delete_force(conn, query, body, force_id):
    forces.delete(conn, int(force_id))
    return {"deleted": int(force_id)}


ROUTES = [
    ("GET", r"/api/meta", get_meta),
    ("GET", r"/api/catalog/search", get_catalog_search),
    ("GET", r"/api/collection", get_collection),
    ("GET", r"/api/boxes", get_boxes),
    ("GET", r"/api/collection/(\d+)/boxes", get_entry_boxes),
    ("GET", r"/api/box-catalog", get_box_catalog),
    ("GET", r"/api/box-catalog/(\d+)", get_box_detail),
    ("POST", r"/api/box-catalog/(\d+)/add", post_box_add),
    ("GET", r"/api/art/([^/]+)", get_unit_art),
    ("GET", r"/api/faction-icon/(\d+)", get_faction_icon),
    ("GET", r"/api/units/([^/]+)", get_unit_detail),
    ("GET", r"/api/collection/(\d+)/variants", get_variants),
    ("POST", r"/api/collection/(\d+)/swap", post_swap),
    ("PATCH", r"/api/copies/(\d+)", patch_copy),
    ("PATCH", r"/api/minis", patch_minis),
    ("POST", r"/api/copies/(\d+)/swap", post_copy_swap),
    ("GET", r"/api/copy-tags", get_copy_tags),
    ("GET", r"/api/copy-factions", get_copy_factions),
    ("GET", r"/api/copy-companies", get_copy_companies),
    ("GET", r"/api/copy-materials", get_copy_materials),
    ("DELETE", r"/api/minis/(\d+)", delete_mini),
    ("POST", r"/api/collection", post_collection),
    ("PATCH", r"/api/collection/(\d+)", patch_collection),
    ("DELETE", r"/api/collection/(\d+)", delete_collection),
    ("POST", r"/api/import", post_import),
    ("POST", r"/api/build", post_build),
    ("GET", r"/api/forces", get_forces),
    ("POST", r"/api/forces", post_force),
    ("GET", r"/api/forces/(\d+)/export", get_force_export),
    ("PUT", r"/api/forces/(\d+)", put_force),
    ("DELETE", r"/api/forces/(\d+)", delete_force),
]
ROUTES = [(m, re.compile(f"^{p}$"), h) for m, p, h in ROUTES]


class Handler(BaseHTTPRequestHandler):
    server_version = "batchall/0.1"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # -- plumbing --

    def _host_ok(self):
        try:
            host = urlparse("//" + (self.headers.get("Host") or "")).hostname
        except ValueError:
            return False
        return host in self.server.allowed_hosts

    def _send_json(self, data, status=HTTPStatus.OK):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_raw(self, raw):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", raw.content_type)
        self.send_header("Content-Length", str(len(raw.data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Cache-Control", f"private, max-age={raw.cache_seconds}" if raw.cache_seconds else "no-store"
        )
        self.end_headers()
        self.wfile.write(raw.data)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ApiError("request body must be application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            raise ApiError("request body is not valid JSON")
        if not isinstance(body, dict):
            raise ApiError("request body must be a JSON object")
        return body

    def _dispatch(self, method):
        if not self._host_ok():
            return self._send_json({"error": "forbidden host"}, HTTPStatus.FORBIDDEN)
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            return self._api(method, path, parse_qs(parsed.query))
        if method != "GET":
            return self._send_json({"error": "method not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)
        return self._static(path)

    def _api(self, method, path, query):
        matched_path = False
        for route_method, pattern, handler in ROUTES:
            m = pattern.match(path)
            if not m:
                continue
            matched_path = True
            if route_method != method:
                continue
            conn = db.connect(self.server.db_path)
            try:
                body = self._read_body() if method in ("POST", "PUT", "PATCH", "DELETE") else {}
                result = handler(conn, query, body, *m.groups())
                if isinstance(result, RawResponse):
                    return self._send_raw(result)
                return self._send_json(result)
            except ApiError as exc:
                return self._send_json({"error": str(exc)}, exc.status)
            except (ValueError, KeyError, TypeError) as exc:
                return self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            finally:
                conn.close()
        status = HTTPStatus.METHOD_NOT_ALLOWED if matched_path else HTTPStatus.NOT_FOUND
        return self._send_json({"error": "no such endpoint"}, status)

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (config.WEB_DIR / rel).resolve()
        if config.WEB_DIR.resolve() not in target.parents or not target.is_file():
            return self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


def make_server(port=config.DEFAULT_PORT, host="127.0.0.1", db_path=None, art_dir=None):
    global art_cache_dir
    art_cache_dir = art_dir
    server = ThreadingHTTPServer((host, port), Handler)
    server.db_path = db_path
    # Started on a specific address (a LAN address, to reach it from a phone), the server also answers
    # to that address. A wildcard bind names none, so only localhost works there.
    named = host.lower() if host and host not in WILDCARD_HOSTS else None
    server.allowed_hosts = LOCAL_HOSTS | ({named} if named else set())
    return server


def serve(port=config.DEFAULT_PORT, host="127.0.0.1", db_path=None, art_dir=None):
    conn = db.connect(db_path)
    try:
        catalog.fill_missing_faction_icons(conn)  # databases from before logos were stored get them without a re-import
    finally:
        conn.close()
    server = make_server(port, host, db_path, art_dir)
    print(f"Batchall running at http://localhost:{server.server_address[1]}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
