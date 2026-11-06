import getpass
import json
import os
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from batchall import config, db, multiuser
from hosted import accounts


class Command(BaseCommand):
    help = (
        "Create the first account and move the existing single-user database into it. "
        "Stop the single-user app first; the database is write-locked while this runs."
    )

    def add_arguments(self, parser):
        parser.add_argument("--handle", required=True)
        parser.add_argument("--display-name", dest="display_name", default=None)
        parser.add_argument(
            "--password-env",
            dest="password_env",
            default=None,
            metavar="VAR",
            help="read the password from this environment variable instead of prompting",
        )

    def handle(self, *args, **options):
        path = settings.BATCHALL_SHARED_DB
        if db.is_multiuser(path):
            self.stdout.write("already adopted")
            return

        if not Path(path).is_file() and not self._legacy_will_be_copied(path):
            raise CommandError(
                f"the shared database {path} does not exist; check BATCHALL_DB. "
                "Nothing was created."
            )

        password = self._password(options["password_env"])
        try:
            handle = accounts.normalize_handle(options["handle"])
            password = accounts.check_password(password)
            user, created = self._account(handle, password, options["display_name"])
        except accounts.AccountError as exc:
            raise CommandError(str(exc)) from None

        try:
            result = multiuser.adopt_data(path, user.id)
        except BaseException as exc:
            # Keep the account only when the data has been moved; otherwise a re-run starts clean.
            if created and not db.is_multiuser(path):
                user.delete()  # the delete signal removes any user file
            if isinstance(exc, Exception):
                raise CommandError(f"adopt failed, nothing was changed: {exc}") from exc
            raise
        if result["already"]:
            if created:
                user.delete()
            if result.get("user_file_missing"):
                raise CommandError(
                    "the database is marked multi-user but the user file is missing; "
                    "restore it from the .pre-multiuser backup"
                )
            self.stdout.write("already adopted")
            return
        self.stdout.write(json.dumps(result))

    @staticmethod
    def _legacy_will_be_copied(path):
        return (
            config.DB_IS_DEFAULT
            and Path(path) == Path(config.DB_PATH)
            and Path(config.LEGACY_DB_PATH).is_file()
        )

    @staticmethod
    def _account(handle, password, display_name):
        """The superuser to adopt into, and whether this run created it.

        A superuser with this handle, left by an interrupted run, is reused when the password
        matches. Any other existing account with the handle is refused.
        """
        existing = get_user_model().objects.filter(username__iexact=handle).first()
        if existing is None:
            return accounts.create_user(handle, password, display_name, superuser=True), True
        if existing.is_superuser and existing.check_password(password):
            return existing, False
        raise CommandError("that handle is already taken")

    def _password(self, env_name):
        if env_name:
            value = os.environ.get(env_name)
            if value is None:
                raise CommandError(f"environment variable {env_name} is not set")
            return value
        first = getpass.getpass("Password: ")
        if getpass.getpass("Password (again): ") != first:
            raise CommandError("the passwords do not match")
        return first
