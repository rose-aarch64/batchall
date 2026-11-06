from django.contrib import admin
from django.urls import path, re_path

from hosted import api as api_module
from hosted import authviews, pages

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/signup", authviews.signup),
    path("api/auth/login", authviews.login_view),
    path("api/auth/logout", authviews.logout_view),
    path("api/auth/me", authviews.me),
    path("api/auth/password", authviews.change_password),
    path("api/auth/account", authviews.delete_account),
    re_path(r"^api/(?P<path>.*)$", api_module.api),
    re_path(r"^(?P<path>.*)$", pages.page),
]
