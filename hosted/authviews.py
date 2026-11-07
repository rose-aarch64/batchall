"""JSON endpoints for sign-up, sign-in, sign-out and account changes."""

import functools
import json
import unicodedata

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie

from batchall.ratelimit import RateLimiter
from hosted import accounts
from hosted.accounts import AccountError

pair_limiter = RateLimiter(5, 300, 300)  # keyed by (handle, ip)
handle_limiter = RateLimiter(30, 300, 300)  # keyed by handle alone, across all addresses
ip_limiter = RateLimiter(30, 300, 300)

_BAD_LOGIN = "invalid handle or password"


class _ApiError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status


def client_ip(request):
    if settings.BATCHALL_TRUSTED_PROXY:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.META.get("REMOTE_ADDR", "")


def csrf_failure(request, reason=""):
    return JsonResponse({"error": "CSRF check failed"}, status=403)


def _error(message, status, **headers):
    response = JsonResponse({"error": message}, status=status)
    for name, value in headers.items():
        response[name.replace("_", "-")] = value
    return response


def api(*methods):
    """Restrict a view to methods, answering every error as JSON."""
    allowed = set(methods)

    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method not in allowed:
                return _error("method not allowed", 405, Allow=", ".join(sorted(allowed)))
            try:
                return view(request, *args, **kwargs)
            except _ApiError as exc:
                return _error(exc.message, exc.status)

        return wrapper

    return decorator


def _json_body(request, allow_empty=False):
    content_type = request.content_type or ""
    if content_type != "application/json":
        raise _ApiError("content type must be application/json", 415)
    if allow_empty and not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except (ValueError, RecursionError):
        raise _ApiError("request body must be valid JSON", 400) from None
    if not isinstance(data, dict):
        raise _ApiError("request body must be a JSON object", 400)
    return data


def _user_dict(user):
    return {
        "id": user.id,
        "handle": user.get_username(),
        "display_name": user.first_name or user.get_username(),
        "is_admin": user.is_superuser,
    }


def _plain_password(value):
    """NFC-normalize a password for comparison, or None if it cannot match."""
    if not isinstance(value, str) or len(value) > accounts.MAX_PASSWORD:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    value = unicodedata.normalize("NFC", value)
    if len(value) > accounts.MAX_PASSWORD:
        return None
    return value


def _locked(request, *limiter_keys):
    """A 429 response when any limiter key is locked, else None."""
    wait = max(limiter.retry_after(key) for limiter, key in limiter_keys)
    if wait > 0:
        return _error("too many attempts, try again later", 429, Retry_After=str(wait))
    return None


def login_handle(raw):
    """The normalized handle for limiter keys, or "" when the text is not a valid handle."""
    try:
        return accounts.normalize_handle(raw)
    except AccountError:
        return ""


def _login_keys(handle, ip):
    return (
        (pair_limiter, ("hi", handle, ip)),
        (handle_limiter, ("handle", handle)),
        (ip_limiter, ("ip", ip)),
    )


def login_retry_after(handle, ip):
    """Seconds until a sign-in attempt may be tried, 0 when none is locked out."""
    return max(limiter.retry_after(key) for limiter, key in _login_keys(handle, ip))


def record_login_failure(handle, ip):
    for limiter, key in _login_keys(handle, ip):
        limiter.record_failure(key)


def record_login_success(handle, ip):
    pair_limiter.reset(("hi", handle, ip))
    handle_limiter.reset(("handle", handle))


@api("POST")
def signup(request):
    data = _json_body(request)
    ip_key = ("ip", client_ip(request))
    locked = _locked(request, (ip_limiter, ip_key))
    if locked:
        return locked
    try:
        user = accounts.sign_up(
            data.get("handle"),
            data.get("password"),
            data.get("invite_code"),
            data.get("display_name"),
        )
    except AccountError as exc:
        ip_limiter.record_failure(ip_key)
        return _error(str(exc), 400)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return JsonResponse(_user_dict(user))


@api("POST")
def login_view(request):
    data = _json_body(request)
    handle = login_handle(data.get("handle"))
    ip = client_ip(request)
    wait = login_retry_after(handle, ip)
    if wait > 0:
        return _error("too many attempts, try again later", 429, Retry_After=str(wait))

    user = None
    password = _plain_password(data.get("password"))
    if handle and password is not None:
        user = authenticate(request, username=handle, password=password)
    if user is None:
        record_login_failure(handle, ip)
        return _error(_BAD_LOGIN, 401)
    record_login_success(handle, ip)
    if request.user.is_authenticated:
        # Django's login() keeps the key when the same user signs in again.
        request.session.cycle_key()
    login(request, user)
    return JsonResponse(_user_dict(user))


@api("POST")
def logout_view(request):
    _json_body(request, allow_empty=True)
    logout(request)
    return JsonResponse({"ok": True})


@api("GET")
@ensure_csrf_cookie
def me(request):
    if not request.user.is_authenticated:
        return _error("sign in required", 401)
    return JsonResponse(_user_dict(request.user))


def _require_user(request):
    if not request.user.is_authenticated:
        raise _ApiError("sign in required", 401)
    return request.user


@api("POST")
def change_password(request):
    user = _require_user(request)
    data = _json_body(request)
    current = _plain_password(data.get("current"))
    if current is None or not user.check_password(current):
        return _error("current password is incorrect", 400)
    try:
        new = accounts.check_password(data.get("new"))
    except AccountError as exc:
        return _error(str(exc), 400)
    user.set_password(new)
    user.save(update_fields=["password"])
    update_session_auth_hash(request, user)
    return JsonResponse({"ok": True})


@api("DELETE")
def delete_account(request):
    user = _require_user(request)
    data = _json_body(request)
    password = _plain_password(data.get("password"))
    if password is None or not user.check_password(password):
        return _error("password is incorrect", 400)
    if data.get("confirm") != user.get_username():
        return _error("confirmation must match your handle", 400)
    logout(request)
    user.delete()
    return JsonResponse({"ok": True})
