from django.contrib.auth.management.commands import createsuperuser as base

from hosted.management.commands import _passwords


class Command(base.Command):
    """createsuperuser with the sign-up password rules: NFC form, 10 to 256 characters."""

    def handle(self, *args, **options):
        original = base.getpass
        base.getpass = _passwords.getpass_module
        try:
            with _passwords.checked_superuser_password_env():
                return super().handle(*args, **options)
        finally:
            base.getpass = original
