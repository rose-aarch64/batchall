"""Private read-only share links for a user's collection."""

import secrets

from django.db import transaction

from hosted.models import ShareLink

TOKEN_PATTERN = r"[A-Za-z0-9_.\-]{1,200}"

_MAX_PK = 2**63 - 1
_DUMMY_SECRET = b"0" * 43


def create_link(user):
    """Create a link for user, replacing any existing one."""
    with transaction.atomic():
        ShareLink.objects.filter(user=user).delete()
        return ShareLink.objects.create(user=user, secret=secrets.token_urlsafe(32))


def revoke_link(user):
    deleted, _ = ShareLink.objects.filter(user=user).delete()
    return deleted > 0


def current_link(user):
    return ShareLink.objects.filter(user=user).first()


def resolve(token):
    """Return the owning user for a valid token, else None."""
    if not isinstance(token, str):
        return None
    pk_text, dot, secret = token.partition(".")
    if not dot or not pk_text.isascii() or not pk_text.isdigit() or not secret:
        return None
    if len(pk_text) > len(str(_MAX_PK)):
        return None
    pk = int(pk_text)
    if pk > _MAX_PK:
        return None
    link = ShareLink.objects.select_related("user").filter(pk=pk).first()
    expected = link.secret.encode("utf-8") if link else _DUMMY_SECRET
    matches = secrets.compare_digest(expected, secret.encode("utf-8"))
    if link is None or not matches:
        return None
    return link.user


def link_path(link):
    return f"/s/{link.token}/"
