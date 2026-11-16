"""MCP adapter initialization and cleanup follow the application lifespan."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI


@pytest.mark.parametrize("fail_start", [False, True])
def test_crew_adapters_follow_each_application_lifespan(monkeypatch, fail_start):
    import open_webui.main as main
    from mcp_backend.management import mcp_manager
    from open_webui import scheduler
    from open_webui.grounding import wiki_search_utils
    from open_webui.metrics import service as metrics_service
    from open_webui.retrieval.vector import connector, locks

    events = []
    calling_thread = threading.get_ident()

    class CrewManager:
        def initialize_mcp_adapters(self):
            events.append(("start", threading.get_ident()))
            if fail_start:
                raise RuntimeError("initialization failed")

        def cleanup_mcp_adapters(self):
            events.append(("stop", threading.get_ident()))

    class FastMCPManager:
        async def cleanup(self):
            pass

    class Metrics:
        def __init__(self, meter, definitions):
            pass

        def has_instrument(self, name):
            return False

    async def health_check():
        return True

    async def close():
        pass

    async def initialize():
        return True

    async def cleanup_loop():
        await asyncio.Event().wait()

    lock_manager = SimpleNamespace(
        health_check=health_check, close=close, redis_url="redis://test"
    )
    grounder = SimpleNamespace(initialize=initialize)
    app = FastAPI()
    app.state.config = SimpleNamespace(ENABLE_MCP_API=False)

    monkeypatch.setattr(main.crew_mcp, "crew_mcp_manager", CrewManager())
    monkeypatch.setattr(main, "reconfigure_access_log", lambda: None)
    monkeypatch.setattr(main, "apply_logger_policy", lambda: None)
    monkeypatch.setattr(main, "RESET_CONFIG_ON_START", False)
    monkeypatch.setattr(main, "meter", SimpleNamespace(name="test"))
    monkeypatch.setattr(main, "periodic_usage_pool_cleanup", cleanup_loop)
    monkeypatch.setattr(main, "shutdown_logging", lambda: None)
    monkeypatch.setattr(main, "shutdown_providers", lambda: None)
    monkeypatch.setattr(locks, "get_collection_lock_manager", lambda: lock_manager)
    monkeypatch.setattr(mcp_manager, "get_mcp_manager", FastMCPManager)
    monkeypatch.setattr(metrics_service, "MetricsService", Metrics)
    monkeypatch.setattr(metrics_service, "set_metrics_service", lambda service: None)
    monkeypatch.setattr(wiki_search_utils, "get_wiki_search_grounder", lambda: grounder)
    monkeypatch.setattr(scheduler, "start_scheduler", lambda: None)
    monkeypatch.setattr(scheduler, "stop_scheduler", lambda: None)
    monkeypatch.setattr(connector, "VECTOR_DB_CLIENT", None)

    async def run():
        for _ in range(2):
            async with main.lifespan(app):
                assert events[-1][0] == "start"
            assert events[-1][0] == "stop"

    asyncio.run(run())

    assert [event for event, _ in events] == ["start", "stop", "start", "stop"]
    assert all(thread != calling_thread for _, thread in events)
