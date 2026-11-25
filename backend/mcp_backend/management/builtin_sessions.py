"""Own the MCP sessions for built-in servers within one backend worker."""

import asyncio
import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable, Generator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool


@dataclass(frozen=True)
class BuiltinTool:
    definition: Tool
    call: Callable[[dict | None], CallToolResult]


class _Session:
    def __init__(self, parameters: StdioServerParameters):
        self.parameters = parameters
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.task: asyncio.Task | None = None
        self.client: ClientSession | None = None
        self.definitions: list[Tool] = []
        self.error: Exception | None = None

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)

        async def serve():
            async with stdio_client(self.parameters) as (read, write):
                async with ClientSession(read, write) as client:
                    self.client = client
                    await client.initialize()
                    self.definitions = (await client.list_tools()).tools
                    self.ready.set()
                    await asyncio.Event().wait()

        self.task = self.loop.create_task(serve())
        try:
            self.loop.run_until_complete(self.task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.error = exc
        finally:
            self.ready.set()
            self.loop.close()

    def start(self) -> list[BuiltinTool]:
        self.thread.start()
        if not self.ready.wait(timeout=30):
            self.close()
            raise TimeoutError("Timed out connecting to built-in MCP server")
        if self.error:
            self.thread.join()
            raise RuntimeError(
                "Failed to connect to built-in MCP server"
            ) from self.error
        return [
            BuiltinTool(definition=definition, call=partial(self.call, definition.name))
            for definition in self.definitions
        ]

    def call(self, name: str, arguments: dict | None) -> CallToolResult:
        if not self.client or not self.thread.is_alive():
            raise RuntimeError("Built-in MCP session is not running")
        future = asyncio.run_coroutine_threadsafe(
            self.client.call_tool(name, arguments), self.loop
        )
        return future.result()

    def close(self) -> None:
        if self.task and self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.task.cancel)
        self.thread.join(timeout=30)
        if self.thread.is_alive():
            raise TimeoutError("Timed out stopping built-in MCP server")


class BuiltinServerBusy(RuntimeError):
    """A built-in server is in use and cannot be restarted."""


class BuiltinSessions:
    """Manage persistent time/news sessions and request-scoped SharePoint sessions."""

    def __init__(self):
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._sessions: dict[str, tuple[_Session, list[BuiltinTool]]] = {}
        self._active: dict[str, int] = {}
        self._sharepoint_tools: dict[str, list[Tool]] = {}
        self._failed_close: dict[str, list[_Session]] = {}
        self._failed_start: set[str] = set()
        self._closing = False

    def _parameters(self, name: str, user_token: str | None) -> StdioServerParameters:
        servers = Path(__file__).parent.parent / "servers"
        common_env = {
            key: value for key, value in os.environ.items() if key != "USER_JWT_TOKEN"
        }
        if name == "time_server":
            return StdioServerParameters(
                command=sys.executable,
                args=[str(servers / "fastmcp_time_server.py")],
                env=common_env,
            )
        if name == "news_server":
            return StdioServerParameters(
                command=sys.executable,
                args=[str(servers / "fastmcp_news_server.py")],
                env=common_env,
            )
        if name.endswith("_sharepoint_server"):
            department = name.removesuffix("_sharepoint_server").upper()
            configured = {
                item.strip().upper()
                for item in os.getenv("SHAREPOINT_DEPARTMENTS", "").split(",")
                if item.strip()
            }
            if (
                department not in configured
                or not department.isascii()
                or not department.isalpha()
            ):
                raise ValueError(f"Unknown built-in MCP server: {name}")
            env = {
                key: value
                for key, value in os.environ.items()
                if key.startswith("SHP_") or key.startswith(f"{department}_SHP_")
            }
            if user_token:
                env["USER_JWT_TOKEN"] = user_token
            return StdioServerParameters(
                command=sys.executable,
                args=[
                    str(servers / "generic_sharepoint_server_multi_dept.py"),
                    department,
                ],
                env=env,
            )
        raise ValueError(f"Unknown built-in MCP server: {name}")

    def _open(
        self, name: str, user_token: str | None
    ) -> tuple[_Session, list[BuiltinTool]]:
        session = None
        try:
            session = _Session(self._parameters(name, user_token))
            tools = session.start()
        except Exception:
            self._failed_start.add(name)
            if session and session.thread.is_alive():
                self._failed_close.setdefault(name, []).append(session)
            raise
        self._failed_start.discard(name)
        return session, tools

    @contextmanager
    def session(
        self, name: str, user_token: str | None = None
    ) -> Generator[list[BuiltinTool], None, None]:
        temporary = name.endswith("_sharepoint_server")
        with self._lock:
            if self._closing:
                raise RuntimeError("Built-in MCP sessions are shutting down")
            if self._failed_close.get(name):
                raise BuiltinServerBusy(f"Built-in MCP server {name} requires cleanup")
            if temporary:
                adapter, tools = self._open(name, user_token)
            else:
                if name not in self._sessions:
                    self._sessions[name] = self._open(name, None)
                adapter, tools = self._sessions[name]
                if not adapter.thread.is_alive():
                    raise RuntimeError(
                        f"Built-in MCP server {name} has stopped unexpectedly"
                    )
            self._active[name] = self._active.get(name, 0) + 1
        try:
            yield tools
        finally:
            try:
                if temporary:
                    adapter.close()
            except Exception:
                with self._lock:
                    self._failed_close.setdefault(name, []).append(adapter)
                raise
            finally:
                with self._condition:
                    self._active[name] -= 1
                    if not self._active[name]:
                        del self._active[name]
                    self._condition.notify_all()

    def status(self, name: str) -> str:
        with self._lock:
            if name in self._failed_close or name in self._failed_start:
                return "stopped"
            if name in self._active:
                return "running"
            session = self._sessions.get(name)
            if session and session[0].thread.is_alive():
                return "running"
            return "stopped" if session else "idle"

    def describe(self, name: str) -> list[Tool]:
        with self._lock:
            if name in self._sharepoint_tools:
                return list(self._sharepoint_tools[name])
            with self.session(name) as tools:
                definitions = [tool.definition for tool in tools]
            if name.endswith("_sharepoint_server"):
                self._sharepoint_tools[name] = definitions
            return list(definitions)

    def restart(self, name: str) -> None:
        with self._lock:
            self.stop(name)
            self._sharepoint_tools.pop(name, None)
            if not name.endswith("_sharepoint_server"):
                self._sessions[name] = self._open(name, None)
            else:
                self._failed_start.discard(name)

    def stop(self, name: str) -> None:
        with self._lock:
            if self._closing:
                raise BuiltinServerBusy("Built-in MCP sessions are shutting down")
            if self._active.get(name, 0):
                raise BuiltinServerBusy(f"Built-in MCP server {name} is in use")
            if self._failed_close.get(name):
                raise BuiltinServerBusy(f"Built-in MCP server {name} requires cleanup")
            self._parameters(name, None)
            session = self._sessions.get(name)
            if session:
                session[0].close()
                del self._sessions[name]

    def cleanup(self) -> None:
        with self._condition:
            self._closing = True
            try:
                if not self._condition.wait_for(lambda: not self._active, timeout=30):
                    raise BuiltinServerBusy("Built-in MCP sessions are still in use")
                errors = []
                for name, (adapter, _) in list(self._sessions.items()):
                    try:
                        adapter.close()
                    except Exception as exc:
                        exc.add_note(f"Failed to close built-in MCP server {name}")
                        errors.append(exc)
                    else:
                        del self._sessions[name]
                for name, sessions in list(self._failed_close.items()):
                    for session in list(sessions):
                        try:
                            session.close()
                        except Exception as exc:
                            exc.add_note(f"Failed to close built-in MCP server {name}")
                            errors.append(exc)
                        else:
                            sessions.remove(session)
                    if not sessions:
                        del self._failed_close[name]
                if errors:
                    raise ExceptionGroup(
                        "Failed to clean up built-in MCP sessions", errors
                    )
                self._sharepoint_tools.clear()
                self._failed_start.clear()
            finally:
                self._closing = False


builtin_sessions = BuiltinSessions()
