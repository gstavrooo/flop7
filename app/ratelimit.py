"""In-process rate limiting for the public API (stdlib only, one uvicorn worker).

Two independent guards: a per-key hit budget (game creation) and a per-key
concurrency budget (SSE streams). Keys are client IPs; alwaysdata's proxy headers
make the real visitor visible to the app (see ``app.main.client_key``).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """Bounded, thread-safe hit windows and slot counters.

    The clock is injectable so tests can advance time without sleeping. Both
    dictionaries are capped so spoofed keys cannot grow memory without limit.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, max_keys: int = 10000) -> None:
        self._clock = clock
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._slots: dict[str, int] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: float) -> int | None:
        """Consume one hit; return seconds to wait when the budget is exhausted."""
        now = self._clock()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] <= now - window:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(bucket[0] + window - now) + 1)
                self._prune_hits(now, window)
                return retry_after
            bucket.append(now)
            self._prune_hits(now, window)
            return None

    def _prune_hits(self, now: float, window: float) -> None:
        excess = len(self._hits) - self._max_keys
        if excess <= 0:
            return
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= now - window][:excess]:
            del self._hits[key]
        excess = len(self._hits) - self._max_keys
        if excess > 0:
            oldest = sorted(self._hits, key=lambda k: self._hits[k][-1] if self._hits[k] else 0)
            for key in oldest[:excess]:
                del self._hits[key]

    def acquire_slot(self, key: str, limit: int) -> bool:
        """Reserve one concurrent slot for ``key``."""
        with self._lock:
            used = self._slots.get(key, 0)
            if used >= limit or (key not in self._slots and len(self._slots) >= self._max_keys):
                return False
            self._slots[key] = used + 1
            return True

    def release_slot(self, key: str) -> None:
        """Release a slot; never leaves a zero or negative counter behind."""
        with self._lock:
            used = self._slots.get(key, 1) - 1
            if used <= 0:
                self._slots.pop(key, None)
            else:
                self._slots[key] = used
