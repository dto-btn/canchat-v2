import contextvars
import logging
import re
import time


from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn.logging import AccessFormatter

from open_webui.observability.logging_config import register_text_formatter
from open_webui.utils.auth import get_request_identity

ACCESS_LOG_NAME: str = "uvicorn.access"
DATE_FORMAT: str = "%Y/%m/%d %H:%M:%S %Z"
ACCESS_LOG_DEFAULT_TEXT: str = "N/A"
ACCESS_LOG_DEFAULT_USER: str = "unauthenticated"
ACCESS_LOG_DEFAULT_DURATION: float = -1.0
ACCESS_LOG_DEFAULT_CONTENT_LENGTH: int = -1
LOG_FORMAT: str = (
    '%(name)s: %(remote_address)s - %(access_request_id)s - %(access_user)s [%(asctime)s] %(host)s "%(http_method)s %(http_path)s HTTP/%(http_version)s" "%(user_agent)s" %(http_status)d %(content_length)dB %(request_duration)fs'
)
# Client-supplied IDs are only used as structured correlation fields if they match.
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

_request_duration_context = contextvars.ContextVar(
    "access_log_process_time", default=ACCESS_LOG_DEFAULT_DURATION
)
_user_context = contextvars.ContextVar(
    "access_log_user", default=ACCESS_LOG_DEFAULT_USER
)
_request_id_context = contextvars.ContextVar(
    "access_log_request_id", default=ACCESS_LOG_DEFAULT_TEXT
)
_context_length_context = contextvars.ContextVar(
    "access_log_content_length", default=ACCESS_LOG_DEFAULT_CONTENT_LENGTH
)
_user_agent_context = contextvars.ContextVar(
    "access_log_user_agent", default=ACCESS_LOG_DEFAULT_TEXT
)
_host_context = contextvars.ContextVar(
    "access_log_host", default=ACCESS_LOG_DEFAULT_TEXT
)


class UvicornAccessFieldsFilter(logging.Filter):
    """
    This filter assigns the args passed to the uvicorn.access logger to named variables
    for simpler access within the formatter.

    Log line and argument order: https://github.com/Kludex/uvicorn/blob/9b1c6c45ed7fe8bd485ddad475f0feff03971af7/uvicorn/protocols/http/h11_impl.py#L473
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Query strings can carry tokens; keep only the path in every sink.
        path = str(record.args[2]).split("?", 1)[0]
        record.args = (*record.args[:2], path, *record.args[3:])

        record.remote_address = record.args[0]
        record.http_method = record.args[1]
        record.http_path = path
        record.http_version = record.args[3]
        record.http_status = record.args[4]

        record.__dict__.update(
            {
                "client.address": str(record.args[0]).rsplit(":", 1)[0],
                "http.request.method": record.args[1],
                "url.path": path,
                "network.protocol.version": record.args[3],
                "http.response.status_code": record.args[4],
            }
        )
        return True


class EndpointFilter(logging.Filter):
    """
    Log filter which removes calls to the health checks from the access logs.

    Log line and argument order: https://github.com/Kludex/uvicorn/blob/9b1c6c45ed7fe8bd485ddad475f0feff03971af7/uvicorn/protocols/http/h11_impl.py#L473
    """

    def filter(self, record: logging.LogRecord) -> bool:

        return not str(record.args[2]).startswith("/health")


class ContextFilter(logging.Filter):
    """
    Logging filter which adds request, response, and identity information into the access logs.
    """

    def filter(self, record: logging.LogRecord) -> bool | logging.LogRecord:
        record.access_user = _user_context.get()
        record.request_duration = _request_duration_context.get()
        record.access_request_id = _request_id_context.get()
        record.content_length = _context_length_context.get()
        record.user_agent = _user_agent_context.get()
        record.host = _host_context.get()

        if record.access_user != ACCESS_LOG_DEFAULT_USER:
            record.user_id = record.access_user
        if REQUEST_ID_PATTERN.match(record.access_request_id):
            record.request_id = record.access_request_id
        if record.request_duration >= 0:
            record.__dict__["duration_ms"] = round(record.request_duration * 1000, 3)
        if record.content_length >= 0:
            record.__dict__["http.response.body.size"] = record.content_length
        return True


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to update the context with the appropriate
    """

    async def dispatch(self, request: Request, call_next):
        start_time = time.perf_counter()

        _request_duration_context.set(ACCESS_LOG_DEFAULT_DURATION)
        _user_context.set(
            get_request_identity(request=request, auth_token=None)
            or ACCESS_LOG_DEFAULT_USER
        )
        _request_id_context.set(
            request.headers.get("X-Request-Id") or ACCESS_LOG_DEFAULT_TEXT
        )
        _user_agent_context.set(
            request.headers.get("User-Agent") or ACCESS_LOG_DEFAULT_TEXT
        )
        _host_context.set(request.headers.get("Host") or ACCESS_LOG_DEFAULT_TEXT)
        _context_length_context.set(ACCESS_LOG_DEFAULT_CONTENT_LENGTH)

        response = await call_next(request)

        _request_duration_context.set(time.perf_counter() - start_time)
        if context_length := response.headers.get("Content-Length"):
            _context_length_context.set(int(context_length))

        return response


def _remove_handlers() -> None:
    """
    Removes the default handlers set up for the uvicorn.access logger, removing the default configurations.
    """
    logger = logging.getLogger(ACCESS_LOG_NAME)

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)


def reconfigure_access_log():
    """
    Routes uvicorn.access records through the root handlers so they share the
    central format, filters and OTLP export. Safe to call more than once.
    """

    _remove_handlers()

    logger = logging.getLogger(ACCESS_LOG_NAME)

    register_text_formatter(
        ACCESS_LOG_NAME, AccessFormatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)
    )

    existing = {type(f) for f in logger.filters}
    for filter in (EndpointFilter(), UvicornAccessFieldsFilter(), ContextFilter()):
        if type(filter) not in existing:
            logger.addFilter(filter)

    logger.propagate = True
    # Must be INFO or lower to be seen.
    logger.setLevel(logging.INFO)
