import re
import time
import logging

from .errors import BadRequest, RateLimited
from .logging_utils import log_event

LOGGER = logging.getLogger("shortener.rate_limit")


WINDOWS = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
}


def parse_limit(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(second|minute|hour|day)\s*", value)
    if not match:
        raise BadRequest(f"Invalid rate limit configuration: {value}", code="invalid_rate_limit")
    return int(match.group(1)), WINDOWS[match.group(2)]


class RateLimiter:
    def __init__(self, db):
        self.db = db

    def check(self, key: str, limit_spec: str):
        limit, window_seconds = parse_limit(limit_spec)
        now = int(time.time())
        window_start = now - (now % window_seconds)
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT window_start, count FROM rate_limits WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None or int(row["window_start"]) != window_start:
                conn.execute(
                    "REPLACE INTO rate_limits(key, window_start, count) VALUES (?, ?, ?)",
                    (key, window_start, 1),
                )
                return
            count = int(row["count"])
            if count >= limit:
                raise RateLimited()
            conn.execute(
                "UPDATE rate_limits SET count = count + 1 WHERE key = ?",
                (key,),
            )


class RedisRateLimiter:
    def __init__(self, redis_url: str):
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("Redis rate limiting requires redis-py. Install production dependencies.") from exc
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def ready(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def check(self, key: str, limit_spec: str):
        limit, window_seconds = parse_limit(limit_spec)
        now = int(time.time())
        window_start = now - (now % window_seconds)
        redis_key = f"rl:{key}:{window_start}"
        try:
            count = self.client.incr(redis_key)
            if count == 1:
                self.client.expire(redis_key, window_seconds * 2)
            if count > limit:
                raise RateLimited()
        except RateLimited:
            raise
        except Exception as exc:
            log_event(LOGGER, logging.ERROR, "dependency.unavailable", dependency="redis", operation="rate_limit", errorType=type(exc).__name__)
            raise


def create_rate_limiter(config, db):
    if config.rate_limit_backend == "redis":
        if not config.redis_url:
            raise RuntimeError("REDIS_URL is required when RATE_LIMIT_BACKEND=redis.")
        return RedisRateLimiter(config.redis_url)
    return RateLimiter(db)
