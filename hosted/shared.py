"""The /s/<token>/ routes: a read-only copy of one user's collection, reached by share link.

These views use no session or cookie. The token alone picks the owner, and ``request.user`` is never
read, so a signed-in visitor sees the token owner's data and cannot write to it either.
"""

from http import HTTPStatus

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from batchall import db, server
from hosted import api, pages, sharing

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


def _not_found():
    """The one answer for every link that does not lead to a collection, whatever the reason."""
    response = JsonResponse({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
    response["Cache-Control"] = api.NO_STORE
    return response


def _owner_name(user):
    return user.first_name or user.get_username()


@csrf_exempt
def shared_view(request, token, path):
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
    return api.dispatch(
        request,
        api_path,
        allowed=ALLOWED,
        open_conn=lambda: db.connect_user_readonly(owner.id, shared_db),
    )
