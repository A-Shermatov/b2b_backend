import logging
from collections import defaultdict
from datetime import datetime, timezone

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_redis_client = None
_memory_rate_limit: dict[str, list[float]] = defaultdict(list)


async def _discard_redis(client, exc: Exception) -> None:
    global _redis_client
    logger.warning("Redis unavailable, using in-memory fallback: %s", exc)
    if _redis_client is client:
        _redis_client = None
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            pass


async def get_redis():
    global _redis_client
    if not settings.redis_url:
        return None
    if _redis_client is not None:
        return _redis_client
    client = None
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        _redis_client = client
        return client
    except Exception as exc:
        await _discard_redis(client, exc)
        return None


async def check_rate_limit(key: str, limit: int, window_seconds: int = 60) -> bool:
    redis = await get_redis()
    now = datetime.now(timezone.utc).timestamp()

    if redis:
        try:
            pipe = redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds)
            results = await pipe.execute()
            return int(results[0]) <= limit
        except Exception as exc:
            await _discard_redis(redis, exc)

    bucket = _memory_rate_limit[key]
    bucket[:] = [t for t in bucket if now - t < window_seconds]
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


async def blacklist_refresh_token(jti_hash: str, expires_at: datetime) -> None:
    redis = await get_redis()
    ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    if ttl <= 0:
        return
    if redis:
        try:
            await redis.setex(f"refresh_blacklist:{jti_hash}", ttl, "1")
            return
        except Exception as exc:
            await _discard_redis(redis, exc)
    _memory_rate_limit[f"bl:{jti_hash}"].append(datetime.now(timezone.utc).timestamp())


async def is_refresh_token_blacklisted(jti_hash: str) -> bool:
    redis = await get_redis()
    if redis:
        try:
            return await redis.exists(f"refresh_blacklist:{jti_hash}") > 0
        except Exception as exc:
            await _discard_redis(redis, exc)
    return f"bl:{jti_hash}" in _memory_rate_limit


async def mark_refresh_rotated(jti_hash: str, grace_seconds: int) -> None:
    """Remember that a refresh token was just rotated (parallel tabs may reuse it briefly)."""
    redis = await get_redis()
    if redis:
        try:
            await redis.setex(f"refresh_grace:{jti_hash}", grace_seconds, "1")
            return
        except Exception as exc:
            await _discard_redis(redis, exc)
    _memory_rate_limit[f"grace:{jti_hash}"] = [
        datetime.now(timezone.utc).timestamp() + grace_seconds
    ]


async def refresh_rotation_in_grace(jti_hash: str) -> bool:
    redis = await get_redis()
    if redis:
        try:
            return await redis.exists(f"refresh_grace:{jti_hash}") > 0
        except Exception as exc:
            await _discard_redis(redis, exc)
    until = _memory_rate_limit.get(f"grace:{jti_hash}")
    return bool(until) and until[0] > datetime.now(timezone.utc).timestamp()
