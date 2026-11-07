from django.contrib.auth.management.commands import changepassword as base

from hosted.management.commands import _passwords


class Command(base.Command):
    """changepassword with the sign-up password rules: NFC form, 10 to 256 characters."""

    def _get_pass(self, prompt="Password: "):
        return _passwords.checked(super()._get_pass(prompt))
