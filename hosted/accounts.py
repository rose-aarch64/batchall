"""Handle, password and invite rules for hosted accounts."""

import re
import secrets
import unicodedata

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from batchall import db
from hosted.models import Invite

MIN_PASSWORD = 10
MAX_PASSWORD = 256
MAX_DISPLAY_NAME = 60
RESERVED_HANDLES = frozenset(
    "api admin login logout signup signin u static assets index".split()
)
_HANDLE_RE = re.compile(r"[a-z0-9-]{3,30}")
_INVITE_SALT = "batchall-invite"
_INVALID_INVITE = "invalid invite code"
_STALE_DATA_FILE = "server error: a data file already exists for this account id"


class AccountError(ValueError):
    """A sign-up or account rule was not met. The message is safe to show."""


def normalize_handle(raw):
    if not isinstance(raw, str):
        raise AccountError("handle must be text")
    handle = raw.strip().lower()
    if not _HANDLE_RE.fullmatch(handle):
        raise AccountError(
            "handle must be 3 to 30 characters: lowercase letters, digits and hyphens"
        )
    if handle in RESERVED_HANDLES:
        raise AccountError("that handle is reserved")
    return handle


def check_password(password):
    """Return the NFC-normalized password or raise AccountError.

    Length and encoding checks run before any validator or hasher.
    """
    if not isinstance(password, str):
        raise AccountError("password must be text")
    if len(password) > MAX_PASSWORD:
        raise AccountError(f"password must be at most {MAX_PASSWORD} characters")
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        raise AccountError("password contains characters that cannot be stored") from None
    password = unicodedata.normalize("NFC", password)
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"password must be at least {MIN_PASSWORD} characters")
    if len(password) > MAX_PASSWORD:
        raise AccountError(f"password must be at most {MAX_PASSWORD} characters")
    try:
        validate_password(password)
    except ValidationError as exc:
        raise AccountError(" ".join(exc.messages)) from None
    return password


def _display_name(display_name, handle):
    name = handle if display_name is None else str(display_name).strip()
    if not name or len(name) > MAX_DISPLAY_NAME:
        raise AccountError(f"display name must be 1 to {MAX_DISPLAY_NAME} characters")
    return name


def _hash_invite(code):
    return salted_hmac(_INVITE_SALT, code, algorithm="sha256").hexdigest()


def _insert_user(handle, hashed_password, name, superuser=False):
    User = get_user_model()
    if User.objects.filter(username__iexact=handle).exists():
        raise AccountError("that handle is already taken")
    user = User(username=handle, first_name=name, password=hashed_password)
    if superuser:
        user.is_staff = True
        user.is_superuser = True
    try:
        with transaction.atomic():
            user.save()
    except IntegrityError:
        raise AccountError("that handle is already taken") from None
    return user


def create_user(handle, password, display_name=None, superuser=False):
    handle = normalize_handle(handle)
    password = check_password(password)
    name = _display_name(display_name, handle)
    return _insert_user(handle, make_password(password), name, superuser)


def make_invite(created_by=None):
    code = secrets.token_urlsafe(16)
    Invite.objects.create(code_hash=_hash_invite(code), created_by=created_by)
    return code


def sign_up(handle, password, invite_code, display_name=None):
    handle = normalize_handle(handle)
    password = check_password(password)
    name = _display_name(display_name, handle)
    if not isinstance(invite_code, str) or not invite_code:
        raise AccountError(_INVALID_INVITE)
    try:
        code_hash = _hash_invite(invite_code)
    except UnicodeEncodeError:
        raise AccountError(_INVALID_INVITE) from None
    # A bad invite is refused before the handle is looked at and before any hashing. The conditional
    # update below is what settles a race between two sign-ups for the same invite.
    if not Invite.objects.filter(code_hash=code_hash, used_by__isnull=True).exists():
        raise AccountError(_INVALID_INVITE)
    hashed = make_password(password)
    with transaction.atomic():
        user = _insert_user(handle, hashed, name)
        if db.user_db_path(settings.BATCHALL_SHARED_DB, user.id).exists():
            raise AccountError(_STALE_DATA_FILE)
        claimed = Invite.objects.filter(code_hash=code_hash, used_by__isnull=True).update(
            used_by=user, used_at=timezone.now()
        )
        if claimed != 1:
            raise AccountError(_INVALID_INVITE)
    return user
