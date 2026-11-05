"""Registry-backed OpenTelemetry metrics with a single recording API (OBS-21).

* ``record()`` is the only way to emit counter, up-down-counter and histogram
  values; raw synchronous instruments are not handed out, so every value goes
  through the label allow-list.
* Observable gauges and up-down counters get one dispatcher callback when they
  are created. Data sources are attached later with ``register_callback()``.
* Labels outside the allow-list are dropped with a one-time warning, or raise
  ``MetricAttributeError`` when ``METRICS_STRICT=true`` (tests).
"""

import logging
import os
import threading
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from opentelemetry.metrics import CallbackOptions, Meter, Observation
from pydantic import BaseModel

log = logging.getLogger(__name__)

SYNCHRONOUS_TYPES = frozenset({"counter", "updowncounter", "histogram"})
OBSERVABLE_TYPES = frozenset({"gauge", "observable_updowncounter"})
METRIC_TYPES = SYNCHRONOUS_TYPES | OBSERVABLE_TYPES

# Seconds. Upper bounds cover slow reasoning models and long tool calls.
LLM_DURATION_BUCKETS = (0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300)
LLM_TTFT_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 20, 30)
# End-to-end chat adds web search, RAG and tools before the provider call.
CHAT_OPERATION_BUCKETS = (0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600)
RAG_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30)
MCP_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120)

# Low-cardinality labels that any metric may carry.
ALLOWED_METRIC_ATTRIBUTES = frozenset(
    {
        "model",
        "provider",
        "outcome",
        "operation",
        "stream",
        "error.type",
        "mcp.server",
        "mcp.tool",
        "rag.source",
        "domain",
    }
)

# Identifiers that must never become labels, even if a definition lists them.
FORBIDDEN_METRIC_ATTRIBUTES = frozenset(
    {
        "user",
        "user_id",
        "user.id",
        "email",
        "user.email",
        "session",
        "session_id",
        "session.id",
        "request",
        "request_id",
        "request.id",
        "chat",
        "chat_id",
        "chat.id",
        "message_id",
        "message.id",
        "trace",
        "trace_id",
        "trace.id",
        "span_id",
        "span.id",
    }
)

_SCALAR_TYPES = (str, bool, int, float)

GaugeSource = Callable[[], Iterable[tuple[float, Mapping[str, Any] | None]]]


class MetricsDefinition(BaseModel):
    name: str
    type: str
    description: str
    unit: str
    buckets: list[float] | None = None
    attributes: list[str] | None = None


class MetricAttributeError(ValueError):
    """Raised in strict mode when a label is not on the allow-list."""


class MetricUsageError(ValueError):
    """Raised in strict mode when ``record``/``register_callback`` is misused."""


def _strict_from_env() -> bool:
    return os.environ.get("METRICS_STRICT", "false").strip().lower() == "true"


class MetricsService:
    def __init__(
        self,
        meter: Meter,
        definitions: list[dict] | list[MetricsDefinition] | None = None,
        *,
        strict: bool | None = None,
    ):
        self._meter = meter
        self._strict = _strict_from_env() if strict is None else strict
        self._registry: dict[str, object] = {}
        self._definitions: dict[str, MetricsDefinition] = {}
        self._gauge_sources: dict[str, list[GaugeSource]] = {}
        self._warned: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

        for raw_def in definitions or []:
            try:
                definition = (
                    raw_def
                    if isinstance(raw_def, MetricsDefinition)
                    else MetricsDefinition(**raw_def)
                )
                self.add_instrument(definition)
            except Exception as e:
                name = raw_def.get("name") if isinstance(raw_def, dict) else None
                name = getattr(raw_def, "name", name)
                log.error(
                    "Failed to create metric instrument %r: %s", name, type(e).__name__
                )

    @property
    def strict(self) -> bool:
        return self._strict

    def list_instruments(self) -> list[str]:
        return list(self._registry.keys())

    def has_instrument(self, name: str) -> bool:
        return name in self._registry

    def add_instrument(self, definition: MetricsDefinition) -> None:
        if definition.name in self._registry:
            raise KeyError(f"Instrument '{definition.name}' already exists")
        validate_definition(definition)

        normalized = definition.model_copy(update={"type": definition.type.lower()})
        self._registry[definition.name] = self._create_instrument(normalized)
        self._definitions[definition.name] = normalized

    def _create_instrument(self, definition: MetricsDefinition):
        common = {
            "name": definition.name,
            "description": definition.description,
            "unit": definition.unit,
        }
        metric_type = definition.type

        if metric_type == "counter":
            return self._meter.create_counter(**common)
        if metric_type == "updowncounter":
            return self._meter.create_up_down_counter(**common)
        if metric_type == "histogram":
            if definition.buckets:
                return self._meter.create_histogram(
                    **common,
                    explicit_bucket_boundaries_advisory=list(definition.buckets),
                )
            return self._meter.create_histogram(**common)

        self._gauge_sources[definition.name] = []
        callback = self._make_dispatcher(definition.name)
        if metric_type == "gauge":
            return self._meter.create_observable_gauge(callbacks=[callback], **common)
        return self._meter.create_observable_up_down_counter(
            callbacks=[callback], **common
        )

    def record(
        self, name: str, value: float, attrs: Mapping[str, Any] | None = None
    ) -> None:
        """Add to a counter/up-down counter or record a histogram value.

        Never raises in production: metrics problems must not break requests.
        """
        definition = self._definitions.get(name)
        if definition is None:
            self._misuse(name, "unknown", f"Unknown metric '{name}'")
            return
        if definition.type not in SYNCHRONOUS_TYPES:
            self._misuse(
                name,
                "observable",
                f"Metric '{name}' is observable; use register_callback()",
            )
            return
        if definition.type == "counter" and value < 0:
            self._misuse(
                name, "negative", f"Counter '{name}' cannot record a negative value"
            )
            return

        clean = self._filter_attrs(name, attrs)
        instrument = self._registry[name]
        try:
            if definition.type == "histogram":
                instrument.record(value, clean)
            else:
                instrument.add(value, clean)
        except Exception as e:
            log.warning("Failed to record metric %r: %s", name, type(e).__name__)

    def register_callback(self, name: str, source: GaugeSource) -> None:
        """Attach a data source to an observable gauge or up-down counter.

        ``source`` returns ``(value, attrs)`` pairs; attrs pass the allow-list.
        """
        definition = self._definitions.get(name)
        if definition is None:
            self._misuse(name, "unknown", f"Unknown metric '{name}'")
            return
        if definition.type not in OBSERVABLE_TYPES:
            self._misuse(
                name, "synchronous", f"Metric '{name}' is not observable; use record()"
            )
            return
        with self._lock:
            self._gauge_sources[name].append(source)

    def _make_dispatcher(self, name: str):
        def dispatch(_options: CallbackOptions) -> Iterable[Observation]:
            with self._lock:
                sources = list(self._gauge_sources.get(name, ()))
            observations = []
            for source in sources:
                try:
                    for value, attrs in source():
                        observations.append(
                            Observation(value, self._filter_attrs(name, attrs))
                        )
                except MetricAttributeError:
                    raise
                except Exception as e:
                    log.warning(
                        "Metric callback for %r failed: %s", name, type(e).__name__
                    )
            return observations

        return dispatch

    def _filter_attrs(
        self, name: str, attrs: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        if not attrs:
            return {}
        extra = self._definitions[name].attributes or ()
        clean: dict[str, Any] = {}
        for key, value in attrs.items():
            if key in FORBIDDEN_METRIC_ATTRIBUTES or (
                key not in ALLOWED_METRIC_ATTRIBUTES and key not in extra
            ):
                self._reject(name, key, "is not on the metric label allow-list")
                continue
            if not isinstance(value, _SCALAR_TYPES):
                self._reject(name, key, "must be a str, bool, int or float")
                continue
            clean[key] = value
        return clean

    def _reject(self, name: str, key: str, reason: str) -> None:
        # Only the key is reported; the value may be an identifier.
        message = f"Label '{key}' on metric '{name}' {reason}"
        if self._strict:
            raise MetricAttributeError(message)
        self._warn_once(name, key, message + "; dropped")

    def _misuse(self, name: str, kind: str, message: str) -> None:
        if self._strict:
            raise MetricUsageError(message)
        self._warn_once(name, f"__{kind}__", message)

    def _warn_once(self, name: str, key: str, message: str) -> None:
        with self._lock:
            if (name, key) in self._warned:
                return
            self._warned.add((name, key))
        log.warning(message)


def validate_definition(definition: MetricsDefinition) -> None:
    """Raise ``ValueError`` naming the offending field; never echo values."""
    if not definition.name.strip():
        raise ValueError("name: expected a non-empty string")

    metric_type = definition.type.lower()
    if metric_type not in METRIC_TYPES:
        raise ValueError("type: expected " + ", ".join(sorted(METRIC_TYPES)))

    if definition.buckets is not None:
        if metric_type != "histogram":
            raise ValueError("buckets: only valid for histogram metrics")
        buckets = definition.buckets
        if not buckets or any(b <= a for a, b in zip(buckets, buckets[1:])):
            raise ValueError("buckets: expected a non-empty, strictly increasing list")

    for key in definition.attributes or ():
        if not key.strip() or key in FORBIDDEN_METRIC_ATTRIBUTES:
            raise ValueError("attributes: contains a forbidden or empty label")
