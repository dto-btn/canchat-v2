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

and the Model Analysis model list:
  • MessageMetrics.get_prompt_models     — direct prompts only (no tool use); prefix filter
  • GET /api/v1/metrics/models           — active = in the chat model dropdown (id or
                                           name, any case); archived = every other
                                           stored name, one entry each
  • GET /api/v1/metrics/models/prompts   — active counts case variants; archived counts
                                           exactly that stored name
  • GET /api/v1/metrics/range/users      — growth counts the same names in both periods
  • _match_live_name / _model_key / _is_hidden — unit tests, no database
"""

from types import SimpleNamespace

import pytest

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


# Live (chat dropdown) models used by the Model Analysis tests. Prod-like: the
# chat dropdown lists LiteLLM names ("GPT 4o"), while metrics often store the
# provider's name ("gpt-4o-2024-11-20").
_LIVE_MODELS = {
    "GPT 4o": {"id": "GPT 4o", "name": "GPT 4o"},
    "Gemini Flash 2.5": {"id": "Gemini Flash 2.5", "name": "Gemini Flash 2.5"},
    "custom-hr": {"id": "custom-hr", "name": "HR Assistant"},  # custom display name
    "hidden-model": {
        "id": "hidden-model",
        "name": "Hidden",
        "info": {"meta": {"hidden": True}},
    },
    "arena-model": {"id": "arena-model", "name": "Arena", "arena": True},
}


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

    # ═══════════════════════════════════════════════════════════════════════════
    # Model Analysis — model list, per-model prompt counts, range growth
    # ═══════════════════════════════════════════════════════════════════════════

    def _insert_model(self, model, mcp_tool=None):
        self.metrics.insert_new_metrics(
            user=_make_user(),
            model=model,
            usage=_USAGE_100,
            chat_id="chat-abc",
            mcp_tool=mcp_tool,
        )

    def test_get_prompt_models_excludes_tool_use(self):
        """Model analytics covers direct prompts only; CrewAI rows are left out."""
        self._insert_model("gpt-4o-2024-08-06")
        self._insert_model("command-r-plus")
        self._insert_model("azure/gpt-4o", mcp_tool="news_server")
        self._insert_model("azure/gpt-4o")  # CrewAI without an MCP process
        self._insert_model("crewai")

        assert sorted(self.metrics.get_prompt_models()) == [
            "command-r-plus",
            "gpt-4o-2024-08-06",
        ]

    def test_get_prompt_models_prefixes_narrow_and_match_literally(self):
        """prefixes return only names starting with one of them; "_" is not a wildcard."""
        self._insert_model("gpt-4o-2024-08-06")
        self._insert_model("gpt-4o-mini")
        self._insert_model("gpt-4.1")
        self._insert_model("my_model-0613")
        self._insert_model(
            "myxmodel-0613"
        )  # would match "my_model" if "_" were a wildcard
        self._insert_model("azure/gpt-4o", mcp_tool="news_server")

        assert sorted(self.metrics.get_prompt_models(prefixes=["gpt-4o"])) == [
            "gpt-4o-2024-08-06",
            "gpt-4o-mini",
        ]
        assert self.metrics.get_prompt_models(prefixes=["my_model"]) == [
            "my_model-0613"
        ]
        # Case is ignored, and several prefixes are combined
        assert sorted(self.metrics.get_prompt_models(prefixes=["GPT-4.1", "my_"])) == [
            "gpt-4.1",
            "my_model-0613",
        ]

    def _set_live_models(self, models):
        """Swap app.state.MODELS for a test; returns the previous value."""
        app = self.fast_api_client.app
        previous = app.state.MODELS
        app.state.MODELS = models
        return previous

    def _get(self, path, query_params=None):
        """GET a metrics endpoint as admin, with the test live models."""
        previous = self._set_live_models(_LIVE_MODELS)
        try:
            with mock_webui_user(**_ADMIN_KWARGS):
                response = self.fast_api_client.get(
                    self.create_url(path, query_params=query_params)
                )
        finally:
            self.fast_api_client.app.state.MODELS = previous
        assert response.status_code == 200
        return response.json()

    def test_endpoint_models_active_if_in_chat_dropdown_else_archived(self):
        """Active = matches a chat model; every other stored name is its own archived entry."""
        self._insert_model("GPT 4o")
        self._insert_model("gpt 4o")  # same chat model, different case
        self._insert_model("HR Assistant")  # matches a display name
        self._insert_model("gpt-4o-2024-08-06")  # provider names: not merged
        self._insert_model("gpt-4o-2024-11-20")
        self._insert_model("hidden-model")  # hidden in chat: archived
        self._insert_model("azure/gpt-4o", mcp_tool="news_server")  # tool use: left out

        data = self._get("/models")

        assert data["active_models"] == [
            {"id": "GPT 4o", "name": "GPT 4o"},
            {"id": "custom-hr", "name": "HR Assistant"},
        ]
        assert data["archived_models"] == [
            {"id": "gpt-4o-2024-08-06", "name": "gpt-4o-2024-08-06"},
            {"id": "gpt-4o-2024-11-20", "name": "gpt-4o-2024-11-20"},
            {"id": "hidden-model", "name": "hidden-model"},
        ]
        assert "azure/gpt-4o" not in data["models"]

    def test_endpoint_model_prompts_active_counts_case_variants(self):
        """An active model counts rows stored under its id or name in any case."""
        self._insert_model("GPT 4o")
        self._insert_model("gpt 4o")
        self._insert_model("GPT 4o mini")  # longer name: a different model
        self._insert_model("gpt-4o-2024-11-20")  # provider name: archived
        self._insert_model("azure/gpt-4o", mcp_tool="news_server")

        assert self._get("/models/prompts", {"model": "GPT 4o"})["total_prompts"] == 2

    def test_endpoint_model_prompts_archived_counts_that_name_only(self):
        """An archived entry counts exactly its stored name, nothing merged."""
        self._insert_model("gpt-4o-2024-08-06")
        self._insert_model("gpt-4o-2024-08-06")
        self._insert_model("gpt-4o-2024-11-20")

        data = self._get("/models/prompts", {"model": "gpt-4o-2024-08-06"})
        assert data["total_prompts"] == 2

    def test_endpoint_range_growth_uses_same_names_in_both_periods(self):
        """Prompt growth counts the same stored names (all case variants) in both periods."""
        from datetime import datetime

        from open_webui.internal.db import Session

        today = datetime.now().strftime("%Y-%m-%d")
        # Same conversion the endpoint uses for start_date
        start_ts = int(datetime.strptime(today, "%Y-%m-%d").timestamp())

        # Previous period (yesterday): one prompt, stored in lower case
        self._insert_model("gpt 4o")
        Session.execute(
            text("UPDATE message_metrics SET created_at = :ts"),
            {"ts": start_ts - 3600},
        )
        Session.commit()

        # Current period (today): two prompts
        self._insert_model("GPT 4o")
        self._insert_model("gpt 4o")
        # Tool use is never counted under a model
        self._insert_model("azure/gpt-4o", mcp_tool="news_server")

        data = self._get(
            "/range/users",
            {"start_date": today, "end_date": today, "model": "GPT 4o"},
        )
        assert data["metrics"]["total_prompts"] == 2
        assert data["growth"]["prompt_growth_percent"] == 100.0


# ── Model list matching (no database needed) ──────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [
        ("GPT 4o", "GPT 4o"),
        ("gpt 4o", "GPT 4o"),  # case is ignored
        ("custom-hr", "custom-hr"),  # matches the id
        ("HR Assistant", "custom-hr"),  # matches the display name
        ("GPT 4o mini", None),  # longer name: a different model
        ("gpt-4o-2024-11-20", None),  # provider name: not in the chat dropdown
    ],
)
def test_match_live_name(name, expected):
    from open_webui.routers.metrics import _match_live_name

    assert _match_live_name(name, _LIVE_MODELS) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        # In the chat dropdown: active, under the chat model id
        ("GPT 4o", "GPT 4o"),
        ("gpt 4o", "GPT 4o"),
        ("HR Assistant", "custom-hr"),
        # Not in the chat dropdown: archived as the stored name, nothing merged
        ("gpt-4o-2024-08-06", "gpt-4o-2024-08-06"),
        ("gpt-4o-2024-11-20", "gpt-4o-2024-11-20"),
        ("command-4", "command-4"),
    ],
)
def test_model_key(name, expected):
    from open_webui.routers.metrics import _model_key

    assert _model_key(name, _LIVE_MODELS) == expected


@pytest.mark.parametrize(
    "model,expected",
    [
        ({}, False),
        ({"info": None}, False),
        ({"info": {"meta": None}}, False),
        ({"info": {"meta": {"hidden": False}}}, False),
        ({"info": {"meta": {"hidden": True}}}, True),
    ],
)
def test_is_hidden(model, expected):
    from open_webui.routers.metrics import _is_hidden

    assert _is_hidden(model) == expected
