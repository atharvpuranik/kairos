from functools import lru_cache

from upstash_redis.asyncio import Redis

from config import get_settings


@lru_cache
def get_redis() -> Redis:
    settings = get_settings()
    return Redis(url=settings.upstash_redis_rest_url, token=settings.upstash_redis_rest_token)
