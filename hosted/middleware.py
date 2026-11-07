import unicodedata

from django.http import HttpResponse

CSP = "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
ADMIN_CSP = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"


ADMIN_LOGIN_PATH = "/admin/login/"


def _authenticated_spelling(raw):
    """The username as the admin form authenticates it (Unicode NFKC), so equivalent spellings share a key.

    Anything that is not text, or that cannot be normalized, is returned unchanged and ends up under
    the fixed invalid-handle key.
    """
    if not isinstance(raw, str):
        return raw
    try:
        return unicodedata.normalize("NFKC", raw)
    except Exception:
        return raw


class AdminLoginLimitMiddleware:
    """Apply the API sign-in limiters to POSTs to the admin login form.

    A locked handle or address gets a 429 before any password is hashed. A 200 answer to a POST is
    the form shown again with an error, which counts as a failure. A redirect is a successful login.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method != "POST" or request.path != ADMIN_LOGIN_PATH:
            return self.get_response(request)
        from hosted import authviews

        handle = authviews.login_handle(_authenticated_spelling(request.POST.get("username")))
        ip = authviews.client_ip(request)
        wait = authviews.login_retry_after(handle, ip)
        if wait > 0:
            response = HttpResponse(
                "too many attempts, try again later\n",
                status=429,
                content_type="text/plain; charset=utf-8",
            )
            response["Retry-After"] = str(wait)
            return response
        response = self.get_response(request)
        if response.status_code == 302:
            authviews.record_login_success(handle, ip)
        elif response.status_code == 200:
            authviews.record_login_failure(handle, ip)
        return response


class SecurityHeadersMiddleware:
    """Add a Content-Security-Policy to every response that does not already have one."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = ADMIN_CSP if request.path.startswith("/admin/") else CSP
        return response
