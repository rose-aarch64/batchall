"""The /s/<token>/ routes: a read-only copy of one user's collection, reached by share link.

These views use no session or cookie. The token alone picks the owner, and ``request.user`` is never
read, so a signed-in visitor sees the token owner's data and cannot write to it either.
"""

import threading
from http import HTTPStatus

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from batchall import db, formations, server
from batchall.ratelimit import RateLimiter
from hosted import api, pages, sharing
from hosted.authviews import client_ip

# Every GET the shared page needs, plus the force builder, which stores nothing. Built from the
# handler objects so a renamed handler fails at import instead of silently dropping out.
ALLOWED = frozenset(
    [
        ("GET", server.get_meta),
        ("GET", server.get_catalog_search),
        ("GET", server.get_collection),
        ("GET", server.get_boxes),
        ("GET", server.get_entry_boxes),
        ("GET", server.get_unit_art),
        ("GET", server.get_faction_icon),
        ("GET", server.get_company_logos),
        ("GET", server.get_company_logo),
        ("GET", server.get_mini_photo),
        ("GET", server.get_faction_colors),
        ("GET", server.get_company_colors),
        ("GET", server.get_unit_detail),
        ("GET", server.get_variants),
        ("GET", server.get_copy_tags),
        ("GET", server.get_copy_factions),
        ("GET", server.get_copy_companies),
        ("GET", server.get_copy_materials),
        ("GET", server.get_forces),
        ("GET", server.get_force_export),
        ("POST", server.post_build),
    ]
)

# A visitor's build runs the same CPU-bound search as the owner's, in the same process, so it is
# bounded. The size ceiling is the largest formation (a company); the BV ceiling is above the
# twelve highest-BV units in the catalog together (about 47,000).
MAX_BUILD_SIZE = max(f.size for f in formations.FORMATIONS.values())
MAX_BUILD_TARGET_BV = 50_000
BUILD_RETRY_SECONDS = 5
build_slot = threading.Semaphore(1)

# Requests under /s/<token>/api/ per client address. Each request is recorded as a "failure", so an
# address that makes this many in the window is locked out for the next window. The first load of a
# large collection fetches an image or two per mini plus logos and icons, a few hundred requests, so
# the ceiling sits well above that. The page and its static files are not counted.
API_REQUESTS_PER_WINDOW = 1000
API_WINDOW_SECONDS = 60
api_limiter = RateLimiter(API_REQUESTS_PER_WINDOW, API_WINDOW_SECONDS, API_WINDOW_SECONDS)


def _not_found():
    """The one answer for every link that does not lead to a collection, whatever the reason."""
    response = JsonResponse({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
    response["Cache-Control"] = api.NO_STORE
    return response


def _owner_name(user):
    return user.first_name or user.get_username()


def _over(value, ceiling):
    """True when value is a number above ceiling. Values that are not numbers are left to the handler."""
    try:
        return int(value) > ceiling
    except OverflowError:
        return True
    except (TypeError, ValueError):
        return False


def _build(request, api_path, open_conn):
    """Run a visitor's build: bounded in size and target, and one at a time per process."""
    try:
        body = api._read_body(request)
    except server.ApiError as exc:
        return api._error(str(exc), exc.status)
    if body.get("size") and _over(body["size"], MAX_BUILD_SIZE):
        return api._error(f"size must be at most {MAX_BUILD_SIZE}", HTTPStatus.BAD_REQUEST)
    if _over(body.get("target_bv"), MAX_BUILD_TARGET_BV):
        return api._error(f"target_bv must be at most {MAX_BUILD_TARGET_BV}", HTTPStatus.BAD_REQUEST)
    if not build_slot.acquire(blocking=False):
        response = api._error("another build is running, try again shortly", HTTPStatus.TOO_MANY_REQUESTS)
        response["Retry-After"] = str(BUILD_RETRY_SECONDS)
        return response
    try:
        return api.dispatch(request, api_path, allowed=ALLOWED, open_conn=open_conn)
    finally:
        build_slot.release()


@csrf_exempt
def shared_view(request, token, path):
    if path.startswith("api/"):
        ip = client_ip(request)
        wait = api_limiter.retry_after(ip)
        if wait:
            response = api._error("too many requests, slow down", HTTPStatus.TOO_MANY_REQUESTS)
            response["Retry-After"] = str(wait)
            return response
        api_limiter.record_failure(ip)

    owner = sharing.resolve(token)
    if owner is None:
        return _not_found()
    shared_db = settings.BATCHALL_SHARED_DB
    if not db.user_db_path(shared_db, owner.id).is_file():
        return _not_found()

    if not path.startswith("api/"):
        return pages.page(request, path)

    api_path = path[len("api/"):]
    if api_path == "auth/me" and request.method == "GET":
        response = JsonResponse({"mode": "shared", "owner": _owner_name(owner)})
        response["Cache-Control"] = api.NO_STORE
        return response
    def open_conn():
        return db.connect_user_readonly(owner.id, shared_db)

    handler, _, _ = api._match(request, api_path, ALLOWED)
    if handler is server.post_build:
        return _build(request, api_path, open_conn)
    return api.dispatch(request, api_path, allowed=ALLOWED, open_conn=open_conn)
