from django.conf import settings
from django.db import models


class Invite(models.Model):
    """A single-use sign-up code. Only a keyed hash of the code is stored."""

    code_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Invite {self.pk}"


class ShareLink(models.Model):
    """A private read-only link to a user's collection. One per user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="share_link",
    )
    secret = models.CharField(max_length=43)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def token(self):
        return f"{self.pk}.{self.secret}"

    def __str__(self):
        return f"ShareLink {self.pk}"
