"""OpenTelemetry providers initialized before application instruments."""

from open_webui.observability.providers import initialize_providers, shutdown_providers

_providers = initialize_providers()

meter = _providers.meter
tracer = _providers.tracer
logger_provider = _providers.logger_provider

__all__ = ["logger_provider", "meter", "shutdown_providers", "tracer"]
