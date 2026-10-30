"""OBS-17: self-tests for the source-confidentiality canary harness.

Most tests check that the harness detects leaks, so they use raw records
(``source_filters=False``); the production redaction filter would otherwise
mask some of the deliberate leaks. The last tests check that the default
harness applies the production source filters (OBS-18).
"""

import logging
import subprocess
import sys
from urllib.parse import quote_plus

import pytest
from opentelemetry import trace

from open_webui.observability.providers import initialize_providers
from open_webui.test.util.canary_harness import (
    CanaryHarness,
    CanaryLeakError,
    Canaries,
)

leaky = logging.getLogger("open_webui.test.leaky")
_ORIGINAL_CALL_HANDLERS = logging.Logger.callHandlers


@pytest.fixture
def canary_harness(capfd):
    with CanaryHarness(capfd, source_filters=False) as harness:
        yield harness


def _leaks(harness):
    harness.expect_leaks()
    with pytest.raises(CanaryLeakError) as error:
        harness.assert_no_canaries(require_records=False)
    return {(leak.canary, leak.sink) for leak in error.value.leaks}


def test_clean_code_passes(canary_harness):
    leaky.info("handled request prompt_len=%d", len(canary_harness.canaries.prompt))
    canary_harness.assert_no_canaries()


def test_leaky_debug_logger_is_detected_in_every_log_sink(canary_harness):
    leaky.debug("prompt=%s", canary_harness.canaries.prompt)

    found = _leaks(canary_harness)
    assert {
        ("prompt", "log records"),
        ("prompt", "json handler"),
        ("prompt", "otlp logs"),
    } <= found


def test_application_loggers_are_lowered_to_debug(capfd):
    leaky.setLevel(logging.WARNING)
    try:
        with CanaryHarness(capfd, source_filters=False) as harness:
            leaky.debug("token=%s", harness.canaries.token)
            assert ("token", "log records") in _leaks(harness)
        assert leaky.level == logging.WARNING
    finally:
        leaky.setLevel(logging.NOTSET)


@pytest.mark.parametrize(
    "emit, label",
    [
        (lambda c: leaky.info("token prefix %s", c.token[:20]), "token"),
        (lambda c: leaky.info("key ends %s", c.api_key[-12:]), "api key"),
        (lambda c: leaky.info("USER %s", c.email.upper()), "email"),
        (lambda c: leaky.info("url /search?q=%s", quote_plus(c.prompt)), "prompt"),
        (lambda c: leaky.info("first 100: %s", c.prompt[:100]), "prompt"),
        (lambda c: leaky.info("ok", extra={"user_name": c.name}), "name"),
    ],
    ids=[
        "token-prefix",
        "key-suffix",
        "case",
        "url-encoded",
        "truncated",
        "extra-field",
    ],
)
def test_fragments_and_variants_are_detected(canary_harness, emit, label):
    emit(canary_harness.canaries)
    assert (label, "log records") in _leaks(canary_harness)


def test_exception_chain_is_detected(canary_harness):
    canaries = canary_harness.canaries
    try:
        try:
            raise ValueError(f"provider echoed {canaries.prompt}")
        except ValueError as cause:
            raise RuntimeError("upstream failed") from cause
    except RuntimeError:
        leaky.exception("request failed")

    found = _leaks(canary_harness)
    assert ("prompt", "log records") in found
    # The production formatters never render exception messages (OBS-18).
    assert ("prompt", "json handler") not in found
    assert ("prompt", "otlp logs") not in found


def test_print_and_stderr_are_detected(canary_harness):
    print(f"debug: {canary_harness.canaries.api_key}")
    sys.stderr.write(f"warn: {canary_harness.canaries.password}\n")

    found = _leaks(canary_harness)
    assert ("api key", "stdout") in found
    assert ("password", "stderr") in found


def test_subprocess_stderr_is_detected(canary_harness):
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write(sys.argv[1])",
            canary_harness.canaries.token,
        ],
        check=True,
    )

    assert ("token", "stderr") in _leaks(canary_harness)


def test_handler_error_output_is_detected(canary_harness):
    class BrokenHandler(logging.Handler):
        def emit(self, record):
            try:
                raise OSError("sink unavailable")
            except OSError:
                self.handleError(record)

    handler = BrokenHandler()
    leaky.addHandler(handler)
    try:
        leaky.info("user %s", canary_harness.canaries.email)
    finally:
        leaky.removeHandler(handler)

    assert ("email", "stderr") in _leaks(canary_harness)


def test_non_propagating_logger_is_captured(capfd):
    isolated = logging.getLogger("open_webui.test.leaky.isolated")
    isolated.propagate = False
    try:
        with CanaryHarness(capfd, source_filters=False) as harness:
            isolated.warning("key=%s", harness.canaries.api_key)
            assert ("api key", "log records") in _leaks(harness)
    finally:
        isolated.propagate = True


def test_logger_made_non_propagating_after_start_is_captured(capfd):
    with CanaryHarness(capfd) as harness:
        late = logging.getLogger(f"open_webui.test.leaky.late.{harness.canaries.tag}")
        late.propagate = False
        late.addHandler(logging.NullHandler())
        try:
            late.error("prompt=%s", harness.canaries.prompt)
            assert ("prompt", "log records") in _leaks(harness)
        finally:
            late.handlers.clear()
            late.propagate = True
    assert logging.Logger.callHandlers is _ORIGINAL_CALL_HANDLERS


def test_disabled_private_otlp_provider_is_rejected(capfd, monkeypatch):
    initialize_providers()
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    root_handlers = list(logging.getLogger().handlers)
    with pytest.raises(RuntimeError, match="OTEL_SDK_DISABLED"):
        with CanaryHarness(capfd):
            pass
    assert logging.getLogger().handlers == root_handlers


def test_final_check_catches_output_after_explicit_check(capfd):
    with CanaryHarness(capfd) as harness:
        leaky.info("request completed")
        harness.assert_no_canaries()
        print(harness.canaries.prompt)
        with pytest.raises(CanaryLeakError):
            harness.final_check()
        harness.expect_leaks()
        harness.final_check()


def test_audit_loggers_are_not_operational_sinks(canary_harness):
    content = logging.getLogger("open_webui.audit.content")
    content.info("content %s", canary_harness.canaries.prompt)
    content.warning("content %s", canary_harness.canaries.prompt)
    leaky.info("operational record")
    canary_harness.assert_no_canaries()


def test_span_attributes_and_exception_events_are_detected(canary_harness):
    canaries = canary_harness.canaries
    tracer = trace.get_tracer("open_webui.test.leaky")
    with tracer.start_as_current_span("chat") as span:
        span.set_attribute("user.email", canaries.email)
    with pytest.raises(RuntimeError):
        with tracer.start_as_current_span("provider"):
            raise RuntimeError(f"rejected {canaries.prompt}")

    found = _leaks(canary_harness)
    assert ("email", "spans") in found
    assert ("prompt", "spans") in found


def test_spans_are_only_captured_while_active(capfd):
    tracer = trace.get_tracer("open_webui.test.leaky")
    with CanaryHarness(capfd) as first:
        pass
    with tracer.start_as_current_span(first.canaries.prompt):
        pass
    with CanaryHarness(capfd) as second:
        leaky.info("ok")
        second.assert_no_canaries()
        assert "spans" not in {leak.sink for leak in first.find_leaks()}


def test_harness_requires_captured_records(canary_harness):
    with pytest.raises(AssertionError, match="captured no log records"):
        canary_harness.assert_no_canaries()


def test_handlers_and_levels_are_restored(capfd):
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    with CanaryHarness(capfd):
        assert len(root.handlers) > len(handlers)
        assert root.level == logging.DEBUG
    assert root.handlers == handlers
    assert root.level == level


def test_canaries_are_unique_per_harness():
    first, second = Canaries(), Canaries()
    assert first.prompt != second.prompt
    assert first.token.count(".") == 2 and first.token.startswith("eyJ")
    extra = first.add("tool result", "tool-output-xyz")
    assert first["tool result"].value == extra


def test_default_harness_applies_production_source_filters(capfd):
    with CanaryHarness(capfd) as harness:
        canaries = harness.canaries
        try:
            raise ValueError(f"provider echoed {canaries.prompt}")
        except ValueError:
            leaky.exception("request failed with %s", canaries.token)
        harness.assert_no_canaries()
        (record,) = [r for r in harness.records if r.name == leaky.name]
        assert "Traceback (most recent call last)" in record.exc_text
        assert "test_canary_harness.py" in record.exc_text
        assert "ValueError: [message omitted]" in record.exc_text


def test_default_harness_still_detects_unmaskable_text(capfd):
    with CanaryHarness(capfd) as harness:
        leaky.info("prompt=%s", harness.canaries.prompt)
        found = _leaks(harness)
    assert {
        ("prompt", "log records"),
        ("prompt", "json handler"),
        ("prompt", "otlp logs"),
    } <= found
