"""OBS-24: ``canchat.llm.tokens`` is recorded with the ``message_metrics`` row."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from open_webui.config import DEFAULT_METRIC_DEFINITIONS
from open_webui.metrics import service as metrics_service_module
from open_webui.metrics.service import MetricsService
from open_webui.models import message_metrics
from open_webui.models.message_metrics import LLM_TOKENS, MessageMetric

USER = SimpleNamespace(id="u1", domain="gc.ca")


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    MessageMetric.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    @contextmanager
    def get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(message_metrics, "get_db", get_db)
    yield SessionLocal
    engine.dispose()


@pytest.fixture
def reader(monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
    # monkeypatch restores the process-wide service after the test.
    monkeypatch.setattr(
        metrics_service_module,
        "_current_service",
        MetricsService(
            provider.get_meter("test"), DEFAULT_METRIC_DEFINITIONS, strict=True
        ),
    )
    yield reader
    provider.shutdown()


def _counter_by_type(reader) -> dict[str, float]:
    data = reader.get_metrics_data()
    totals: dict[str, float] = {}
    for resource_metrics in data.resource_metrics if data else ():
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name != LLM_TOKENS:
                    continue
                for point in metric.data.data_points:
                    assert set(point.attributes) == {"type"}
                    totals[point.attributes["type"]] = point.value
    return totals


def _db_totals(SessionLocal) -> dict[str, float]:
    with SessionLocal() as session:
        prompt, completion = session.query(
            func.sum(MessageMetric.prompt_tokens),
            func.sum(MessageMetric.completion_tokens),
        ).one()
    return {"prompt": prompt or 0, "completion": completion or 0}


def test_db_row_totals_equal_counter(db, reader):
    MessageMetrics = message_metrics.MessageMetrics
    MessageMetrics.insert_new_metrics(
        USER,
        "gpt-4o",
        {"prompt_tokens": 60, "completion_tokens": 40, "total_tokens": 100},
        "chat-1",
    )
    MessageMetrics.insert_new_metrics(
        USER,
        "azure/gpt-4o",
        {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
        "chat-2",
        mcp_tool="news_server",
    )
    # Fallback row written when the provider sent no usage.
    MessageMetrics.insert_new_metrics(
        USER,
        "gpt-4o",
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )

    assert _db_totals(db) == {"prompt": 180, "completion": 120}
    assert _counter_by_type(reader) == _db_totals(db)


def test_failed_commit_does_not_increment_counter(db, reader, monkeypatch):
    def broken_commit(*_args, **_kwargs):
        raise RuntimeError("db down")

    with monkeypatch.context() as m, pytest.raises(RuntimeError):
        m.setattr("sqlalchemy.orm.Session.commit", broken_commit)
        message_metrics.MessageMetrics.insert_new_metrics(
            USER,
            "gpt-4o",
            {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        )

    assert _db_totals(db) == {"prompt": 0, "completion": 0}
    assert _counter_by_type(reader) == {}


def test_insert_works_without_metrics_service(db, monkeypatch):
    monkeypatch.setattr(metrics_service_module, "_current_service", None)
    message_metrics.MessageMetrics.insert_new_metrics(
        USER,
        "gpt-4o",
        {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    )
    assert _db_totals(db) == {"prompt": 7, "completion": 3}
