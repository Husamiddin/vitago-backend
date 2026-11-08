import json
import logging
import threading
import time
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger("vitago.cache")

# In-Memory Cache Fallback Structure
class InMemoryCache:
    def __init__(self, max_items: int = 1000):
        self._store: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()
        self._max_items = max_items

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            expiry, serialized = item
            if expiry < now:
                del self._store[key]
                return None
            try:
                return json.loads(serialized)
            except Exception:
                return None

    def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        now = time.time()
        expiry = now + ttl_seconds
        try:
            serialized = json.dumps(value, ensure_ascii=False)
        except Exception:
            return

        with self._lock:
            # Simple eviction if store exceeds max items
            if len(self._store) >= self._max_items:
                expired_keys = [k for k, (exp, _) in self._store.items() if exp < now]
                for k in expired_keys:
                    del self._store[k]
                if len(self._store) >= self._max_items:
                    # remove oldest 10%
                    keys_to_remove = list(self._store.keys())[: (self._max_items // 10) or 1]
                    for k in keys_to_remove:
                        del self._store[k]

            self._store[key] = (expiry, serialized)

    def delete_prefix(self, prefix: str) -> None:
        with self._lock:
            matching = [k for k in self._store.keys() if k.startswith(prefix)]
            for k in matching:
                del self._store[k]

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_memory_cache = InMemoryCache()
_redis_client = None
_redis_checked = False


def _get_redis():
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client

    _redis_checked = True
    redis_url = settings.REDIS_URL
    if not redis_url:
        return None

    try:
        import redis
        client = redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        _redis_client = client
        logger.info("Connected to Redis cache successfully.")
    except Exception as exc:
        logger.warning(f"Redis cache unavailable, falling back to In-Memory cache: {exc}")
        _redis_client = None

    return _redis_client


def get_cached(key: str) -> Optional[Any]:
    """Retrieve JSON-serializable item from Redis or In-Memory cache."""
    r = _get_redis()
    if r is not None:
        try:
            raw = r.get(key)
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.debug(f"Redis get failed ({exc}), falling back to memory cache.")
    return _memory_cache.get(key)


def set_cached(key: str, value: Any, ttl_seconds: int = 300) -> None:
    """Store JSON-serializable item in Redis and In-Memory cache."""
    _memory_cache.set(key, value, ttl_seconds)
    r = _get_redis()
    if r is not None:
        try:
            r.setex(key, ttl_seconds, json.dumps(value, ensure_ascii=False))
        except Exception as exc:
            logger.debug(f"Redis set failed ({exc}).")


def invalidate_cache(prefix: str) -> None:
    """Invalidate all cached keys starting with prefix."""
    _memory_cache.delete_prefix(prefix)
    r = _get_redis()
    if r is not None:
        try:
            keys = r.keys(f"{prefix}*")
            if keys:
                r.delete(*keys)
        except Exception as exc:
            logger.debug(f"Redis delete failed ({exc}).")
