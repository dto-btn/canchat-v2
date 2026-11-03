"""Process-wide OpenTelemetry provider configuration and lifecycle."""

import atexit
import importlib.metadata
import ipaddress
import json
import math
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)

from open_webui.observability.tracing import (
    SanitizingSpanExporter,
    build_sampler,
    configure_propagation,
)

DEFAULT_ENDPOINT = "http://otel-collector:4318"
DEFAULT_SERVICE_NAME = "canchat-backend"
DEFAULT_METER_NAME = "canchat-meter"
DEFAULT_METRIC_EXPORT_INTERVAL_MS = 15000
DEFAULT_TRACES_SAMPLER_RATIO = 0.1
DEFAULT_DEV_TRACES_SAMPLER_RATIO = 1.0
_LEGACY_METRIC_PATH = "/v1/metrics"
_SHUTDOWN_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class OTelSettings:
    enabled: bool
    service_name: str
    service_version: str
    environment: str
    endpoint: str
    meter_name: str
    metric_export_interval_ms: int
    traces_sampler_ratio: float = DEFAULT_DEV_TRACES_SAMPLER_RATIO
    traces_healthchecks: bool = False
    propagation_allowed_hosts: tuple[str, ...] = ()


@dataclass
class ProviderBundle:
    meter_provider: MeterProvider
    tracer_provider: TracerProvider
    logger_provider: LoggerProvider
    meter: Meter
    tracer: trace.Tracer
    export_enabled: bool = False
    settings: OTelSettings | None = None

    def shutdown(self) -> None:
        errors: list[Exception] = []
        for provider in (
            self.logger_provider,
            self.tracer_provider,
            self.meter_provider,
        ):
            try:
                if not provider.force_flush(timeout_millis=_SHUTDOWN_TIMEOUT_MS):
                    errors.append(
                        RuntimeError(f"{type(provider).__name__} force_flush timed out")
                    )
            except Exception as error:
                errors.append(error)

            try:
                provider.shutdown()
            except Exception as error:
                errors.append(error)

        if errors:
            raise RuntimeError(
                f"Failed to flush or shut down {len(errors)} OpenTelemetry operation(s)"
            ) from errors[0]


def _service_version() -> str:
    if os.environ.get("FROM_INIT_PY", "false").strip().lower() == "true":
        try:
            return importlib.metadata.version("open-webui")
        except importlib.metadata.PackageNotFoundError as error:
            raise RuntimeError(
                "Unable to determine the installed Open WebUI service version"
            ) from error

    package_json = Path(__file__).resolve().parents[3] / "package.json"
    try:
        with package_json.open(encoding="utf-8") as package_file:
            version = json.load(package_file)["version"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError(
            "Unable to determine the Open WebUI service version"
        ) from error
    if not isinstance(version, str) or not version:
        raise RuntimeError("The package.json version must be a non-empty string")
    return version


def _resolve_setting(
    environ: Mapping[str, str], name: str, alias: str | None, default: str
) -> str:
    if name in environ:
        return environ[name]
    if alias is not None and alias in environ:
        return environ[alias]
    return default


def _strip_legacy_metric_path(endpoint: str) -> str:
    parts = urlsplit(endpoint.rstrip("/"))
    path = parts.path
    if path.endswith(_LEGACY_METRIC_PATH):
        path = path[: -len(_LEGACY_METRIC_PATH)]
    return urlunsplit(
        (parts.scheme, parts.netloc, path, parts.query, parts.fragment)
    ).rstrip("/")


def _is_valid_hostname(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            return False

        if len(ascii_hostname) > 253:
            return False
        if ascii_hostname.endswith("."):
            ascii_hostname = ascii_hostname[:-1]
        return bool(ascii_hostname) and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in ascii_hostname.split(".")
        )


def _validate_endpoint(endpoint: str) -> None:
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError as error:
        raise ValueError(
            "OTEL_EXPORTER_OTLP_ENDPOINT must be a valid HTTP(S) base URL"
        ) from error

    hostname = parts.hostname
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not hostname
        or not _is_valid_hostname(hostname)
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or "?" in endpoint
        or "#" in endpoint
        or port == 0
        or any(character.isspace() for character in endpoint)
    ):
        raise ValueError("OTEL_EXPORTER_OTLP_ENDPOINT must be a valid HTTP(S) base URL")


def _parse_sampler_ratio(value: str) -> float:
    try:
        ratio = float(value)
    except ValueError:
        ratio = math.nan
    if not math.isfinite(ratio) or not 0.0 <= ratio <= 1.0:
        raise ValueError(
            "OTEL_TRACES_SAMPLER_RATIO must be a number between 0.0 and 1.0"
        )
    return ratio


def _parse_propagation_hosts(value: str) -> tuple[str, ...]:
    hosts = []
    for entry in value.split(","):
        host = entry.strip().lower().rstrip(".")
        if not host:
            continue
        if host.startswith("*."):
            valid = _is_valid_hostname(host[2:]) and "." in host[2:]
        else:
            valid = _is_valid_hostname(host)
        if not valid:
            raise ValueError(
                "OTEL_PROPAGATION_ALLOWED_HOSTS must be a comma-separated list of "
                "hostnames, IP addresses or '*.<domain>' wildcards"
            )
        hosts.append(host)
    return tuple(dict.fromkeys(hosts))


def load_settings(environ: Mapping[str, str] | None = None) -> OTelSettings:
    """Resolve OTEL settings, preferring current names over legacy aliases."""
    values = os.environ if environ is None else environ
    environment = values.get("ENV", "dev")
    container_defaults = (
        values.get("DOCKER", "false").strip().lower() == "true"
        or environment.strip().lower() != "dev"
    )
    default_enabled = "true" if container_defaults else "false"
    enabled_value = _resolve_setting(values, "OTEL_ENABLED", None, default_enabled)
    enabled_text = enabled_value.strip().lower()
    if enabled_text not in {"true", "false"}:
        raise ValueError("OTEL_ENABLED must be either 'true' or 'false'")

    if "OTEL_EXPORTER_OTLP_ENDPOINT" in values:
        endpoint = values["OTEL_EXPORTER_OTLP_ENDPOINT"].rstrip("/")
    elif "INSTRUMENTATION_ENDPOINT" in values:
        endpoint = _strip_legacy_metric_path(values["INSTRUMENTATION_ENDPOINT"])
    else:
        endpoint = DEFAULT_ENDPOINT
    if not endpoint:
        raise ValueError("OTEL_EXPORTER_OTLP_ENDPOINT must not be empty")
    _validate_endpoint(endpoint)

    interval_value = _resolve_setting(
        values,
        "OTEL_METRIC_EXPORT_INTERVAL",
        "INSTRUMENTATION_EXPORT_INTERVAL_MS",
        str(DEFAULT_METRIC_EXPORT_INTERVAL_MS),
    )
    try:
        metric_export_interval_ms = int(interval_value)
    except ValueError as error:
        raise ValueError(
            "OTEL_METRIC_EXPORT_INTERVAL must be a positive integer in milliseconds"
        ) from error
    if metric_export_interval_ms <= 0:
        raise ValueError(
            "OTEL_METRIC_EXPORT_INTERVAL must be a positive integer in milliseconds"
        )

    service_name = _resolve_setting(
        values,
        "OTEL_SERVICE_NAME",
        "INSTRUMENTATION_SERVICE_NAME",
        DEFAULT_SERVICE_NAME,
    )
    if not service_name:
        raise ValueError("OTEL_SERVICE_NAME must not be empty")

    traces_sampler_ratio = _parse_sampler_ratio(
        values.get(
            "OTEL_TRACES_SAMPLER_RATIO",
            str(
                DEFAULT_TRACES_SAMPLER_RATIO
                if container_defaults
                else DEFAULT_DEV_TRACES_SAMPLER_RATIO
            ),
        ).strip()
    )

    healthchecks_text = values.get("OTEL_TRACES_HEALTHCHECKS", "false").strip().lower()
    if healthchecks_text not in {"true", "false"}:
        raise ValueError("OTEL_TRACES_HEALTHCHECKS must be either 'true' or 'false'")

    return OTelSettings(
        enabled=enabled_text == "true",
        service_name=service_name,
        service_version=_service_version(),
        environment=environment,
        endpoint=endpoint,
        meter_name=values.get("INSTRUMENTATION_METER_NAME", DEFAULT_METER_NAME),
        metric_export_interval_ms=metric_export_interval_ms,
        traces_sampler_ratio=traces_sampler_ratio,
        traces_healthchecks=healthchecks_text == "true",
        propagation_allowed_hosts=_parse_propagation_hosts(
            values.get("OTEL_PROPAGATION_ALLOWED_HOSTS", "")
        ),
    )


def _signal_endpoint(base_endpoint: str, signal: str) -> str:
    return f"{base_endpoint.rstrip('/')}/v1/{signal}"


def create_providers(
    settings: OTelSettings,
    metric_readers: Sequence[MetricReader] = (),
    span_exporters: Sequence[SpanExporter] = (),
) -> ProviderBundle:
    """Build a provider bundle; optional readers/exporters support in-memory tests.

    Every span exporter, including test exporters, receives spans only after
    the attribute allow-list in ``tracing.SanitizingSpanExporter`` is applied.
    """
    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": settings.service_version,
            "deployment.environment": settings.environment,
        }
    )
    configured_readers = list(metric_readers)
    # The lifecycle owner shuts providers down once; SDK atexit hooks would repeat it.
    tracer_provider = TracerProvider(
        resource=resource,
        sampler=build_sampler(settings.traces_sampler_ratio),
        shutdown_on_exit=False,
    )
    logger_provider = LoggerProvider(resource=resource, shutdown_on_exit=False)
    for exporter in span_exporters:
        tracer_provider.add_span_processor(
            SimpleSpanProcessor(SanitizingSpanExporter(exporter))
        )

    if settings.enabled:
        configured_readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=_signal_endpoint(settings.endpoint, "metrics")
                ),
                export_interval_millis=settings.metric_export_interval_ms,
            )
        )
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                SanitizingSpanExporter(
                    OTLPSpanExporter(
                        endpoint=_signal_endpoint(settings.endpoint, "traces")
                    )
                )
            )
        )
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(
                OTLPLogExporter(endpoint=_signal_endpoint(settings.endpoint, "logs"))
            )
        )

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=configured_readers,
        shutdown_on_exit=False,
    )
    return ProviderBundle(
        meter_provider=meter_provider,
        tracer_provider=tracer_provider,
        logger_provider=logger_provider,
        meter=meter_provider.get_meter(settings.meter_name),
        tracer=tracer_provider.get_tracer(settings.meter_name),
        export_enabled=settings.enabled,
        settings=settings,
    )


_provider_lock = threading.Lock()
_providers: ProviderBundle | None = None
_providers_shutdown = False


def initialize_providers() -> ProviderBundle:
    """Set the process-wide providers exactly once."""
    global _providers

    with _provider_lock:
        if _providers is None:
            settings = load_settings()
            providers = create_providers(settings)
            metrics.set_meter_provider(providers.meter_provider)
            trace.set_tracer_provider(providers.tracer_provider)
            set_logger_provider(providers.logger_provider)
            configure_propagation(settings.propagation_allowed_hosts)
            _providers = providers
            # Covers processes that never run the FastAPI lifespan, such as scripts.
            atexit.register(shutdown_providers)
        return _providers


def shutdown_providers() -> None:
    """Flush and shut down the process-wide providers once."""
    global _providers_shutdown

    with _provider_lock:
        if _providers is None or _providers_shutdown:
            return
        _providers_shutdown = True
        _providers.shutdown()
