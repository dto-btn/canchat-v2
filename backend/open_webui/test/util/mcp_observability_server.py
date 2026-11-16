"""MCP subprocess used by the OBS-29 integration tests."""

import asyncio
import json
import os
import sys

from mcp.server.fastmcp import Context, FastMCP

server = FastMCP("observability-test")


@server.tool()
async def get_current_time(
    payload: str, reply: str, ctx: Context, mode: str = "success"
) -> str:
    sys.stderr.write(f"MCP stderr: {payload[: len(payload) // 2]}")
    sys.stderr.flush()
    sys.stderr.write(f"{payload[len(payload) // 2 :]} {reply}\n")
    sys.stderr.flush()
    if mode == "error":
        raise ValueError(f"{payload} {reply}")
    if mode == "crash":
        os._exit(17)
    if mode == "wait":
        await asyncio.sleep(30)
    meta = ctx.request_context.meta
    fields = meta.model_dump(exclude_none=True) if meta else {}
    return json.dumps(
        {
            "payload": payload,
            "reply": reply,
            "trace_context_present": any(
                name in fields for name in ("traceparent", "tracestate", "baggage")
            ),
        }
    )


if __name__ == "__main__":
    if "--http" in sys.argv:
        sys.stderr.write(os.environ["OBS29_TEST_STDERR"])
        sys.stderr.flush()
    else:
        server.run(transport="stdio")
