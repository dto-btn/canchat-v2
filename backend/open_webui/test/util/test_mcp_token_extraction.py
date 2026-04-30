"""
Unit tests for CrewAI token extraction helper.

These tests verify that _extract_token_usage correctly parses token
consumption from CrewAI CrewOutput objects under a variety of conditions,
including normal operation, missing attributes, None values, and exceptions.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

# ── helper: import function under test ────────────────────────────────────────


def get_extract_fn():
    """Lazily import so the function is only resolved when the test runs."""
    from mcp_backend.integration.crew_mcp_integration import _extract_token_usage

    return _extract_token_usage


# ── fixtures ───────────────────────────────────────────────────────────────────


def make_crew_output(prompt=100, completion=50, total=150):
    """Return a mock CrewOutput object with token_usage populated."""
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )
    return SimpleNamespace(token_usage=usage)


# ── tests: normal extraction ───────────────────────────────────────────────────


def test_extract_token_usage_normal_values():
    """Happy path: all three token counts are returned correctly."""
    fn = get_extract_fn()
    result = fn(make_crew_output(prompt=120, completion=80, total=200))
    assert result == {
        "prompt_tokens": 120,
        "completion_tokens": 80,
        "total_tokens": 200,
    }


def test_extract_token_usage_zero_values():
    """Token counts of zero are valid and must not be replaced with fallback."""
    fn = get_extract_fn()
    result = fn(make_crew_output(prompt=0, completion=0, total=0))
    assert result == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_extract_token_usage_large_values():
    """Large token counts (e.g. long document inputs) are preserved as-is."""
    fn = get_extract_fn()
    result = fn(make_crew_output(prompt=100_000, completion=5_000, total=105_000))
    assert result["prompt_tokens"] == 100_000
    assert result["completion_tokens"] == 5_000
    assert result["total_tokens"] == 105_000


# ── tests: missing / None token_usage ─────────────────────────────────────────


def test_extract_token_usage_no_token_usage_attribute():
    """CrewOutput without a token_usage attribute should return zeroes, not raise."""
    fn = get_extract_fn()
    output = SimpleNamespace()  # no token_usage attr
    result = fn(output)
    assert result == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_extract_token_usage_token_usage_is_none():
    """token_usage=None should return zeroes gracefully."""
    fn = get_extract_fn()
    output = SimpleNamespace(token_usage=None)
    result = fn(output)
    assert result == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_extract_token_usage_none_fields_on_usage():
    """Individual field values of None on the usage object should default to 0."""
    fn = get_extract_fn()
    usage = SimpleNamespace(
        prompt_tokens=None, completion_tokens=None, total_tokens=None
    )
    output = SimpleNamespace(token_usage=usage)
    result = fn(output)
    assert result == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_extract_token_usage_partial_none_fields():
    """Only the None fields should be replaced; present values are kept."""
    fn = get_extract_fn()
    usage = SimpleNamespace(prompt_tokens=42, completion_tokens=None, total_tokens=42)
    output = SimpleNamespace(token_usage=usage)
    result = fn(output)
    assert result["prompt_tokens"] == 42
    assert result["completion_tokens"] == 0
    assert result["total_tokens"] == 42


def test_extract_token_usage_missing_individual_fields():
    """A usage object that is missing some fields should treat them as 0."""
    fn = get_extract_fn()
    usage = SimpleNamespace(prompt_tokens=10)  # no completion_tokens / total_tokens
    output = SimpleNamespace(token_usage=usage)
    result = fn(output)
    assert result["prompt_tokens"] == 10
    assert result["completion_tokens"] == 0
    assert result["total_tokens"] == 0


# ── tests: exception resilience ───────────────────────────────────────────────


def test_extract_token_usage_raises_on_getattr_returns_zero_dict():
    """
    If accessing token_usage raises an exception (e.g. a property that throws),
    _extract_token_usage must catch it and return the zero dict, not propagate.
    """
    fn = get_extract_fn()

    class BrokenOutput:
        @property
        def token_usage(self):
            raise RuntimeError("simulated internal error")

    result = fn(BrokenOutput())
    assert result == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_extract_token_usage_returns_dict_type():
    """Return type is always a plain dict with the three expected keys."""
    fn = get_extract_fn()
    result = fn(make_crew_output())
    assert isinstance(result, dict)
    assert set(result.keys()) == {"prompt_tokens", "completion_tokens", "total_tokens"}
    for v in result.values():
        assert isinstance(v, int)
