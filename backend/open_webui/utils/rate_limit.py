"""Fixed-window rate limiting per key (for example, per user).

Counts are kept in memory, so each process (pod) has its own budget and a
restart resets it. That is enough to stop a user submitting in a loop.
"""

import time
from typing import Dict, Optional, Tuple


class RateLimiter:
    def __init__(self, name: str, limit: int, window_seconds: int):
        self.name = name
        self.limit = limit
        self.window_seconds = window_seconds
        # key -> (window start, count)
        self._windows: Dict[str, Tuple[float, int]] = {}

    def hit(self, key: str) -> Optional[int]:
        """Count one request for key.

        Returns None if it is allowed, or the seconds until the window resets
        if the limit has been reached.
        """
        now = time.monotonic()
        self._drop_expired(now)

        window_start, count = self._windows.get(key, (now, 0))
        if now - window_start >= self.window_seconds:
            window_start, count = now, 0

        count += 1
        self._windows[key] = (window_start, count)
        if count <= self.limit:
            return None
        return max(1, int(self.window_seconds - (now - window_start)))

    def _drop_expired(self, now: float) -> None:
        """Forget windows that have ended, so memory doesn't grow forever."""
        expired = [
            key
            for key, (window_start, _) in self._windows.items()
            if now - window_start >= self.window_seconds
        ]
        for key in expired:
            del self._windows[key]
