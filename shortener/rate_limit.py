import re
import time

from .errors import BadRequest, RateLimited


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

