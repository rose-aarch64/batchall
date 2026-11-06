from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import BaseUserCreationForm
from django.core.exceptions import ValidationError

from hosted.accounts import AccountError, normalize_handle
from hosted.models import Invite

User = get_user_model()


class HandleUserCreationForm(BaseUserCreationForm):
    def clean_username(self):
        try:
            handle = normalize_handle(self.cleaned_data["username"])
        except AccountError as exc:
            raise ValidationError(str(exc)) from None
        if User.objects.filter(username__iexact=handle).exists():
            raise ValidationError("that handle is already taken")
        return handle


class HandleUserAdmin(UserAdmin):
    add_form = HandleUserCreationForm
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "password1", "password2")}),
    )


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "created_by", "used_by", "used_at")
    fields = ("created_at", "created_by", "used_by", "used_at")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.unregister(User)
admin.site.register(User, HandleUserAdmin)
