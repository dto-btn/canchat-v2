"""
Unit tests for health check endpoints (/health, /health/db, /health/redis, /health/ready).

These tests mock external dependencies (DB, Redis) to validate endpoint behavior
without requiring real infrastructure.
"""

from unittest.mock import patch, MagicMock
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from open_webui.main import app

    with TestClient(app) as c:
        yield c


# ── /health ───────────────────────────────────────────────────────────────────


class TestHealthcheck:
    def test_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": True}


# ── /health/db ────────────────────────────────────────────────────────────────


class TestHealthcheckDb:
    def test_db_healthy(self, client):
        mock_db = MagicMock()

        @contextmanager
        def fake_get_db():
            yield mock_db

        with patch("open_webui.main.get_db", fake_get_db):
            response = client.get("/health/db")

        assert response.status_code == 200
        assert response.json() == {"status": True}
        mock_db.execute.assert_called_once()

    def test_db_failure_returns_503(self, client):
        @contextmanager
        def fake_get_db():
            raise RuntimeError("connection refused")
            yield  # noqa: unreachable

        with patch("open_webui.main.get_db", fake_get_db):
            response = client.get("/health/db")

        assert response.status_code == 503
        assert response.json()["detail"] == "Database connection failed"


# ── /health/redis ─────────────────────────────────────────────────────────────


class TestHealthcheckRedis:
    def test_redis_locks_healthy(self, client):
        mock_metrics = {
            "circuit_state": "closed",
            "redis_initialized": True,
            "consecutive_failures": 0,
            "recovery_attempts": 0,
            "redis_url": "redis://secret:6379",
        }
        mock_manager = MagicMock()
        mock_manager.get_health_metrics.return_value = mock_metrics.copy()

        with (
            patch("open_webui.main.USE_REDIS_LOCKS", True),
            patch("open_webui.main.WEBSOCKET_MANAGER", "local"),
            patch(
                "open_webui.retrieval.vector.locks.get_collection_lock_manager",
                return_value=mock_manager,
            ),
        ):
            response = client.get("/health/redis")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] is True
        # redis_url must be stripped from the response
        assert "redis_url" not in body.get("locks", {})

    def test_redis_circuit_open_returns_503(self, client):
        mock_metrics = {
            "circuit_state": "open",
            "redis_initialized": True,
            "consecutive_failures": 5,
            "recovery_attempts": 3,
        }
        mock_manager = MagicMock()
        mock_manager.get_health_metrics.return_value = mock_metrics

        with (
            patch("open_webui.main.USE_REDIS_LOCKS", True),
            patch("open_webui.main.WEBSOCKET_MANAGER", "local"),
            patch(
                "open_webui.retrieval.vector.locks.get_collection_lock_manager",
                return_value=mock_manager,
            ),
        ):
            response = client.get("/health/redis")

        assert response.status_code == 503
        assert response.json()["detail"] == "Redis lock circuit open"

    def test_redis_not_initialized_returns_503(self, client):
        mock_metrics = {
            "circuit_state": "closed",
            "redis_initialized": False,
            "consecutive_failures": 0,
            "recovery_attempts": 0,
        }
        mock_manager = MagicMock()
        mock_manager.get_health_metrics.return_value = mock_metrics

        with (
            patch("open_webui.main.USE_REDIS_LOCKS", True),
            patch("open_webui.main.WEBSOCKET_MANAGER", "local"),
            patch(
                "open_webui.retrieval.vector.locks.get_collection_lock_manager",
                return_value=mock_manager,
            ),
        ):
            response = client.get("/health/redis")

        assert response.status_code == 503
        assert response.json()["detail"] == "Redis lock manager not initialized"

    def test_websocket_redis_healthy(self, client):
        mock_pool = MagicMock()
        mock_pool.redis.ping.return_value = True

        with (
            patch("open_webui.main.USE_REDIS_LOCKS", False),
            patch("open_webui.main.WEBSOCKET_MANAGER", "redis"),
            patch("open_webui.main.SESSION_POOL", mock_pool),
        ):
            response = client.get("/health/redis")

        assert response.status_code == 200
        assert response.json()["websocket"] is True

    def test_websocket_redis_failure_returns_503(self, client):
        mock_pool = MagicMock()
        mock_pool.redis.ping.side_effect = ConnectionError("Redis down")

        with (
            patch("open_webui.main.USE_REDIS_LOCKS", False),
            patch("open_webui.main.WEBSOCKET_MANAGER", "redis"),
            patch("open_webui.main.SESSION_POOL", mock_pool),
        ):
            response = client.get("/health/redis")

        assert response.status_code == 503
        assert response.json()["detail"] == "Websocket Redis connection failed"

    def test_no_redis_configured_returns_status_only(self, client):
        with (
            patch("open_webui.main.USE_REDIS_LOCKS", False),
            patch("open_webui.main.WEBSOCKET_MANAGER", "local"),
        ):
            response = client.get("/health/redis")

        assert response.status_code == 200
        assert response.json() == {"status": True}


# ── /health/ready ─────────────────────────────────────────────────────────────


class TestHealthcheckReady:
    def test_ready_all_healthy(self, client):
        mock_db = MagicMock()

        @contextmanager
        def fake_get_db():
            yield mock_db

        with (
            patch("open_webui.main.get_db", fake_get_db),
            patch("open_webui.main.USE_REDIS_LOCKS", False),
            patch("open_webui.main.WEBSOCKET_MANAGER", "local"),
        ):
            response = client.get("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] is True
        assert body["db"] is True
        assert "redis" not in body

    def test_ready_db_failure_returns_503(self, client):
        @contextmanager
        def fake_get_db():
            raise RuntimeError("connection refused")
            yield  # noqa: unreachable

        with (
            patch("open_webui.main.get_db", fake_get_db),
            patch("open_webui.main.USE_REDIS_LOCKS", False),
            patch("open_webui.main.WEBSOCKET_MANAGER", "local"),
        ):
            response = client.get("/health/ready")

        assert response.status_code == 503
        assert response.json()["detail"] == "Database connection failed"

    def test_ready_redis_disabled_returns_200_without_redis(self, client):
        mock_db = MagicMock()

        @contextmanager
        def fake_get_db():
            yield mock_db

        with (
            patch("open_webui.main.get_db", fake_get_db),
            patch("open_webui.main.USE_REDIS_LOCKS", False),
            patch("open_webui.main.WEBSOCKET_MANAGER", "local"),
        ):
            response = client.get("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert "redis" not in body

    def test_ready_redis_unhealthy_returns_503(self, client):
        mock_db = MagicMock()
        mock_pool = MagicMock()
        mock_pool.redis.ping.side_effect = ConnectionError("Redis down")

        @contextmanager
        def fake_get_db():
            yield mock_db

        with (
            patch("open_webui.main.get_db", fake_get_db),
            patch("open_webui.main.USE_REDIS_LOCKS", False),
            patch("open_webui.main.WEBSOCKET_MANAGER", "redis"),
            patch("open_webui.main.SESSION_POOL", mock_pool),
        ):
            response = client.get("/health/ready")

        assert response.status_code == 503
        assert response.json()["detail"] == "Redis health check failed"

    def test_ready_with_redis_healthy(self, client):
        mock_db = MagicMock()
        mock_pool = MagicMock()
        mock_pool.redis.ping.return_value = True

        @contextmanager
        def fake_get_db():
            yield mock_db

        with (
            patch("open_webui.main.get_db", fake_get_db),
            patch("open_webui.main.USE_REDIS_LOCKS", False),
            patch("open_webui.main.WEBSOCKET_MANAGER", "redis"),
            patch("open_webui.main.SESSION_POOL", mock_pool),
        ):
            response = client.get("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] is True
        assert body["db"] is True
        assert body["redis"]["status"] is True
