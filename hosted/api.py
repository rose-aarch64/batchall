"""The /api/ adapter: runs ``batchall.server.ROUTES`` handlers against the signed-in user's database."""

import json
import logging
import urllib.parse
from http import HTTPStatus

from django.conf import settings
from django.core.exceptions import RequestDataTooBig as TooBig
from django.core.exceptions import SuspiciousOperation
from django.http import HttpResponse, JsonResponse, RawPostDataException

from batchall import db, server

BODY_METHODS = ("POST", "PUT", "PATCH", "DELETE")
NO_STORE = "no-store"
IMAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
MAX_SQLITE_INT = 2**63 - 1

logger = logging.getLogger("hosted.api")


def _error(message, status):
    response = JsonResponse({"error": message}, status=int(status), json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = NO_STORE
    return response


def _read_body(request):
    """The JSON object sent with a mutating request. Raises ApiError with the local server's messages."""
    not_json = server.ApiError("request body must be application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return {}
    # Check the type first: touching request.body on a form or multipart request can fail.
    if "application/json" not in (request.META.get("CONTENT_TYPE") or ""):
        raise not_json
    try:
        raw = request.body
    except SuspiciousOperation:
        raise server.ApiError("request body is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    except RawPostDataException:
        raise not_json
    if not raw:
        return {}
    if len(raw) > server.MAX_BODY_BYTES:
        raise server.ApiError("request body is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise server.ApiError("request body is not valid JSON")
    if not isinstance(body, dict):
        raise server.ApiError("request body must be a JSON object")
    return body


def _send(result):
    if isinstance(result, server.RawResponse):
        response = HttpResponse(result.data, content_type=result.content_type)
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = IMAGE_CSP
        response["Cache-Control"] = f"private, max-age={result.cache_seconds}" if result.cache_seconds else NO_STORE
        return response
    response = JsonResponse(result, safe=False, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = NO_STORE
    return response


def bad_request(request, exception=None):
    """The 400 handler: JSON for requests Django rejects before a view runs (such as an oversized form)."""
    if isinstance(exception, TooBig):
        return _error("request body is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    return _error("bad request", HTTPStatus.BAD_REQUEST)


def _match(request, path, allowed=None):
    """Find the route for this request. Returns (handler, match, None) or (None, None, error response)."""
    full_path = "/api/" + path
    matches = []
    for route_method, pattern, handler in server.ROUTES:
        found = pattern.match(full_path)
        if found:
            matches.append((route_method, handler, found))
    if not matches:
        return None, None, _error("no such endpoint", HTTPStatus.NOT_FOUND)
    chosen = [m for m in matches if m[0] == request.method and (allowed is None or (m[0], m[1]) in allowed)]
    if not chosen:
        return None, None, _error("no such endpoint", HTTPStatus.METHOD_NOT_ALLOWED)
    _, handler, found = chosen[0]
    return handler, found, None


def api(request, path):
    # Route errors (404/405) come before the sign-in check, as they always have.
    _, _, failure = _match(request, path)
    if failure is not None:
        return failure

    if not request.user.is_authenticated:
        return _error("sign in required", HTTPStatus.UNAUTHORIZED)

    if not db.is_multiuser(settings.BATCHALL_SHARED_DB):
        return _error("the server is not set up: run manage.py adopt", HTTPStatus.SERVICE_UNAVAILABLE)

    return dispatch(
        request, path, open_conn=lambda: db.connect_user(request.user.id, settings.BATCHALL_SHARED_DB)
    )


def dispatch(request, path, *, open_conn, allowed=None):
    """Run the matched handler on the connection from ``open_conn``.

    ``allowed`` is None (every route) or a set of (method, handler) pairs; other routes get a 405.
    """
    full_path = "/api/" + path
    handler, found, failure = _match(request, path, allowed)
    if failure is not None:
        return failure

    try:
        body = _read_body(request) if request.method in BODY_METHODS else {}
    except server.ApiError as exc:
        return _error(str(exc), exc.status)

    query = urllib.parse.parse_qs(request.META.get("QUERY_STRING", ""), keep_blank_values=False)
    groups = found.groups()
    try:
        conn = open_conn()
    except FileNotFoundError:
        return _error("not found", HTTPStatus.NOT_FOUND)
    try:
        table = server.OWNED_ID_ROUTES.get(handler)
        if table is not None:
            try:
                row_id = int(groups[0])
            except (IndexError, ValueError):
                return _error("not found", HTTPStatus.NOT_FOUND)
            if row_id > MAX_SQLITE_INT:
                return _error("not found", HTTPStatus.NOT_FOUND)
            # The table name comes from the static OWNED_ID_ROUTES values, never from the request.
            if conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone() is None:
                return _error("not found", HTTPStatus.NOT_FOUND)
        try:
            return _send(handler(conn, query, body, *groups))
        except server.ApiError as exc:
            return _error(str(exc), exc.status)
        except (ValueError, KeyError, TypeError) as exc:
            return _error(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception:
            logger.exception("unhandled error in %s %s", request.method, full_path)
            return _error("internal error", HTTPStatus.INTERNAL_SERVER_ERROR)
    finally:
        conn.close()
