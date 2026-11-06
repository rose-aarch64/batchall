from django.apps import AppConfig


class HostedConfig(AppConfig):
    name = "hosted"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from hosted import signals  # noqa: F401
