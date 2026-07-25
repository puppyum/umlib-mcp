import asyncio
import itertools
import time

from . import config


class RateLimited(Exception):
    def __init__(self, retry_after_s: float, disabled: bool = False):
        self.retry_after_s = round(retry_after_s)
        super().__init__(
            "licensed fetches are switched off (max_fetches_per_hour is 0)"
            if disabled
            else f"licensed-fetch rate cap reached ({config.MAX_FETCHES_PER_HOUR}/hour); "
            f"retry in about {self.retry_after_s}s"
        )


# Wall clock, not monotonic: on macOS the monotonic clock stops while the
# machine is asleep, so a capped user would still be capped after a night's
# sleep. Entries are (token, timestamp); the token lets a caller give back
# its own slot rather than whichever was added last.
_slots: dict[int, float] = {}
_next_token = itertools.count(1)
_last_fetch = 0.0
_lock = asyncio.Lock()


def _cap() -> int:
    # 0 means the user switched licensed fetching off, and used to be silently
    # read as 1; remaining_this_hour() agrees with this, so status and
    # behaviour cannot disagree
    return max(0, config.MAX_FETCHES_PER_HOUR)


def _expire(now: float) -> None:
    for token, at in list(_slots.items()):
        # a clock jumped backwards is treated as "expired" rather than
        # trapping the user behind a window that can never close
        if now - at > 3600 or at > now:
            del _slots[token]


async def acquire() -> int:
    """Charge one slot and return its token. Pass the token to refund() if the
    attempt turns out never to have reached licensed content."""
    global _last_fetch
    async with _lock:  # concurrent calls must not both pass the cap check
        now = time.time()
        _expire(now)
        if _cap() <= 0:
            raise RateLimited(0, disabled=True)
        if len(_slots) >= _cap():
            oldest = min(_slots.values())
            raise RateLimited(3600 - (now - oldest))
        gap = config.MIN_FETCH_INTERVAL_S - (time.monotonic() - _last_fetch)
        if gap > 0:
            await asyncio.sleep(gap)
        _last_fetch = time.monotonic()
        token = next(_next_token)
        _slots[token] = time.time()
        return token


def refund(token: int | None) -> None:
    """Hand back one specific slot. Used when a fetch was charged up front but
    never reached licensed content (no session, or the host isn't proxied), so
    a signed-out user cannot burn their quota. Refunding twice is a no-op, and
    a concurrent fetch's slot is never taken by mistake."""
    if token is not None:
        _slots.pop(token, None)


def remaining_this_hour() -> int:
    _expire(time.time())
    return max(0, _cap() - len(_slots))
