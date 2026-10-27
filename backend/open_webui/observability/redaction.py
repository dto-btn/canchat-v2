"""Source redaction shared by every operational log sink (OBS-18).

Exception text often repeats sensitive input: provider errors echo the prompt,
HTTP client errors include the full URL, MCP tool errors include arguments.
Regexes cannot recognize arbitrary prompt text, so exception messages are never
logged. Tracebacks keep their frames (file, line, function) and exception
types, but every message in the chain is replaced by ``[message omitted]``.

On top of that, :func:`redact_text` masks values that have a recognizable
shape: URL query strings, URL credentials, ``Authorization``/``Bearer`` values,
JWT-shaped strings, ``key=value`` secrets and the values of secret settings
(environment variables and registered sources such as ``PersistentConfig``).

:class:`RedactionFilter` applies both to a log record in place, before any
formatter runs, so every handler that shares the record gets the same safe
copy. :class:`SafeHandleErrorMixin` stops ``logging.Handler.handleError`` from
printing the record and the active exception text to stderr.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import traceback
from functools import lru_cache
from typing import Callable, Iterable, Mapping

REDACTED = "[REDACTED]"
MESSAGE_OMITTED = "[message omitted]"
EXCEPTION_TYPE_ATTR = "_canchat_exception_type"
_REDACTED_MARK = "_canchat_redacted"
_MAX_CHAIN = 16
_MIN_SECRET_LENGTH = 8

# Absolute URLs, and path-like tokens followed by a key=value query.
_URL_QUERY = re.compile(
    r"(?P<prefix>(?:\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>?#]*|(?<![\w?])/[^\s'\"<>?#]*)\?)"
    r"(?P<query>[^\s'\"<>#]+)"
)
_URL_USERINFO = re.compile(
    r"(?P<scheme>\b[a-zA-Z][a-zA-Z0-9+.-]*://)[^\s/@:'\"<>]+:[^\s/@'\"<>]+@"
)
_AUTH_HEADER = re.compile(
    r"(?P<name>\b(?:proxy-)?authorization\b[\"']?\s*[:=]\s*[\"']?)"
    r"(?:(?P<scheme>bearer|basic|token|digest|apikey)\s+)?[^\s\"',;}]+",
    re.IGNORECASE,
)
_BEARER = re.compile(
    r"\b(?P<scheme>bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}", re.IGNORECASE
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*")
_KNOWN_KEY_SHAPES = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|AIza[0-9A-Za-z_-]{30,}|AKIA[0-9A-Z]{16})"
)
_SECRET_KEY_NAMES = (
    r"api[_-]?key|apikey|x-api-key|access[_-]?token|refresh[_-]?token|id[_-]?token"
    r"|auth[_-]?token|token|secret|client[_-]?secret|password|passwd|pwd"
    r"|private[_-]?key|sig|signature|cookie|set-cookie"
)
_KEY_VALUE = re.compile(
    rf"(?P<key>[\"']?\b(?:{_SECRET_KEY_NAMES})\b[\"']?\s*(?:=|:)\s*[\"']?)"
    r"(?P<value>[^\s\"'&,;}\]]+)",
    re.IGNORECASE,
)

_SECRET_SETTING = re.compile(
    r"(?:^|_)(?:API_?KEYS?|SECRETS?|SECRET_KEY|CLIENT_SECRET|TOKEN|PASSWORD|PASSWD"
    r"|CREDENTIALS?|PRIVATE_KEY|ACCESS_KEY|CONNECTION_STRING|CONN_STR|DSN"
    r"|WEBHOOK_URL|DATABASE_URL|REDIS_URL)(?:_|$)"
)
_NOT_SECRET_SETTING = re.compile(
    r"EXPIRE|ENABLE|ALLOWED|TIMEOUT|LIMIT|HEADER|COOKIE_|_TTL|_LENGTH|_NAME$"
)


@lru_cache(maxsize=4096)
def is_secret_setting(name: str) -> bool:
    """Whether a setting name (``OPENAI_API_KEYS``) holds a credential."""
    upper = name.upper()
    return bool(_SECRET_SETTING.search(upper)) and not _NOT_SECRET_SETTING.search(upper)


def _secret_values(value: object) -> Iterable[str]:
    if isinstance(value, str):
        candidates = [value, *value.split(";")]
    elif isinstance(value, (list, tuple, set, frozenset)):
        candidates = [item for item in value if isinstance(item, str)]
    else:
        return ()
    return (
        item.strip()
        for item in candidates
        if len(item.strip()) >= _MIN_SECRET_LENGTH
        and not item.strip().isdigit()
        and item.strip().lower() not in {"true", "false", "none", "null"}
    )


SecretSource = Callable[[], Iterable[tuple[str, object]]]


class SecretRegistry:
    """Current values of secret settings, matched literally in log text.

    Sources are re-read on every lookup so a key changed at runtime (for
    example in the admin settings) is masked at once; the pattern is rebuilt
    only when the set of values changes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sources: list[SecretSource] = []
        self._values: frozenset[str] = frozenset()
        self._pattern: re.Pattern[str] | None = None

    def add_source(self, source: SecretSource) -> None:
        with self._lock:
            if source not in self._sources:
                self._sources.append(source)

    def remove_source(self, source: SecretSource) -> None:
        with self._lock:
            if source in self._sources:
                self._sources.remove(source)

    def _collect(self) -> frozenset[str]:
        values: set[str] = set()
        for source in list(self._sources):
            try:
                for name, value in source():
                    if is_secret_setting(name):
                        values.update(_secret_values(value))
            except Exception:
                continue
        return frozenset(values)

    def pattern(self) -> re.Pattern[str] | None:
        values = self._collect()
        with self._lock:
            if values != self._values:
                self._values = values
                self._pattern = (
                    re.compile(
                        "|".join(
                            re.escape(v) for v in sorted(values, key=len, reverse=True)
                        )
                    )
                    if values
                    else None
                )
            return self._pattern


def environment_secrets(
    environ: Mapping[str, str] | None = None,
) -> SecretSource:
    values = os.environ if environ is None else environ
    return lambda: list(values.items())


SECRETS = SecretRegistry()
SECRETS.add_source(environment_secrets())


def register_secret_source(source: SecretSource) -> None:
    """Add ``(setting name, value)`` pairs whose secret values must be masked."""
    SECRETS.add_source(source)


def _mask_auth_header(match: re.Match[str]) -> str:
    scheme = match.group("scheme")
    return f"{match.group('name')}{scheme + ' ' if scheme else ''}{REDACTED}"


def redact_text(text: str) -> str:
    """Mask credential-shaped values. Does not detect arbitrary prompt text."""
    if not text:
        return text
    secrets = SECRETS.pattern()
    if secrets is not None:
        text = secrets.sub(REDACTED, text)
    text = _URL_USERINFO.sub(lambda m: f"{m.group('scheme')}{REDACTED}@", text)
    text = _URL_QUERY.sub(lambda m: f"{m.group('prefix')}{REDACTED}", text)
    text = _AUTH_HEADER.sub(_mask_auth_header, text)
    text = _BEARER.sub(lambda m: f"{m.group('scheme')} {REDACTED}", text)
    text = _JWT.sub(REDACTED, text)
    text = _KNOWN_KEY_SHAPES.sub(REDACTED, text)
    text = _KEY_VALUE.sub(lambda m: f"{m.group('key')}{REDACTED}", text)
    return text


def exception_type_name(exc: BaseException) -> str:
    exc_type = type(exc)
    module = exc_type.__module__
    if module in {"builtins", "__main__", None}:
        return exc_type.__qualname__
    return f"{module}.{exc_type.__qualname__}"


def _render(exc: BaseException, out: list[str], seen: set[int], depth: int) -> None:
    seen.add(id(exc))
    if depth < _MAX_CHAIN:
        cause, context = exc.__cause__, exc.__context__
        if cause is not None and id(cause) not in seen:
            _render(cause, out, seen, depth + 1)
            out.append(
                "\nThe above exception was the direct cause of the following "
                "exception:\n\n"
            )
        elif (
            context is not None
            and not exc.__suppress_context__
            and id(context) not in seen
        ):
            _render(context, out, seen, depth + 1)
            out.append(
                "\nDuring handling of the above exception, another exception "
                "occurred:\n\n"
            )
    if exc.__traceback__ is not None:
        out.append("Traceback (most recent call last):\n")
        out.extend(
            traceback.StackSummary.extract(
                traceback.walk_tb(exc.__traceback__), capture_locals=False
            ).format()
        )
    name = exception_type_name(exc)
    out.append(f"{name}: {MESSAGE_OMITTED}\n" if _has_message(exc) else f"{name}\n")
    if isinstance(exc, BaseExceptionGroup) and depth < _MAX_CHAIN:
        for index, sub in enumerate(exc.exceptions, 1):
            nested: list[str] = []
            _render(sub, nested, seen, depth + 1)
            out.append(f"  +---- sub-exception {index} ----\n")
            out.extend(
                f"  | {line}\n" for line in "".join(nested).rstrip("\n").split("\n")
            )


def _has_message(exc: BaseException) -> bool:
    return bool(exc.args) or bool(getattr(exc, "__notes__", None))


def format_exception_safe(exc: BaseException) -> str:
    """Traceback with frames and exception types, without any message or notes."""
    out: list[str] = []
    _render(exc, out, set(), 0)
    return redact_text("".join(out))


def _exception_safe(value: object) -> object:
    if isinstance(value, BaseException):
        return exception_type_name(value)
    return value


def _redact_record(record: logging.LogRecord) -> None:
    # ``log.exception(e)`` and ``log.error("failed: %s", e)`` would render the
    # exception message into the body; use the exception type instead.
    record.msg = _exception_safe(record.msg)
    if isinstance(record.args, tuple):
        record.args = tuple(_exception_safe(arg) for arg in record.args)
    elif isinstance(record.args, Mapping):
        record.args = {key: _exception_safe(v) for key, v in record.args.items()}
    try:
        message = record.getMessage()
    except Exception as error:
        message = f"{record.msg} [unformattable log arguments: {type(error).__name__}]"
    record.msg = redact_text(str(message))
    record.args = None
    exc_info = record.exc_info
    if exc_info and exc_info[1] is not None:
        record.__dict__[EXCEPTION_TYPE_ATTR] = exception_type_name(exc_info[1])
        record.exc_text = format_exception_safe(exc_info[1])
        # Drop the live exception so no later handler or formatter can render
        # its message; exc_text carries the safe traceback.
        record.exc_info = None
    elif record.exc_text:
        record.exc_text = redact_text(record.exc_text)
    if record.stack_info:
        record.stack_info = redact_text(record.stack_info)


class RedactionFilter(logging.Filter):
    """Redacts a record in place; never drops a record and never raises."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.__dict__.get(_REDACTED_MARK):
            return True
        try:
            _redact_record(record)
        except Exception as error:
            record.msg = f"[log record redaction failed: {type(error).__name__}]"
            record.args = None
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        record.__dict__[_REDACTED_MARK] = True
        return True


def exception_fields(record: logging.LogRecord) -> dict[str, str]:
    """``exception.type`` and ``exception.stacktrace`` for a record, if any.

    Works for both redacted records and raw ones (formatters used without the
    filter), so no sink renders an exception message either way.
    """
    exc_type = record.__dict__.get(EXCEPTION_TYPE_ATTR)
    if exc_type:
        return {
            "exception.type": exc_type,
            "exception.stacktrace": record.exc_text or "",
        }
    if record.exc_info and record.exc_info[1] is not None:
        exc = record.exc_info[1]
        return {
            "exception.type": exception_type_name(exc),
            "exception.stacktrace": format_exception_safe(exc),
        }
    return {}


class SafeHandleErrorMixin:
    """Reports handler failures without the record content or exception text.

    The stdlib ``handleError`` prints the record's message, arguments and the
    active (possibly chained) exception to stderr, bypassing every filter.
    """

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        if not logging.raiseExceptions or sys.stderr is None:
            return
        exc = sys.exc_info()[1]
        try:
            sys.stderr.write(
                f"--- Logging error in {type(self).__name__} "
                f"(logger={record.name}, source={record.module}:{record.lineno}) ---\n"
            )
            if exc is not None:
                sys.stderr.write(format_exception_safe(exc))
        except Exception:
            pass
        finally:
            del exc
