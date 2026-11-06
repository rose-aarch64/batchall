from django.contrib import admin
from django.urls import path

from hosted import authviews

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/signup", authviews.signup),
    path("api/auth/login", authviews.login_view),
    path("api/auth/logout", authviews.logout_view),
    path("api/auth/me", authviews.me),
    path("api/auth/password", authviews.change_password),
    path("api/auth/account", authviews.delete_account),
]
