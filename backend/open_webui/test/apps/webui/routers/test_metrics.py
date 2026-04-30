"""
Integration tests for MCP token consumption logging — message_metrics model
and /api/v1/metrics/* router endpoints.

These tests use a real Postgres database (spun up in Docker by AbstractPostgresTest)
and cover:
  • MessageMetrics.insert_new_metrics  — mcp_tool column stored correctly
  • MessageMetrics.get_mcp_processes   — distinct, non-null tools returned
  • MessageMetrics._apply_mcp_filter   — all three sentinel values behave correctly
  • GET /api/v1/metrics/mcp-processes  — endpoint returns the logged tool list
  • GET /api/v1/metrics/tokens         — mcp_tool query-param filters correctly
  • GET /api/v1/metrics/daily/tokens   — mcp_tool filter respected
"""

from types import SimpleNamespace

from sqlalchemy import text

from open_webui.test.util.abstract_integration_test import AbstractPostgresTest
from open_webui.test.util.mock_user import mock_webui_user

# ── convenience factory ────────────────────────────────────────────────────────

_ADMIN_KWARGS = {"role": "admin", "domain": "gc.ca"}
_USAGE_100 = {"prompt_tokens": 60, "completion_tokens": 40, "total_tokens": 100}
_USAGE_200 = {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200}
_USAGE_300 = {"prompt_tokens": 180, "completion_tokens": 120, "total_tokens": 300}


def _make_user(user_id="test-user-1", domain="gc.ca"):
    """Return a SimpleNamespace user accepted by insert_new_metrics."""
    return SimpleNamespace(id=user_id, domain=domain)


class TestMcpTokenMetrics(AbstractPostgresTest):
    BASE_PATH = "/api/v1/metrics"

    # ── class setup ───────────────────────────────────────────────────────────

    @classmethod
    def setup_class(cls):
        super().setup_class()
        from open_webui.models.message_metrics import MessageMetrics

        cls.metrics = MessageMetrics

    # ── per-test setup / teardown ─────────────────────────────────────────────

    def setup_method(self):
        super().setup_method()

    def teardown_method(self):
        """Extend parent teardown to also clear the message_metrics table."""
        from open_webui.internal.db import Session

        Session.commit()

        # Tables cleaned up by the parent class
        parent_tables = [
            "auth",
            "chat",
            "chatidtag",
            "document",
            "model",
            "prompt",
            "tag",
            '"user"',
        ]
        for table in parent_tables:
            Session.execute(text(f"TRUNCATE TABLE {table}"))

        # Our new table
        Session.execute(text("TRUNCATE TABLE message_metrics"))
        Session.commit()

    # ── helper ────────────────────────────────────────────────────────────────

    def _insert(self, mcp_tool=None, usage=None, user_id="u1", domain="gc.ca"):
        if usage is None:
            usage = _USAGE_100
        self.metrics.insert_new_metrics(
            user=_make_user(user_id, domain),
            model="azure/gpt-4o",
            usage=usage,
            chat_id="chat-abc",
            mcp_tool=mcp_tool,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Model-level tests — insert_new_metrics
    # ═══════════════════════════════════════════════════════════════════════════

    def test_insert_with_mcp_tool_stores_value(self):
        """mcp_tool is persisted verbatim in the database row."""
        self._insert(mcp_tool="news_server")
        processes = self.metrics.get_mcp_processes()
        assert "news_server" in processes

    def test_insert_without_mcp_tool_stores_null(self):
        """Rows inserted without mcp_tool must NOT appear in get_mcp_processes."""
        self._insert(mcp_tool=None)
        processes = self.metrics.get_mcp_processes()
        assert processes == []

    def test_insert_records_token_counts(self):
        """Token totals are stored and retrievable via get_message_tokens_sum."""
        self._insert(usage=_USAGE_200)
        total = self.metrics.get_message_tokens_sum()
        assert total == 200

    # ═══════════════════════════════════════════════════════════════════════════
    # Model-level tests — get_mcp_processes
    # ═══════════════════════════════════════════════════════════════════════════

    def test_get_mcp_processes_returns_distinct_tools(self):
        """Inserting duplicate tool names should return only one entry."""
        self._insert(mcp_tool="news_server")
        self._insert(mcp_tool="news_server")
        self._insert(mcp_tool="mpo_sharepoint_server")
        processes = self.metrics.get_mcp_processes()
        assert sorted(processes) == ["mpo_sharepoint_server", "news_server"]

    def test_get_mcp_processes_excludes_null_rows(self):
        """Non-MCP rows (mcp_tool=NULL) must never appear in the process list."""
        self._insert(mcp_tool=None)
        self._insert(mcp_tool="news_server")
        processes = self.metrics.get_mcp_processes()
        assert None not in processes
        assert "" not in processes
        assert "news_server" in processes

    def test_get_mcp_processes_returns_empty_when_no_mcp_rows(self):
        """An empty or non-MCP database should return an empty list."""
        self._insert(mcp_tool=None)
        assert self.metrics.get_mcp_processes() == []

    # ═══════════════════════════════════════════════════════════════════════════
    # Model-level tests — _apply_mcp_filter / get_message_tokens_sum
    # ═══════════════════════════════════════════════════════════════════════════

    def test_mcp_all_sentinel_returns_only_mcp_rows(self):
        """__mcp_all__ should sum only rows where mcp_tool IS NOT NULL."""
        self._insert(mcp_tool="news_server", usage=_USAGE_100)  # 100
        self._insert(mcp_tool="mpo_sharepoint_server", usage=_USAGE_200)  # 200
        self._insert(mcp_tool=None, usage=_USAGE_300)  # 300 — non-MCP, excluded

        total = self.metrics.get_message_tokens_sum(mcp_tool="__mcp_all__")
        assert total == 300  # 100 + 200 only

    def test_non_mcp_sentinel_returns_only_non_mcp_rows(self):
        """__non_mcp__ should sum only rows where mcp_tool IS NULL."""
        self._insert(mcp_tool=None, usage=_USAGE_300)  # 300
        self._insert(mcp_tool="news_server", usage=_USAGE_100)  # 100 — excluded

        total = self.metrics.get_message_tokens_sum(mcp_tool="__non_mcp__")
        assert total == 300

    def test_specific_tool_filter_returns_only_that_tool(self):
        """A literal tool name should sum tokens for that tool only."""
        self._insert(mcp_tool="news_server", usage=_USAGE_100)  # 100
        self._insert(mcp_tool="mpo_sharepoint_server", usage=_USAGE_200)  # 200
        self._insert(mcp_tool=None, usage=_USAGE_300)  # 300

        total = self.metrics.get_message_tokens_sum(mcp_tool="news_server")
        assert total == 100

    def test_no_filter_returns_all_rows(self):
        """No mcp_tool filter (None) should sum every row regardless of MCP status."""
        self._insert(mcp_tool="news_server", usage=_USAGE_100)
        self._insert(mcp_tool=None, usage=_USAGE_200)

        total = self.metrics.get_message_tokens_sum()
        assert total == 300  # 100 + 200

    def test_mcp_all_sentinel_with_empty_table_returns_zero(self):
        """__mcp_all__ on an empty table must return 0, not None or an error."""
        total = self.metrics.get_message_tokens_sum(mcp_tool="__mcp_all__")
        assert total == 0

    def test_non_mcp_sentinel_with_empty_table_returns_zero(self):
        """__non_mcp__ on an empty table must return 0, not None or an error."""
        total = self.metrics.get_message_tokens_sum(mcp_tool="__non_mcp__")
        assert total == 0

    def test_specific_tool_with_no_matching_rows_returns_zero(self):
        """Filtering by a tool name with no rows should return 0, not None."""
        self._insert(mcp_tool="other_server", usage=_USAGE_100)
        total = self.metrics.get_message_tokens_sum(mcp_tool="news_server")
        assert total == 0

    # ═══════════════════════════════════════════════════════════════════════════
    # Router endpoint tests — GET /api/v1/metrics/mcp-processes
    # ═══════════════════════════════════════════════════════════════════════════

    def test_endpoint_mcp_processes_requires_auth(self):
        """Unauthenticated request must be rejected with a 4xx status."""
        response = self.fast_api_client.get(self.create_url("/mcp-processes"))
        assert response.status_code in (401, 403, 422)

    def test_endpoint_mcp_processes_empty_db(self):
        """Empty database should return an empty list, not an error."""
        with mock_webui_user(**_ADMIN_KWARGS):
            response = self.fast_api_client.get(self.create_url("/mcp-processes"))
        assert response.status_code == 200
        data = response.json()
        assert "mcp_processes" in data
        assert data["mcp_processes"] == []

    def test_endpoint_mcp_processes_returns_logged_tools(self):
        """Endpoint must return each distinct tool name that has been logged."""
        self._insert(mcp_tool="news_server")
        self._insert(mcp_tool="mpo_sharepoint_server")
        self._insert(mcp_tool=None)  # should NOT appear

        with mock_webui_user(**_ADMIN_KWARGS):
            response = self.fast_api_client.get(self.create_url("/mcp-processes"))
        assert response.status_code == 200
        processes = response.json()["mcp_processes"]
        assert sorted(processes) == ["mpo_sharepoint_server", "news_server"]

    def test_endpoint_mcp_processes_no_duplicates(self):
        """Each tool name should appear exactly once even with many logged calls."""
        for _ in range(5):
            self._insert(mcp_tool="news_server")
        with mock_webui_user(**_ADMIN_KWARGS):
            response = self.fast_api_client.get(self.create_url("/mcp-processes"))
        assert response.status_code == 200
        assert response.json()["mcp_processes"].count("news_server") == 1

    # ═══════════════════════════════════════════════════════════════════════════
    # Router endpoint tests — GET /api/v1/metrics/tokens  (mcp_tool param)
    # ═══════════════════════════════════════════════════════════════════════════

    def test_endpoint_tokens_no_filter_returns_all(self):
        """Without mcp_tool param the endpoint returns total tokens for all rows."""
        self._insert(mcp_tool="news_server", usage=_USAGE_100)
        self._insert(mcp_tool=None, usage=_USAGE_200)

        with mock_webui_user(**_ADMIN_KWARGS):
            response = self.fast_api_client.get(self.create_url("/tokens"))
        assert response.status_code == 200
        assert response.json()["total_tokens"] == 300

    def test_endpoint_tokens_mcp_all_sentinel(self):
        """mcp_tool=__mcp_all__ must sum only MCP rows."""
        self._insert(mcp_tool="news_server", usage=_USAGE_100)
        self._insert(mcp_tool="mpo_sharepoint_server", usage=_USAGE_200)
        self._insert(mcp_tool=None, usage=_USAGE_300)

        with mock_webui_user(**_ADMIN_KWARGS):
            response = self.fast_api_client.get(
                self.create_url("/tokens", query_params={"mcp_tool": "__mcp_all__"})
            )
        assert response.status_code == 200
        # 100 + 200 MCP; 300 non-MCP excluded
        assert response.json()["total_tokens"] == 300

    def test_endpoint_tokens_non_mcp_sentinel(self):
        """mcp_tool=__non_mcp__ must sum only non-MCP rows."""
        self._insert(mcp_tool="news_server", usage=_USAGE_100)
        self._insert(mcp_tool=None, usage=_USAGE_300)

        with mock_webui_user(**_ADMIN_KWARGS):
            response = self.fast_api_client.get(
                self.create_url("/tokens", query_params={"mcp_tool": "__non_mcp__"})
            )
        assert response.status_code == 200
        assert response.json()["total_tokens"] == 300

    def test_endpoint_tokens_specific_tool(self):
        """Filtering by a specific tool name returns only that tool's tokens."""
        self._insert(mcp_tool="news_server", usage=_USAGE_100)
        self._insert(mcp_tool="mpo_sharepoint_server", usage=_USAGE_200)
        self._insert(mcp_tool=None, usage=_USAGE_300)

        with mock_webui_user(**_ADMIN_KWARGS):
            response = self.fast_api_client.get(
                self.create_url(
                    "/tokens", query_params={"mcp_tool": "mpo_sharepoint_server"}
                )
            )
        assert response.status_code == 200
        assert response.json()["total_tokens"] == 200

    def test_endpoint_tokens_unknown_tool_returns_zero(self):
        """A tool name with no matching rows returns 0, not 404 or null."""
        self._insert(mcp_tool="news_server", usage=_USAGE_100)

        with mock_webui_user(**_ADMIN_KWARGS):
            response = self.fast_api_client.get(
                self.create_url(
                    "/tokens", query_params={"mcp_tool": "nonexistent_server"}
                )
            )
        assert response.status_code == 200
        assert response.json()["total_tokens"] == 0

    # ═══════════════════════════════════════════════════════════════════════════
    # Router endpoint tests — GET /api/v1/metrics/daily/tokens
    # ═══════════════════════════════════════════════════════════════════════════

    def test_endpoint_daily_tokens_mcp_all_sentinel(self):
        """Daily tokens endpoint respects __mcp_all__ sentinel."""
        self._insert(mcp_tool="news_server", usage=_USAGE_100)
        self._insert(mcp_tool=None, usage=_USAGE_200)

        with mock_webui_user(**_ADMIN_KWARGS):
            response = self.fast_api_client.get(
                self.create_url(
                    "/daily/tokens", query_params={"mcp_tool": "__mcp_all__"}
                )
            )
        assert response.status_code == 200
        # Only MCP rows count; the non-MCP 200-token row is excluded.
        # The daily query also filters by today's UTC window, so this checks
        # the filter works (value ≤ 100, not equal to 300).
        assert response.json()["total_daily_tokens"] <= 100

    def test_endpoint_daily_tokens_non_mcp_sentinel(self):
        """Daily tokens endpoint respects __non_mcp__ sentinel."""
        self._insert(mcp_tool="news_server", usage=_USAGE_100)
        self._insert(mcp_tool=None, usage=_USAGE_200)

        with mock_webui_user(**_ADMIN_KWARGS):
            response = self.fast_api_client.get(
                self.create_url(
                    "/daily/tokens", query_params={"mcp_tool": "__non_mcp__"}
                )
            )
        assert response.status_code == 200
        assert response.json()["total_daily_tokens"] <= 200
