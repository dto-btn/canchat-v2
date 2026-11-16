"""Synchronous CrewAI tools backed by a filtered MCP stdio session."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from contextvars import Context
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from open_webui.env import MCP_TOOL_CALL_TIMEOUT
from open_webui.observability.mcp import (
    FilteredStderrPipe,
    observe_tool,
    register_builtin_server,
)

if TYPE_CHECKING:
    from crewai.tools import BaseTool

log = logging.getLogger(__name__)


class MCPServerAdapter:
    """Expose CrewAI's normal MCP tools while owning the stdio boundary."""

    def __init__(
        self,
        serverparams: StdioServerParameters,
        server_name: str,
        *,
        connect_timeout: float = 30,
        call_timeout: float | None = MCP_TOOL_CALL_TIMEOUT,
    ) -> None:
        from crewai_tools.adapters.mcp_adapter import CrewAIToolAdapter

        self._params = serverparams
        self._server = register_builtin_server(server_name)
        self._call_timeout = (
            timedelta(seconds=call_timeout) if call_timeout is not None else None
        )
        self._stderr = FilteredStderrPipe()
        self._loop = asyncio.new_event_loop()
        self._ready: Future[list[Tool]] = Future()
        self._task: asyncio.Task | None = None
        self._session: ClientSession | None = None
        self._close_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run_loop, name="crew-mcp", daemon=True
        )
        try:
            self._thread.start()
            tools = self._ready.result(timeout=connect_timeout)
            adapter = CrewAIToolAdapter()
            self._tools = [adapter.adapt(self._call(tool.name), tool) for tool in tools]
        except BaseException:
            self.close()
            raise

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._task = self._loop.create_task(self._run_session(), context=Context())
        try:
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            if not self._ready.done():
                self._ready.set_exception(error)
            else:
                log.error("Crew MCP session failed: %s", type(error).__name__)
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._loop.close()

    async def _run_session(self) -> None:
        try:
            async with stdio_client(
                self._params, errlog=self._stderr.stream
            ) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    self._session = session
                    self._ready.set_result(tools)
                    await asyncio.Event().wait()
        finally:
            self._session = None

    def _call(
        self, tool_name: str
    ) -> Callable[[dict[str, Any] | None], CallToolResult]:
        def call(arguments: dict[str, Any] | None) -> CallToolResult:
            with observe_tool(self._server, tool_name) as observation:
                session = self._session
                if session is None or not self._loop.is_running():
                    raise RuntimeError("Crew MCP session is not available")
                coroutine = session.call_tool(
                    tool_name, arguments, read_timeout_seconds=self._call_timeout
                )
                try:
                    future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
                except BaseException:
                    coroutine.close()
                    raise
                result = future.result()
                if result.isError:
                    observation.error()
                return result

        return call

    def __enter__(self) -> list["BaseTool"]:
        return self._tools

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def close(self) -> None:
        with self._close_lock:
            if self._thread.is_alive():
                if not self._loop.is_closed():
                    try:
                        self._loop.call_soon_threadsafe(self._cancel)
                    except RuntimeError:
                        if not self._loop.is_closed():
                            raise
                self._thread.join(timeout=5)
            if self._thread.is_alive():
                log.warning("Crew MCP session did not stop after cancellation")
            elif not self._loop.is_closed():
                self._loop.close()
            self._stderr.close()

    def _cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
