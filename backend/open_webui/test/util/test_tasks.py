"""OBS-30: task lifecycle metrics and finalization in ``open_webui.tasks``.

Every task is finalized once, whether it completes, fails, is stopped, is
cancelled before it starts or is still running at shutdown: it leaves the
registry, records one ``canchat.tasks.finished`` value and calls its
``on_finish`` hook once.
"""

import asyncio

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from open_webui import tasks
from open_webui.config import DEFAULT_METRIC_DEFINITIONS
from open_webui.metrics import service as service_module
from open_webui.metrics.service import MetricsService


class Metrics:
    def __init__(self):
        self.reader = InMemoryMetricReader()
        self.provider = MeterProvider(
            metric_readers=[self.reader], shutdown_on_exit=False
        )
        self.service = MetricsService(
            self.provider.get_meter("test"), DEFAULT_METRIC_DEFINITIONS, strict=True
        )
        tasks.register_task_metrics(self.service)

    def points(self, name):
        data = self.reader.get_metrics_data()
        for resource_metrics in data.resource_metrics if data else ():
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name == name:
                        return list(metric.data.data_points)
        return []

    def outcomes(self):
        return {
            tuple(sorted(point.attributes.items())): point.value
            for point in self.points(tasks.TASKS_FINISHED)
        }

    def active(self):
        (point,) = self.points(tasks.TASKS_ACTIVE)
        return point.value


@pytest.fixture
def metrics(monkeypatch):
    m = Metrics()
    monkeypatch.setattr(service_module, "_current_service", m.service)
    monkeypatch.setattr(tasks, "tasks", {})
    monkeypatch.setattr(tasks, "_finalizers", {})
    yield m
    m.provider.shutdown()


class Finalizer:
    def __init__(self):
        self.calls = []

    def __call__(self, outcome, error):
        self.calls.append((outcome, type(error).__name__ if error else None))


async def settle():
    # Lets done callbacks scheduled with call_soon run.
    for _ in range(3):
        await asyncio.sleep(0)


def outcome(name, error_type=None):
    attrs = {"outcome": name}
    if error_type:
        attrs["error.type"] = error_type
    return tuple(sorted(attrs.items()))


@pytest.mark.anyio
async def test_completed_task_is_finalized_once(metrics):
    release = asyncio.Event()
    finalizer = Finalizer()

    async def work():
        await release.wait()
        return "done"

    task_id, task = tasks.create_task(work(), on_finish=finalizer)
    assert tasks.list_tasks() == [task_id]
    assert metrics.active() == 1

    release.set()
    assert await task == "done"
    await settle()

    assert tasks.list_tasks() == []
    assert metrics.active() == 0
    assert finalizer.calls == [("success", None)]
    assert metrics.outcomes() == {outcome("success"): 1}


@pytest.mark.anyio
async def test_failed_task_records_error_type_only(metrics, caplog):
    finalizer = Finalizer()

    async def work():
        raise ConnectionError("secret prompt text")

    _, task = tasks.create_task(work(), on_finish=finalizer)
    await asyncio.wait({task})
    await settle()

    assert finalizer.calls == [("error", "ConnectionError")]
    assert metrics.outcomes() == {outcome("error", "ConnectionError"): 1}
    assert "ConnectionError" in caplog.text
    assert "secret prompt text" not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("swallow", [True, False])
async def test_stop_task_finalizes_cancelled_once(metrics, swallow):
    started = asyncio.Event()
    finalizer = Finalizer()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # The chat background handler handles cancellation and returns.
            if not swallow:
                raise

    task_id, task = tasks.create_task(work(), on_finish=finalizer)
    await started.wait()

    result = await tasks.stop_task(task_id)
    await settle()

    assert result["status"] is True
    assert task.done()
    assert tasks.list_tasks() == []
    assert finalizer.calls == [("cancelled", None)]
    assert metrics.outcomes() == {outcome("cancelled"): 1}


@pytest.mark.anyio
async def test_stop_unknown_task_raises(metrics):
    with pytest.raises(ValueError):
        await tasks.stop_task("missing")


@pytest.mark.anyio
async def test_task_cancelled_before_it_starts_is_finalized(metrics):
    ran = []
    finalizer = Finalizer()

    async def work():
        ran.append(True)

    _, task = tasks.create_task(work(), on_finish=finalizer)
    task.cancel()
    await asyncio.wait({task})
    await settle()

    assert ran == []
    assert finalizer.calls == [("cancelled", None)]
    assert metrics.outcomes() == {outcome("cancelled"): 1}


@pytest.mark.anyio
async def test_shutdown_cancels_and_finalizes_each_task_once(metrics):
    started = asyncio.Event()
    cleaned_up = []
    finalizers = [Finalizer(), Finalizer()]

    async def work(index):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaned_up.append(index)

    for index, finalizer in enumerate(finalizers):
        tasks.create_task(work(index), on_finish=finalizer)
    await started.wait()

    assert await tasks.shutdown_tasks(timeout=5) == 2
    await settle()

    assert sorted(cleaned_up) == [0, 1]
    assert tasks.list_tasks() == []
    assert metrics.active() == 0
    assert [f.calls for f in finalizers] == [[("cancelled", None)]] * 2
    assert metrics.outcomes() == {outcome("cancelled"): 2}
    # Nothing left to finalize on a second call.
    assert await tasks.shutdown_tasks(timeout=5) == 0


@pytest.mark.anyio
async def test_shutdown_finalizes_task_that_does_not_stop_in_time(metrics):
    started = asyncio.Event()
    release = asyncio.Event()
    finalizer = Finalizer()

    async def stubborn():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Slow cancellation handling, e.g. a hung socket emit.
            await release.wait()

    _, task = tasks.create_task(stubborn(), on_finish=finalizer)
    await started.wait()

    assert await tasks.shutdown_tasks(timeout=0.01) == 1
    assert not task.done()
    assert tasks.list_tasks() == []
    assert finalizer.calls == [("cancelled", None)]

    # The task ending later does not finalize it a second time.
    release.set()
    await asyncio.wait({task})
    await settle()
    assert finalizer.calls == [("cancelled", None)]
    assert metrics.outcomes() == {outcome("cancelled"): 1}


@pytest.mark.anyio
async def test_failing_finalizer_does_not_break_the_registry(metrics):
    def broken(outcome, error):
        raise RuntimeError("boom")

    _, task = tasks.create_task(asyncio.sleep(0), on_finish=broken)
    await task
    await settle()

    assert tasks.list_tasks() == []
    assert metrics.outcomes() == {outcome("success"): 1}


def test_task_metrics_are_defined():
    by_name = {d["name"]: d for d in DEFAULT_METRIC_DEFINITIONS}
    assert by_name[tasks.TASKS_ACTIVE]["type"] == "observable_updowncounter"
    assert by_name[tasks.TASKS_FINISHED]["type"] == "counter"
