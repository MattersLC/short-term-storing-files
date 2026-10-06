import redis
from django.http import JsonResponse

from .redis_client import client


def health(request):
    try:
        client.ping()
    except redis.RedisError as exc:
        return JsonResponse(
            {"status": "error", "redis": "unavailable", "detail": str(exc)},
            status=503,
        )
    return JsonResponse({"status": "ok", "redis": "ok"})


def csrf_failure(request, reason=""):
    return JsonResponse(
        {"error": "csrf_failed", "detail": "CSRF verification failed. Reload the page and try again."},
        status=403,
    )
