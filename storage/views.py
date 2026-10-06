import logging
import uuid

import redis
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from config.redis_client import client

logger = logging.getLogger(__name__)

KEY_PREFIX = "temporary_storage"
FILE_FIELDS = ("file_1", "file_2")


def redis_key(record_id):
    return f"{KEY_PREFIX}:{record_id}"


@require_GET
@ensure_csrf_cookie
def index(request):
    """Minimal manual-testing page; it talks to the JSON API via fetch."""
    return render(
        request,
        "storage/index.html",
        {"max_upload_size_bytes": settings.MAX_UPLOAD_SIZE_BYTES},
    )


@require_POST
def create_storage(request):
    missing = [f for f in FILE_FIELDS if f not in request.FILES]
    text = request.POST.get("text", "")
    if not text.strip():
        missing.append("text")
    if missing:
        return JsonResponse(
            {"error": "missing_fields", "detail": "Required fields are missing or empty.", "fields": missing},
            status=400,
        )

    max_size = settings.MAX_UPLOAD_SIZE_BYTES
    too_large = [f for f in FILE_FIELDS if request.FILES[f].size > max_size]
    if too_large:
        return JsonResponse(
            {
                "error": "file_too_large",
                "detail": f"Each file must be at most {max_size} bytes.",
                "fields": too_large,
                "max_upload_size_bytes": max_size,
            },
            status=413,
        )

    file_1, file_2 = request.FILES["file_1"], request.FILES["file_2"]
    # uuid4 draws 122 random bits from os.urandom: not sequential, not time-based.
    record_id = str(uuid.uuid4())
    ttl = settings.REDIS_TTL_SECONDS
    key = redis_key(record_id)

    try:
        pipe = client.pipeline(transaction=True)
        pipe.hset(
            key,
            mapping={
                "file_1_name": file_1.name,
                "file_1_content": file_1.read(),
                "file_2_name": file_2.name,
                "file_2_content": file_2.read(),
                "text": text,
            },
        )
        pipe.expire(key, ttl)
        pipe.execute()
    except redis.RedisError:
        return storage_unavailable("storing", record_id)

    logger.info(
        "Stored record id=%s file_1_size=%d file_2_size=%d ttl_seconds=%d",
        record_id, file_1.size, file_2.size, ttl,
    )
    return JsonResponse({"id": record_id, "ttl_seconds": ttl}, status=201)


def not_found(record_id):
    return JsonResponse({"id": record_id, "exists": False}, status=404)


def is_valid_id(record_id):
    try:
        uuid.UUID(record_id)
    except ValueError:
        return False
    return True


def storage_unavailable(action, record_id):
    logger.exception("Redis error %s record %s", action, record_id)
    return JsonResponse({"error": "storage_unavailable"}, status=503)


@require_http_methods(["GET", "DELETE"])
def storage_detail(request, record_id):
    if not is_valid_id(record_id):
        return not_found(record_id)
    if request.method == "DELETE":
        return delete_storage(record_id)
    return get_storage(record_id)


def get_storage(record_id):
    key = redis_key(record_id)
    try:
        pipe = client.pipeline(transaction=True)
        pipe.hmget(key, "file_1_name", "file_2_name", "text")
        pipe.ttl(key)
        (file_1_name, file_2_name, text), ttl = pipe.execute()
    except redis.RedisError:
        return storage_unavailable("reading", record_id)

    # TTL -2 means the key does not exist (expired or never created).
    if ttl == -2 or file_1_name is None:
        return not_found(record_id)

    return JsonResponse(
        {
            "id": record_id,
            "exists": True,
            "file_1_name": file_1_name.decode(),
            "file_2_name": file_2_name.decode(),
            "text": text.decode(),
            "ttl_remaining_seconds": ttl,
        }
    )


def delete_storage(record_id):
    try:
        deleted = client.delete(redis_key(record_id))
    except redis.RedisError:
        return storage_unavailable("deleting", record_id)

    if not deleted:
        return not_found(record_id)

    logger.info("Deleted record id=%s", record_id)
    return JsonResponse({"id": record_id, "deleted": True})


@require_POST
def touch_storage(request, record_id):
    if not is_valid_id(record_id):
        return not_found(record_id)

    key = redis_key(record_id)
    ttl = settings.REDIS_TTL_SECONDS
    try:
        # EXPIRE only acts on an existing key, so expired data is never recreated.
        pipe = client.pipeline(transaction=True)
        pipe.expire(key, ttl)
        pipe.ttl(key)
        touched, ttl_remaining = pipe.execute()
    except redis.RedisError:
        return storage_unavailable("touching", record_id)

    if not touched:
        return not_found(record_id)

    logger.info("Touched record id=%s ttl_seconds=%d", record_id, ttl)
    return JsonResponse(
        {"id": record_id, "touched": True, "ttl_seconds": ttl, "ttl_remaining_seconds": ttl_remaining}
    )


@require_GET
def poc_config(request):
    """Non-sensitive settings only, useful to explain the demo."""
    return JsonResponse(
        {
            "redis_ttl_seconds": settings.REDIS_TTL_SECONDS,
            "max_upload_size_bytes": settings.MAX_UPLOAD_SIZE_BYTES,
        }
    )
