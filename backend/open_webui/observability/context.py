"""Context variables shared by request and operation instrumentation."""

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
operation_id_var: ContextVar[str | None] = ContextVar("operation_id", default=None)
