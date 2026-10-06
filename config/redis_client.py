import redis
from django.conf import settings

client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    socket_connect_timeout=2,
    socket_timeout=2,
)
