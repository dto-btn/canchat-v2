"""MCP tool telemetry and the subprocess stderr boundary (OBS-29)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from concurrent.futures import CancelledError as FutureCancelledError
from contextlib import contextmanager
from typing import Generator

import httpx
from mcp.shared.exceptions import McpError
from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode

from open_webui.metrics.service import get_metrics_service
from open_webui.observability.chat import mark_span_error
from open_webui.observability.redaction import MESSAGE_OMITTED

log = logging.getLogger(__name__)

TRACER_NAME = "open_webui.mcp"
TOOL_DURATION = "canchat.mcp.tool.duration"
MAX_BUILTIN_SERVERS = 100
_monotonic = time.monotonic

_SHAREPOINT_SERVER = re.compile(r"^([a-z]{1,40})_sharepoint_server$")
_SHAREPOINT_TOOLS = frozenset(
    {
        "list_folder_contents",
        "search_documents_fast",
        "get_document_by_id",
        "get_sharepoint_document_content",
        "get_all_documents_comprehensive",
        "analyze_all_documents_for_content",
        "check_sharepoint_permissions",
    }
)
_STATIC_BUILTIN_TOOLS: dict[str, frozenset[str]] = {
    "time_server": frozenset({"get_current_time"}),
    "news_server": frozenset({"get_top_headlines"}),
}
_builtin_servers = set(_STATIC_BUILTIN_TOOLS)
_builtin_servers_lock = threading.Lock()


def register_builtin_server(name: str) -> str:
    """Register a trusted built-in name, or return the bounded overflow label."""
    if name not in _STATIC_BUILTIN_TOOLS and not _SHAREPOINT_SERVER.fullmatch(name):
        log.warning("MCP built-in server name is not eligible for telemetry labels")
        return "other"
    with _builtin_servers_lock:
        if name not in _builtin_servers:
            if len(_builtin_servers) >= MAX_BUILTIN_SERVERS:
                log.warning("MCP built-in server label limit reached")
                return "other"
            _builtin_servers.add(name)
    return name


def tool_labels(server: str, tool: str) -> tuple[str, str]:
    """Use only registered built-ins and their known tool names as labels."""
    with _builtin_servers_lock:
        registered = server in _builtin_servers
    if not registered:
        return ("other" if server == "other" else "external", "other")
    if server in _STATIC_BUILTIN_TOOLS:
        return server, tool if tool in _STATIC_BUILTIN_TOOLS[server] else "other"
    match = _SHAREPOINT_SERVER.fullmatch(server)
    if match:
        prefix = f"{match.group(1)}_"
        if tool.startswith(prefix) and tool[len(prefix) :] in _SHAREPOINT_TOOLS:
            return server, tool
    return server, "other"


def _tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def _is_timeout(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return True
    # The MCP session reports its read timeout as a JSON-RPC 408 error.
    return (
        isinstance(error, McpError) and error.error.code == httpx.codes.REQUEST_TIMEOUT
    )


def _record_duration(duration: float, server: str, tool: str, outcome: str) -> None:
    metrics = get_metrics_service()
    if metrics is None:
        return
    try:
        if metrics.has_instrument(TOOL_DURATION):
            metrics.record(
                TOOL_DURATION,
                duration,
                {"mcp.server": server, "mcp.tool": tool, "outcome": outcome},
            )
    except Exception as error:
        if getattr(metrics, "strict", False):
            raise
        log.warning("Failed to record MCP tool duration: %s", type(error).__name__)


class ToolObservation:
    def __init__(self, span: Span):
        self.span = span
        self.outcome = "success"

    def error(self) -> None:
        """Mark an in-band MCP error without recording its content."""
        self.outcome = "error"
        self.span.set_attribute("error.type", "MCPToolError")
        self.span.set_status(StatusCode.ERROR)


@contextmanager
def observe_tool(server: str, tool: str) -> Generator[ToolObservation, None, None]:
    """Trace and time one call without recording its arguments or result."""
    server, tool = tool_labels(server, tool)
    started = _monotonic()
    with _tracer().start_as_current_span(
        "mcp.tool.call", record_exception=False, set_status_on_exception=False
    ) as span:
        span.set_attribute("canchat.mcp.server", server)
        span.set_attribute("canchat.mcp.tool", tool)
        observation = ToolObservation(span)
        try:
            yield observation
        except (asyncio.CancelledError, FutureCancelledError):
            observation.outcome = "cancelled"
            raise
        except BaseException as error:
            observation.outcome = "timeout" if _is_timeout(error) else "error"
            mark_span_error(span, error)
            raise
        finally:
            span.set_attribute("canchat.outcome", observation.outcome)
            _record_duration(_monotonic() - started, server, tool, observation.outcome)


class FilteredStderrPipe:
    """Drain a child process's stderr without logging its content.

    Credential redaction cannot recognize arbitrary prompts or results, so the
    untrusted text is discarded and only one DEBUG size summary is logged when
    the stream closes.
    """

    def __init__(self) -> None:
        self._chunks = 0
        self._bytes = 0
        read_fd, write_fd = os.pipe()
        try:
            self.stream = os.fdopen(write_fd, "w", encoding="utf-8")
        except BaseException:
            os.close(read_fd)
            os.close(write_fd)
            raise
        self._read_fd = read_fd
        self._thread = threading.Thread(
            target=self._drain, name="mcp-stderr", daemon=True
        )
        try:
            self._thread.start()
        except BaseException:
            self.stream.close()
            os.close(read_fd)
            raise

    def _drain(self) -> None:
        try:
            with os.fdopen(self._read_fd, "rb", buffering=0) as source:
                while chunk := source.read(8192):
                    self._forward(chunk)
        except OSError as error:
            log.warning("MCP stderr reader failed: %s", type(error).__name__)
        if self._bytes:
            log.debug(
                "MCP subprocess stderr: %s (%d chunks, %d bytes)",
                MESSAGE_OMITTED,
                self._chunks,
                self._bytes,
            )

    def _forward(self, chunk: bytes) -> None:
        self._chunks += 1
        self._bytes += len(chunk)

    def close(self) -> None:
        if not self.stream.closed:
            self.stream.close()
        self._thread.join(timeout=3)
        if self._thread.is_alive():
            log.warning("MCP stderr reader did not stop after the subprocess closed")
