import asyncio
import time
from collections import deque

from . import config


class RateLimited(Exception):
    def __init__(self, retry_after_s: float):
        self.retry_after_s = round(retry_after_s)
        super().__init__(
            f"licensed-fetch rate cap reached ({config.MAX_FETCHES_PER_HOUR}/hour); "
            f"retry in about {self.retry_after_s}s"
        )


_times: deque[float] = deque()
_last_fetch = 0.0
_lock = asyncio.Lock()


async def acquire() -> None:
    global _last_fetch
    async with _lock:  # concurrent calls must not both pass the cap check
        now = time.monotonic()
        while _times and now - _times[0] > 3600:
            _times.popleft()
        if len(_times) >= config.MAX_FETCHES_PER_HOUR:
            raise RateLimited(3600 - (now - _times[0]))
        gap = config.MIN_FETCH_INTERVAL_S - (now - _last_fetch)
        if gap > 0:
            await asyncio.sleep(gap)
        _last_fetch = time.monotonic()
        _times.append(_last_fetch)


def refund() -> None:
    """Hand back the most recent slot. Used when a fetch was charged up front
    but never actually reached licensed content (no session, or the publisher
    isn't proxied at all), so a signed-out user can't burn their quota."""
    if _times:
        _times.pop()


def remaining_this_hour() -> int:
    now = time.monotonic()
    while _times and now - _times[0] > 3600:
        _times.popleft()
    return max(0, config.MAX_FETCHES_PER_HOUR - len(_times))
