"""Context variables shared by request and operation instrumentation.

Values set here are added to every log record emitted in the same context
(see ``logging_config``). Only internal identifiers belong here: never
email addresses, names, prompts or credentials.
"""

import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from opentelemetry.trace import Span

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
operation_id_var: ContextVar[str | None] = ContextVar("operation_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
chat_id_var: ContextVar[str | None] = ContextVar("chat_id", default=None)
message_id_var: ContextVar[str | None] = ContextVar("message_id", default=None)
model_var: ContextVar[str | None] = ContextVar("model", default=None)
provider_var: ContextVar[str | None] = ContextVar("provider", default=None)

# ASGI ``scope["state"]`` key where the server span context is kept, so the
# completion access record, emitted after the span has ended, still carries it.
SERVER_SPAN_CONTEXT_STATE_KEY = "otel_server_span_context"

LOG_CONTEXT_VARS: dict[str, ContextVar[str | None]] = {
    "request_id": request_id_var,
    "operation_id": operation_id_var,
    "user_id": user_id_var,
    "chat_id": chat_id_var,
    "message_id": message_id_var,
    "model": model_var,
    "provider": provider_var,
}


# The chat operation (``observability.chat.ChatOperation``) whose response is
# being produced, so provider operations started below it can be finished with
# it if their stream is abandoned.
current_chat_operation_var: ContextVar[Any] = ContextVar(
    "current_chat_operation", default=None
)


@dataclass
class OperationContext:
    """State of an operation whose end is decided by whoever consumes it.

    The span, monotonic start and IDs travel with the response object, so the
    path that finishes it (an HTTP stream, a background task or the JSON
    handler) does not depend on the context it runs in. ``claim_finish``
    returns ``True`` exactly once.
    """

    span: Span
    started: float = field(default_factory=time.monotonic)
    operation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    chat_id: str | None = None
    message_id: str | None = None
    _finished: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    @property
    def finished(self) -> bool:
        return self._finished

    def claim_finish(self) -> bool:
        with self._lock:
            if self._finished:
                return False
            self._finished = True
            return True


@contextmanager
def log_context(**values: str | None) -> Iterator[None]:
    """Bind log context fields for the duration of a block."""
    unknown = set(values) - set(LOG_CONTEXT_VARS)
    if unknown:
        raise ValueError(f"Unknown log context fields: {sorted(unknown)}")
    tokens = []
    try:
        for name, value in values.items():
            var = LOG_CONTEXT_VARS[name]
            tokens.append((var, var.set(value)))
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
