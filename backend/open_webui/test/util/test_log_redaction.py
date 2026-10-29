"""OBS-18: source redaction of log records and exception text."""

import io
import json
import logging
import sys

import pytest

from open_webui.observability.logging_config import (
    ContractOTLPHandler,
    JsonFormatter,
    TextFormatter,
    _StdoutHandler,
)
from open_webui.observability.redaction import (
    MESSAGE_OMITTED,
    REDACTED,
    RedactionFilter,
    SecretRegistry,
    format_exception_safe,
    invalidate_secrets,
    is_secret_setting,
    redact_text,
)

log = logging.getLogger("open_webui.test.redaction")


def _record(msg, args=(), exc_info=None, level=logging.ERROR):
    return logging.LogRecord(
        "open_webui.test.redaction", level, __file__, 10, msg, args, exc_info
    )


def _filtered(msg, args=(), exc_info=None):
    record = _record(msg, args, exc_info)
    assert RedactionFilter().filter(record) is True
    return record


def _raise(message):
    raise ValueError(message)


def _exc_info(message="boom"):
    try:
        _raise(message)
    except ValueError:
        return sys.exc_info()


@pytest.mark.parametrize(
    "text, secret",
    [
        ("GET https://api.example.com/v1/search?q=hello&key=abc123", "q=hello"),
        ("ws://host:8080/socket.io/?EIO=4&token=abc123def", "abc123def"),
        ("url /api/v1/retrieval/search?query=my+prompt", "my+prompt"),
        ("postgresql://admin:hunter2pass@db:5432/app", "hunter2pass"),
        ("{'Authorization': 'Bearer abc.def.ghijkl'}", "ghijkl"),
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
        ("got bearer AAAAbbbbCCCC1234 back", "AAAAbbbbCCCC1234"),
        (
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJlLXZhbHVl",
            "c2lnbmF0dXJlLXZhbHVl",
        ),
        ("key sk-proj-0123456789abcdefABCD used", "0123456789abcdef"),
        ('{"api_key": "plain-key-value", "x": 1}', "plain-key-value"),
        ("password=hunter22&user=me", "hunter22"),
        ("client_secret: s3cr3t-value", "s3cr3t-value"),
    ],
)
def test_redact_text_masks_credential_shapes(text, secret):
    redacted = redact_text(text)
    assert secret not in redacted
    assert REDACTED in redacted


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            'connect password="correct horse battery" ok',
            f'connect password="{REDACTED}" ok',
        ),
        ("token='two words' next", f"token='{REDACTED}' next"),
        (
            '{"client_secret": "has \\"escaped\\" quotes and spaces", "x": 1}',
            f'{{"client_secret": "{REDACTED}", "x": 1}}',
        ),
        ("{'password': 'a b c'}", f"{{'password': '{REDACTED}'}}"),
        ('password="unterminated value', f'password="{REDACTED} value'),
    ],
)
def test_quoted_credentials_with_spaces_are_masked_whole(text, expected):
    assert redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "status_code=500 error code: 42 provider=openai",
        "Failed to process 3 files in 1.25s",
        "Web search failed: requests.exceptions.ConnectionError",
        "yes/no? what?x=1",
    ],
)
def test_redact_text_keeps_safe_fields(text):
    assert redact_text(text) == text


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_configured_secret_values_are_cached_and_refreshed():
    clock = _Clock()
    registry = SecretRegistry(refresh_interval=5.0, clock=clock)
    settings = {"OPENAI_API_KEYS": ["first-provider-key", "x"], "ENABLE_API_KEY": True}
    reads = []

    def source():
        reads.append(1)
        return list(settings.items())

    registry.add_source(source)
    assert registry.pattern().sub(REDACTED, "used first-provider-key") == (
        f"used {REDACTED}"
    )
    settings["OPENAI_API_KEYS"] = ["rotated-provider-key"]
    # Cached until the refresh interval or an explicit invalidation.
    assert registry.pattern().pattern == "first\\-provider\\-key"
    assert len(reads) == 1

    clock.now += 5.0
    pattern = registry.pattern()
    assert pattern.sub(REDACTED, "first-provider-key rotated-provider-key") == (
        f"first-provider-key {REDACTED}"
    )
    assert len(reads) == 2

    settings["OPENAI_API_KEYS"] = ["third-provider-key"]
    registry.invalidate()
    assert registry.pattern().sub(REDACTED, "third-provider-key") == REDACTED


def test_invalidation_during_refresh_is_not_lost():
    clock = _Clock()
    registry = SecretRegistry(refresh_interval=5.0, clock=clock)
    settings = {"OPENAI_API_KEYS": "old-provider-key"}
    reads = []

    def source():
        reads.append(1)
        snapshot = list(settings.items())
        if len(reads) == 1:
            # A config change lands while this refresh is collecting values.
            settings["OPENAI_API_KEYS"] = "rotated-provider-key"
            registry.invalidate()
        return snapshot

    registry.add_source(source)
    pattern = registry.pattern()
    # The stale refresh is discarded and retried, so the rotated key is masked
    # immediately rather than after the refresh interval.
    assert len(reads) == 2
    assert pattern.sub(REDACTED, "rotated-provider-key") == REDACTED
    assert registry.pattern() is pattern
    assert len(reads) == 2


def test_refresh_invalidated_on_every_attempt_stays_expired():
    clock = _Clock()
    registry = SecretRegistry(refresh_interval=5.0, clock=clock)
    reads = []

    def source():
        reads.append(1)
        registry.invalidate()
        return [("OPENAI_API_KEYS", f"provider-key-{len(reads):04d}")]

    registry.add_source(source)
    pattern = registry.pattern()
    assert pattern.sub(REDACTED, "provider-key-0003") == REDACTED
    attempts = len(reads)
    registry.pattern()
    # Nothing was cached, so the next lookup re-reads the sources.
    assert len(reads) > attempts


def test_environment_secret_is_masked(monkeypatch):
    monkeypatch.setenv("CANARY_WEBHOOK_SECRET", "env-secret-value-123")
    # Environment changes are picked up on the next refresh.
    invalidate_secrets()
    assert redact_text("sent env-secret-value-123") == f"sent {REDACTED}"


def test_persistent_config_secret_changes_are_masked_at_once(monkeypatch):
    from open_webui import config

    item = config.PersistentConfig.__new__(config.PersistentConfig)
    item.env_name = "CANARY_SEARCH_API_KEY"
    item.value = "persistent-config-key-123"
    monkeypatch.setattr(config, "PERSISTENT_CONFIG_REGISTRY", [item])
    invalidate_secrets()
    assert redact_text("rejected key persistent-config-key-123") == (
        f"rejected key {REDACTED}"
    )

    # An admin rotates the key: setting ``value`` invalidates the cache.
    item.value = "rotated-config-key-456"
    assert redact_text("rejected key rotated-config-key-456") == (
        f"rejected key {REDACTED}"
    )


def test_redaction_cost_is_linear_for_slash_heavy_text():
    import base64
    import os
    import time

    samples = [
        base64.b64encode(os.urandom(750_000)).decode(),
        "/" * 200_000,
        "a+" * 250_000,
    ]
    for text in samples:
        started = time.perf_counter()
        redact_text(text)
        # Quadratic patterns took seconds on these inputs.
        assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(
    "name, secret",
    [
        ("OPENAI_API_KEYS", True),
        ("WEBUI_SECRET_KEY", True),
        ("MICROSOFT_CLIENT_SECRET", True),
        ("JIRA_API_TOKEN", True),
        ("LDAP_APP_PASSWORD", True),
        ("DATABASE_URL", True),
        ("BING_SEARCH_V7_SUBSCRIPTION_KEY", True),
        ("AZURE_STORAGE_KEY", True),
        ("AUTOMATIC1111_API_AUTH", True),
        ("JIRA_PROJECT_KEY", False),
        ("ACCESS_TOKEN_EXPIRES_IN", False),
        ("ENABLE_API_KEY", False),
        ("RAG_CONTEXT_FALLBACK_MAX_TOKENS", False),
        ("TIKTOKEN_ENCODING_NAME", False),
        ("PWD", False),
    ],
)
def test_secret_setting_names(name, secret):
    assert is_secret_setting(name) is secret


def test_traceback_keeps_frames_and_drops_messages_across_the_chain():
    first, context_text, cause = (f"{n} secret text" for n in ("1", "2", "3"))
    try:
        try:
            try:
                _raise(first)
            except ValueError:
                raise KeyError(context_text)
        except KeyError as context:
            raise RuntimeError(cause) from context
    except RuntimeError as error:
        text = format_exception_safe(error)

    assert "secret text" not in text
    assert text.count("Traceback (most recent call last)") == 3
    assert "During handling of the above exception" in text
    assert "The above exception was the direct cause" in text
    assert f"ValueError: {MESSAGE_OMITTED}" in text
    assert f"KeyError: {MESSAGE_OMITTED}" in text
    assert f"RuntimeError: {MESSAGE_OMITTED}" in text
    assert f'File "{__file__}"' in text
    assert ", in _raise" in text
    assert ", in test_traceback_keeps_frames_and_drops_messages_across_the_chain" in (
        text
    )


def test_exception_groups_and_notes_are_rendered_without_text():
    error = ExceptionGroup("group secret", [ValueError("member secret")])
    error.add_note("note secret")
    text = format_exception_safe(error)
    assert "secret" not in text
    assert "ExceptionGroup" in text and "ValueError" in text


def test_traceback_omits_source_lines_with_literal_messages():
    try:
        raise ValueError("literal prompt text in raise statement")
    except ValueError as error:
        text = format_exception_safe(error)

    assert "literal prompt text" not in text
    assert "raise ValueError" not in text
    assert f'File "{__file__}", line ' in text
    assert ", in test_traceback_omits_source_lines_with_literal_messages" in text
    assert f"ValueError: {MESSAGE_OMITTED}" in text


def test_qualified_type_for_library_exceptions():
    import requests

    text = format_exception_safe(requests.exceptions.ConnectionError("x"))
    assert text.strip() == f"requests.exceptions.ConnectionError: {MESSAGE_OMITTED}"


def test_filter_redacts_message_args_and_exception_in_place():
    record = _filtered(
        "call to %s failed for %s",
        ("https://p.example/v1?key=abc123456", "prompt text"),
        _exc_info("provider echoed prompt text"),
    )
    assert record.getMessage() == (
        f"call to https://p.example/v1?{REDACTED} failed for prompt text"
    )
    assert record.args is None
    assert record.exc_info is None
    assert "provider echoed" not in record.exc_text
    assert ", in _raise" in record.exc_text


@pytest.mark.parametrize(
    "msg, args",
    [
        (ValueError("raw exception text"), ()),
        ("failed: %s", (ValueError("raw exception text"),)),
        ("failed: %(error)s", ({"error": ValueError("raw exception text")},)),
    ],
    ids=["exception-as-message", "exception-argument", "exception-mapping"],
)
def test_exception_objects_in_message_or_args_become_type_names(msg, args):
    record = _filtered(msg, args)
    assert "raw exception text" not in record.getMessage()
    assert "ValueError" in record.getMessage()


def test_filter_is_idempotent_and_never_raises():
    record = _filtered("bad %d format", ("not a number",))
    assert "unformattable log arguments: TypeError" in record.getMessage()
    assert "not a number" not in record.getMessage()
    first = record.getMessage()
    assert RedactionFilter().filter(record) is True
    assert record.getMessage() == first


def test_pre_rendered_exc_text_and_stack_info_are_pattern_masked():
    record = _record("x")
    record.exc_text = "Traceback ... https://h/x?token=abc123456"
    record.stack_info = "Stack ... Bearer abcdefghijkl"
    RedactionFilter().filter(record)
    assert "abc123456" not in record.exc_text
    assert "abcdefghijkl" not in record.stack_info


def test_formatters_never_render_exception_messages_even_without_filter():
    record = _record("failed", exc_info=_exc_info("formatter secret"))
    fields = json.loads(JsonFormatter({}).format(record))
    assert "formatter secret" not in json.dumps(fields)
    assert fields["exception.type"] == "ValueError"
    assert ", in _raise" in fields["exception.stacktrace"]

    record = _record("failed", exc_info=_exc_info("formatter secret"))
    text = TextFormatter().format(record)
    assert "formatter secret" not in text
    assert ", in _raise" in text


def test_redacted_record_keeps_exception_fields_in_json():
    record = _filtered("failed", exc_info=_exc_info("json secret"))
    fields = json.loads(JsonFormatter({}).format(record))
    assert fields["exception.type"] == "ValueError"
    assert fields["exception.stacktrace"].startswith("Traceback")
    assert "json secret" not in json.dumps(fields)
    text = TextFormatter().format(record)
    assert "Traceback" in text and ", in _raise" in text


SENTINEL = "handler-failure-sentinel-text"


class _BrokenStream(io.StringIO):
    def write(self, text):
        raise OSError(f"sink unavailable while writing {text}")


def test_stdout_handler_failure_does_not_print_record_or_exception_text(
    capsys, monkeypatch
):
    handler = _StdoutHandler()
    handler.setFormatter(TextFormatter())
    log.addHandler(handler)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", _BrokenStream())
        try:
            log.error("prompt %s", SENTINEL)
        finally:
            log.removeHandler(handler)

    stderr = capsys.readouterr().err
    assert "--- Logging error in _StdoutHandler" in stderr
    assert "OSError" in stderr and ", in write" in stderr
    assert SENTINEL not in stderr


def test_otlp_handler_failure_is_reported_safely(capsys, monkeypatch):
    handler = ContractOTLPHandler()

    def broken_translate(record):
        raise RuntimeError(f"export failed for {record.getMessage()}")

    monkeypatch.setattr(handler, "_translate", broken_translate)
    log.addHandler(handler)
    try:
        log.warning("user %s", SENTINEL)
    finally:
        log.removeHandler(handler)

    stderr = capsys.readouterr().err
    assert "--- Logging error in ContractOTLPHandler" in stderr
    assert "RuntimeError" in stderr and ", in broken_translate" in stderr
    assert SENTINEL not in stderr
