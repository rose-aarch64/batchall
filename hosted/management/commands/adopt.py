import getpass
import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from batchall import db, multiuser
from hosted import accounts


class Command(BaseCommand):
    help = "Create the first account and move the existing single-user database into it."

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

        password = self._password(options["password_env"])
        try:
            accounts.normalize_handle(options["handle"])
            accounts.check_password(password)
            user = accounts.create_user(
                options["handle"], password, options["display_name"], superuser=True
            )
        except accounts.AccountError as exc:
            raise CommandError(str(exc)) from None

        try:
            result = multiuser.adopt_data(path, user.id)
        except Exception as exc:
            user.delete()  # the delete signal removes any user file
            raise CommandError(f"adopt failed, nothing was changed: {exc}") from exc
        self.stdout.write(json.dumps(result))

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
