"""
Web Search Audit Logger

Structured audit logging for every web search query dispatched to upstream providers.
Designed for:
  - Data leakage analysis (what queries are leaving the system)
  - Performance monitoring (latency, result counts)
  - Security auditing (who searched what, when)

Each log record is emitted as a single JSON payload in the application logger,
so it is captured by the container runtime (stdout/stderr) without external
file writes.
"""

import hashlib
import json
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from urllib.parse import urlparse, urlunparse

from open_webui.env import SRC_LOG_LEVELS

try:
    from open_webui.instrumentation import meter as otel_meter
except Exception:
    otel_meter = None

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])


@dataclass
class WebSearchMetricsInstruments:
    dispatch_counter: Any = None
    result_counter: Any = None
    result_latency_histogram: Any = None
    result_count_histogram: Any = None
    content_load_counter: Any = None
    content_load_latency_histogram: Any = None


def _create_metrics_instruments() -> WebSearchMetricsInstruments:
    if otel_meter is None:
        return WebSearchMetricsInstruments()

    try:
        return WebSearchMetricsInstruments(
            dispatch_counter=otel_meter.create_counter(
                name="web_search_dispatch_total",
                description="Total web-search requests dispatched to providers",
                unit="{dispatch}",
            ),
            result_counter=otel_meter.create_counter(
                name="web_search_result_total",
                description="Total web-search provider results (success and failure)",
                unit="{result}",
            ),
            result_latency_histogram=otel_meter.create_histogram(
                name="web_search_result_latency_ms",
                description="Latency of provider web-search calls in milliseconds",
                unit="ms",
            ),
            result_count_histogram=otel_meter.create_histogram(
                name="web_search_result_count",
                description="Number of search results returned by providers",
                unit="{result}",
            ),
            content_load_counter=otel_meter.create_counter(
                name="web_search_content_load_total",
                description="Total web-search content-load phases",
                unit="{load}",
            ),
            content_load_latency_histogram=otel_meter.create_histogram(
                name="web_search_content_load_latency_ms",
                description="Latency of web-search content loading in milliseconds",
                unit="ms",
            ),
        )
    except Exception:
        log.exception("[web_search_audit] failed to initialize OTel metrics")
        return WebSearchMetricsInstruments()


_METRICS_INSTRUMENTS = _create_metrics_instruments()
_MAX_LOGGED_RESULT_URLS = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_query(query: str) -> str:
    """SHA-256 hash of the normalised query for correlation without storing PII at INFO level."""
    normalised = query.lower().strip()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def _safe_user_attrs(user) -> dict:
    """Extract safe, non-secret user attributes for audit logging."""
    if not user:
        return {
            "user_id": None,
            "user_role": None,
            "user_email": None,
            "user_name": None,
        }
    return {
        "user_id": (
            str(getattr(user, "id", None))
            if getattr(user, "id", None) is not None
            else None
        ),
        "user_role": getattr(user, "role", None),
        "user_email": getattr(user, "email", None),
        "user_name": getattr(user, "name", None),
    }


def _sanitize_result_url(url: str) -> str:
    """Remove query strings and fragments before logging result URLs."""
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _sanitize_urls(urls: list[str], limit: int) -> list[str]:
    sanitized_urls = []
    for url in urls:
        if len(sanitized_urls) >= limit:
            break
        try:
            sanitized_urls.append(_sanitize_result_url(url))
        except Exception:
            sanitized_urls.append(url)
    return sanitized_urls


def _emit_sensitive_debug_record(record: dict) -> None:
    """Emit sensitive dispatch details at DEBUG level only."""
    try:
        payload = json.dumps(
            record, default=str, ensure_ascii=False, separators=(",", ":")
        )
        log.debug("[web_search_audit_sensitive] %s", payload)
    except Exception:
        log.exception("[web_search_audit_sensitive] failed to serialize audit record")


def _emit_audit_record(record: dict) -> None:
    """Emit a structured audit record into container logs without raising."""
    try:
        payload = json.dumps(
            record, default=str, ensure_ascii=False, separators=(",", ":")
        )
        log.info("[web_search_audit] %s", payload)
    except Exception:
        log.exception("[web_search_audit] failed to serialize audit record")


def _emit_audit_records(record: dict, sensitive_record: Optional[dict] = None) -> None:
    _emit_audit_record(record)
    if sensitive_record is not None:
        _emit_sensitive_debug_record(sensitive_record)


def _record_counter(instrument, value: int, attributes: dict[str, Any]) -> None:
    if instrument is None:
        return
    try:
        instrument.add(value, attributes=attributes)
    except Exception:
        log.debug("[web_search_audit] failed to record counter metric", exc_info=True)


def _record_histogram(instrument, value: int, attributes: dict[str, Any]) -> None:
    if instrument is None:
        return
    try:
        instrument.record(value, attributes=attributes)
    except Exception:
        log.debug("[web_search_audit] failed to record histogram metric", exc_info=True)


@dataclass
class WebSearchAuditContext:
    event_id: str
    engine: str
    start_time: float = field(default_factory=time.monotonic)
    result_logged: bool = False


def new_web_search_event_id() -> str:
    """Generate a collision-resistant web-search event identifier."""
    return f"wsd_{uuid.uuid4()}"


def _normalize_dispatch_ctx(
    dispatch_ctx: WebSearchAuditContext | dict,
) -> WebSearchAuditContext:
    if isinstance(dispatch_ctx, WebSearchAuditContext):
        return dispatch_ctx

    return WebSearchAuditContext(
        event_id=dispatch_ctx["event_id"],
        start_time=dispatch_ctx["start_time"],
        engine=dispatch_ctx["engine"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def log_web_search_dispatch(
    *,
    engine: str,
    query: str,
    result_count_requested: int,
    domain_filter_list: Optional[list] = None,
    user=None,
    event_id: Optional[str] = None,
) -> WebSearchAuditContext:
    """
    Log a web search dispatch event BEFORE calling the upstream provider.

    Returns a context object to be passed to ``log_web_search_result()`` later.
    """
    ctx = WebSearchAuditContext(
        event_id=event_id or new_web_search_event_id(),
        engine=engine,
    )
    ts = datetime.now(timezone.utc).isoformat()
    user_attrs = _safe_user_attrs(user)
    query_hash = _hash_query(query)

    try:
        record = {
            "event_id": ctx.event_id,
            "event_type": "web_search_dispatch",
            "timestamp": ts,
            "engine": engine,
            "query": None,
            "query_hash": query_hash,
            "query_length": len(query),
            "result_count_requested": result_count_requested,
            "domain_filter_active": bool(domain_filter_list),
            "domain_filter_count": len(domain_filter_list) if domain_filter_list else 0,
            "user_id": user_attrs["user_id"],
            "user_role": user_attrs["user_role"],
            "user_email": None,
            "user_name": None,
        }
        _emit_audit_records(
            record,
            {
                "event_type": "web_search_dispatch_sensitive",
                "query": query,
                "user_email": user_attrs["user_email"],
                "user_name": user_attrs["user_name"],
            },
        )
        _record_counter(
            _METRICS_INSTRUMENTS.dispatch_counter,
            1,
            {
                "engine": engine,
                "domain_filter_active": bool(domain_filter_list),
            },
        )
    except Exception:
        log.exception("[web_search_dispatch] audit logging failure")

    return ctx


def log_web_search_result(
    *,
    dispatch_ctx: WebSearchAuditContext | dict,
    results: list,
    error: Optional[Exception] = None,
) -> None:
    """
    Log the outcome of a web search call AFTER the upstream provider returns.

    Args:
        dispatch_ctx: A ``WebSearchAuditContext`` returned by
            ``log_web_search_dispatch()`` or a compatible dict audit context.
        results: The list of SearchResult objects returned (empty on failure).
        error: The exception if the call failed, else None.
    """
    ctx = _normalize_dispatch_ctx(dispatch_ctx)
    elapsed_ms = round((time.monotonic() - ctx.start_time) * 1000)
    event_id = ctx.event_id
    engine = ctx.engine

    try:
        result_links = []
        result_domains: list[str] = []
        _seen_domains: set[str] = set()
        for result in results or []:
            link = getattr(result, "link", None)
            if link:
                result_links.append(link)
                try:
                    domain = urlparse(link).netloc
                    if domain and domain not in _seen_domains:
                        result_domains.append(domain)
                        _seen_domains.add(domain)
                except Exception:
                    continue

        result_urls = _sanitize_urls(result_links, _MAX_LOGGED_RESULT_URLS)

        record = {
            "event_id": event_id,
            "event_type": "web_search_result",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "engine": engine,
            "latency_ms": elapsed_ms,
            "result_count": len(results) if results else 0,
            "result_urls": result_urls,
            "result_domains": result_domains,
            "result_domain_count": len(result_domains),
            "has_snippets": sum(
                1 for result in (results or []) if getattr(result, "snippet", None)
            ),
            "success": error is None,
            "error_type": type(error).__name__ if error else None,
            "error_message": str(error)[:500] if error else None,
        }
        _emit_audit_record(record)

        metric_attrs = {
            "engine": engine,
            "success": error is None,
            "error_type": type(error).__name__ if error else "none",
        }
        _record_counter(_METRICS_INSTRUMENTS.result_counter, 1, metric_attrs)
        _record_histogram(
            _METRICS_INSTRUMENTS.result_latency_histogram,
            elapsed_ms,
            metric_attrs,
        )
        _record_histogram(
            _METRICS_INSTRUMENTS.result_count_histogram,
            len(results) if results else 0,
            metric_attrs,
        )
    except Exception:
        log.exception("[web_search_result] audit logging failure")
    finally:
        ctx.result_logged = True


@contextmanager
def web_search_audit_scope(
    *,
    engine: str,
    query: str,
    result_count_requested: int,
    domain_filter_list: Optional[list] = None,
    user=None,
    event_id: Optional[str] = None,
) -> Iterator[WebSearchAuditContext]:
    """Context manager that ensures dispatch and failure result are paired."""
    dispatch_ctx = log_web_search_dispatch(
        engine=engine,
        query=query,
        result_count_requested=result_count_requested,
        domain_filter_list=domain_filter_list,
        user=user,
        event_id=event_id,
    )
    try:
        yield dispatch_ctx
    except Exception as error:
        if not dispatch_ctx.result_logged:
            log_web_search_result(dispatch_ctx=dispatch_ctx, results=[], error=error)
        raise


def log_web_search_content_load(
    *,
    event_id: str,
    urls: list,
    docs_loaded: int,
    latency_ms: int,
    bypass_embedding: bool = False,
    collection_name: Optional[str] = None,
    engine: Optional[str] = None,
    error: Optional[Exception] = None,
) -> None:
    """
    Log the URL content-loading phase that follows search result retrieval.

    This captures what external URLs are actually being fetched/scraped,
    which is critical for data-leakage analysis.
    """
    try:
        record = {
            "event_id": event_id,
            "event_type": "web_search_content_load",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "engine": engine,
            "urls_requested_count": len(urls) if urls else 0,
            "docs_loaded": docs_loaded,
            "latency_ms": latency_ms,
            "bypass_embedding": bypass_embedding,
            "collection_name": collection_name,
            "success": error is None,
            "error_type": type(error).__name__ if error else None,
            "error_message": str(error)[:500] if error else None,
        }
        _emit_audit_records(
            record,
            {
                "event_type": "web_search_content_load_sensitive",
                "urls_requested": urls,
            },
        )

        metric_attrs = {
            "engine": engine or "unknown",
            "success": error is None,
            "bypass_embedding": bypass_embedding,
            "error_type": type(error).__name__ if error else "none",
        }
        _record_counter(_METRICS_INSTRUMENTS.content_load_counter, 1, metric_attrs)
        _record_histogram(
            _METRICS_INSTRUMENTS.content_load_latency_histogram,
            latency_ms,
            metric_attrs,
        )
    except Exception:
        log.exception("[web_search_content_load] audit logging failure")
