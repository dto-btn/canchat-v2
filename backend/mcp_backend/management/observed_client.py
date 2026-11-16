"""FastMCP client without implicit MCP-metadata trace propagation."""

from datetime import timedelta
from typing import Any

from fastmcp.client import Client
from fastmcp.client.progress import ProgressHandler
from fastmcp.utilities.timeout import normalize_timeout_to_timedelta
from mcp.types import CallToolResult


class ObservedMCPClient(Client):
    """FastMCP client whose tool calls carry no implicit trace metadata.

    ``call_tool_mcp`` is copied from ``fastmcp==3.2.0``
    (``fastmcp.client.mixins.tools.ToolsMixin.call_tool_mcp``) minus its span
    and ``inject_trace_context`` call, and uses the private
    ``_await_with_session_monitoring``. Re-check it whenever FastMCP is upgraded;
    ``test_observed_client_matches_pinned_fastmcp`` fails if the upstream
    signature or private helper changes.
    """

    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        progress_handler: ProgressHandler | None = None,
        timeout: timedelta | float | int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        # FastMCP's implementation adds trace context to MCP metadata and
        # creates a span named with the unrestricted tool name. The manager
        # supplies its own bounded span instead.
        return await self._await_with_session_monitoring(
            self.session.call_tool(
                name=name,
                arguments=arguments,
                read_timeout_seconds=normalize_timeout_to_timedelta(timeout),
                progress_callback=progress_handler or self._progress_handler,
                meta=meta,
            )
        )
