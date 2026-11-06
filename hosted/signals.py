"""Remove a user's data files when the account is deleted."""

from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete
from django.dispatch import receiver

from batchall import db


@receiver(post_delete, sender=get_user_model(), dispatch_uid="batchall-remove-user-data")
def remove_user_data(sender, instance, **kwargs):
    path = db.user_db_path(settings.BATCHALL_SHARED_DB, instance.pk)
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
