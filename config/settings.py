import os

from storage.crypto import decode_key

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-secret-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "0").lower() in ("1", "true", "yes")
ALLOWED_HOSTS = ["*"]  # PoC only

INSTALLED_APPS = ["storage"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

# Rejected CSRF checks answer with JSON so the web UI can show a clear message.
CSRF_FAILURE_VIEW = "config.views.csrf_failure"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
    }
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# No database: all PoC data lives in Redis.
DATABASES = {}

USE_TZ = True

# Redis
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_TTL_SECONDS = int(os.environ.get("REDIS_TTL_SECONDS", "300"))

# Application-level encryption (AES-256-GCM) of everything stored in Redis.
# Required: Django refuses to start without a valid Base64-encoded 32-byte key.
STORAGE_ENCRYPTION_KEY = decode_key(os.environ.get("STORAGE_ENCRYPTION_KEY"))

# Uploads: keep files in memory only (never written to disk) and cap their size.
MAX_UPLOAD_SIZE_BYTES = int(os.environ.get("MAX_UPLOAD_SIZE_BYTES", "5242880"))
FILE_UPLOAD_HANDLERS = ["storage.upload_handlers.BoundedMemoryUploadHandler"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"storage": {"handlers": ["console"], "level": "INFO"}},
}
