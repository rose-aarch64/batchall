"""Shared password checks for the changepassword and createsuperuser commands."""

import getpass as _getpass
import os
from contextlib import contextmanager
from types import SimpleNamespace

from django.core.management.base import CommandError

from hosted import accounts


def checked(password):
    """Return the password in the form sign-in compares, or raise CommandError."""
    try:
        return accounts.check_password(password)
    except accounts.AccountError as exc:
        raise CommandError(str(exc)) from None


def checked_getpass(prompt="Password: ", stream=None):
    value = _getpass.getpass(prompt) if stream is None else _getpass.getpass(prompt, stream)
    return checked(value) if value else value


# Stands in for the getpass module inside Django's commands.
getpass_module = SimpleNamespace(getpass=checked_getpass, getuser=_getpass.getuser)


@contextmanager
def checked_superuser_password_env():
    """Replace DJANGO_SUPERUSER_PASSWORD with its checked form while the block runs."""
    name = "DJANGO_SUPERUSER_PASSWORD"
    original = os.environ.get(name)
    if original is not None:
        os.environ[name] = checked(original)
    try:
        yield
    finally:
        if original is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = original
