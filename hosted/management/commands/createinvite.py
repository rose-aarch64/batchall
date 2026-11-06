import json

from django.core.management.base import BaseCommand

from hosted import accounts


class Command(BaseCommand):
    help = "Create a one-use invite code and print it as JSON."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps({"invite": accounts.make_invite()}))
