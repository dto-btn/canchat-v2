"""Unit tests for open_webui.utils.rate_limit.RateLimiter."""

import pytest

from open_webui.utils import rate_limit
from open_webui.utils.rate_limit import RateLimiter


@pytest.fixture()
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: now[0])
    return now


class TestRateLimiter:
    def test_allows_up_to_the_limit(self, clock):
        limiter = RateLimiter("test", limit=3, window_seconds=60)

        assert [limiter.hit("user-1") for _ in range(3)] == [None, None, None]

    def test_blocks_over_the_limit_with_seconds_left(self, clock):
        limiter = RateLimiter("test", limit=2, window_seconds=60)
        limiter.hit("user-1")
        limiter.hit("user-1")

        clock[0] += 15
        assert limiter.hit("user-1") == 45

    def test_resets_after_the_window(self, clock):
        limiter = RateLimiter("test", limit=1, window_seconds=60)
        limiter.hit("user-1")
        assert limiter.hit("user-1") is not None

        clock[0] += 60
        assert limiter.hit("user-1") is None

    def test_window_does_not_slide_on_each_hit(self, clock):
        limiter = RateLimiter("test", limit=1, window_seconds=60)
        limiter.hit("user-1")
        clock[0] += 50
        limiter.hit("user-1")  # blocked, must not restart the window

        clock[0] += 10
        assert limiter.hit("user-1") is None

    def test_keys_are_counted_separately(self, clock):
        limiter = RateLimiter("test", limit=1, window_seconds=60)
        limiter.hit("user-1")

        assert limiter.hit("user-2") is None

    def test_forgets_expired_windows(self, clock):
        limiter = RateLimiter("test", limit=1, window_seconds=60)
        for user in range(100):
            limiter.hit(f"user-{user}")

        clock[0] += 60
        limiter.hit("someone-new")

        assert list(limiter._windows) == ["someone-new"]
