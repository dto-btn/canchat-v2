"""Context variables shared by request and operation instrumentation.

Values set here are added to every log record emitted in the same context
(see ``logging_config``). Only internal identifiers belong here: never
email addresses, names, prompts or credentials.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
operation_id_var: ContextVar[str | None] = ContextVar("operation_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
chat_id_var: ContextVar[str | None] = ContextVar("chat_id", default=None)
message_id_var: ContextVar[str | None] = ContextVar("message_id", default=None)
model_var: ContextVar[str | None] = ContextVar("model", default=None)
provider_var: ContextVar[str | None] = ContextVar("provider", default=None)

LOG_CONTEXT_VARS: dict[str, ContextVar[str | None]] = {
    "request_id": request_id_var,
    "operation_id": operation_id_var,
    "user_id": user_id_var,
    "chat_id": chat_id_var,
    "message_id": message_id_var,
    "model": model_var,
    "provider": provider_var,
}


@contextmanager
def log_context(**values: str | None) -> Iterator[None]:
    """Bind log context fields for the duration of a block."""
    unknown = set(values) - set(LOG_CONTEXT_VARS)
    if unknown:
        raise ValueError(f"Unknown log context fields: {sorted(unknown)}")
    tokens = [
        (LOG_CONTEXT_VARS[name], LOG_CONTEXT_VARS[name].set(value))
        for name, value in values.items()
    ]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
