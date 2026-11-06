"""Django settings for hosted mode. Django handles accounts only."""

import os

from django.core.exceptions import ImproperlyConfigured

from batchall import config

SECRET_KEY = os.environ.get("BATCHALL_SECRET", "")
if len(SECRET_KEY) < 32:
    raise ImproperlyConfigured("BATCHALL_SECRET must be set to at least 32 characters")

DEBUG = os.environ.get("BATCHALL_DEBUG") == "1"


def _csv(name):
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


BATCHALL_SHARED_DB = config.DB_PATH
BATCHALL_TRUSTED_PROXY = os.environ.get("BATCHALL_TRUSTED_PROXY") == "1"
BATCHALL_HTTPS = os.environ.get("BATCHALL_HTTPS") == "1"

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"] + _csv("BATCHALL_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = _csv("BATCHALL_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "hosted",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "hosted.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "hosted.urls"
WSGI_APPLICATION = "hosted.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": config.ACCOUNTS_DB_PATH,
        "OPTIONS": {
            "init_command": "PRAGMA journal_mode=WAL;",
            "transaction_mode": "IMMEDIATE",
        },
    }
}

SESSION_COOKIE_NAME = "batchall_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 30 * 24 * 3600
SESSION_COOKIE_SECURE = BATCHALL_HTTPS
CSRF_COOKIE_SECURE = BATCHALL_HTTPS
CSRF_FAILURE_VIEW = "hosted.authviews.csrf_failure"
if BATCHALL_HTTPS:
    SECURE_HSTS_SECONDS = 31536000
if BATCHALL_TRUSTED_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.ScryptPasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
]

SECURE_REFERRER_POLICY = "no-referrer"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 14_000_000

USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
STATIC_ROOT = config.DATA_DIR / "static"
