"""The static pages in ``web/``. They are public; the data behind them is not."""

import mimetypes
from http import HTTPStatus

from django.http import HttpResponse, JsonResponse

from batchall import config


def page(request, path):
    if request.method != "GET":
        return JsonResponse({"error": "method not allowed"}, status=HTTPStatus.METHOD_NOT_ALLOWED)
    rel = path.lstrip("/") or "index.html"
    web_dir = config.WEB_DIR.resolve()
    try:
        target = (config.WEB_DIR / rel).resolve()
        found = web_dir in target.parents and target.is_file()
    except ValueError:  # an embedded null byte
        found = False
    if not found:
        return JsonResponse({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype.endswith("javascript"):
        ctype = f"{ctype}; charset=utf-8"
    response = HttpResponse(target.read_bytes(), content_type=ctype)
    response["Cache-Control"] = "no-cache"
    return response
