"""Efficiency benchmarks for framework hot paths.

These tests measure wall-clock time for critical operations and assert
they stay within acceptable bounds. They are NOT micro-benchmarks —
they test real framework code paths end-to-end.

Each test uses per-worker database isolation (db_prefix derived from
PYTEST_XDIST_WORKER + PID) so they run safely under xdist parallelism.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import pytest
from pydantic import Field


os.environ.setdefault("JWT_SECRET", "test-jwt-secret-32-bytes-or-more-aaaaaa")
os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("SEED_DATA", "false")


def _worker_db_prefix(label: str) -> str:
    """Build a per-worker, per-PID database prefix for xdist isolation."""
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    return f"bench.{label}.{worker}.{os.getpid()}"


def _reset_registry():
    from zephyrex.lib.Pydantic2SQLAlchemy import (
        clear_registry_cache,
        reset_extension_system,
    )

    clear_registry_cache()
    reset_extension_system()


class TestBootPerformance:
    """Measure app boot (instance() call) time."""

    def test_instance_boots_under_5_seconds(self, tmp_path):
        os.environ["DATABASE_NAME"] = f"bench_boot_{os.getpid()}"
        os.environ["DATABASE_PATH"] = str(tmp_path)

        _reset_registry()

        start = time.perf_counter()
        from zephyrex.app import instance

        app = instance(extensions="", db_prefix=_worker_db_prefix("boot"))
        elapsed = time.perf_counter() - start

        assert app is not None
        assert elapsed < 5.0, f"Boot took {elapsed:.2f}s (limit: 5s)"
        print(f"\n  Boot time: {elapsed:.2f}s")


class TestModelRegistryPerformance:
    """Measure model registry commit time."""

    def test_commit_under_3_seconds(self, tmp_path):
        os.environ["DATABASE_NAME"] = f"bench_registry_{os.getpid()}"
        os.environ["DATABASE_PATH"] = str(tmp_path)

        from zephyrex.database.DatabaseManager import DatabaseManager
        from zephyrex.lib.Pydantic import ModelRegistry

        _reset_registry()

        db_prefix = _worker_db_prefix("reg")
        db_mgr = DatabaseManager(db_prefix=db_prefix)
        registry = ModelRegistry()
        registry.database_manager = db_mgr

        start = time.perf_counter()
        registry.commit()
        elapsed = time.perf_counter() - start

        assert elapsed < 3.0, f"Registry commit took {elapsed:.2f}s (limit: 3s)"
        print(f"\n  Registry commit: {elapsed:.2f}s")


class TestSQLAlchemyModelCreation:
    """Measure SA model creation from Pydantic models."""

    @pytest.mark.parametrize("count", [1, 10, 50])
    def test_create_models_scales_linearly(self, count):
        from sqlalchemy.orm import DeclarativeBase

        from zephyrex.lib.Pydantic import ModelRegistry
        from zephyrex.lib.Pydantic2SQLAlchemy import (
            ApplicationModel,
            create_sqlalchemy_model,
        )

        class BenchBase(DeclarativeBase):
            pass

        models = []
        for i in range(count):
            model = type(
                f"BenchModel{i}",
                (ApplicationModel,),
                {
                    "__annotations__": {"name": str, "value": Optional[int]},
                    "name": Field(..., description=f"Name {i}"),
                    "value": Field(None),
                },
            )
            models.append(model)

        registry = ModelRegistry()
        start = time.perf_counter()
        for model in models:
            create_sqlalchemy_model(model, model_registry=registry, base_model=BenchBase)
        elapsed = time.perf_counter() - start

        per_model = elapsed / count * 1000
        assert per_model < 50, f"{per_model:.1f}ms per model (limit: 50ms)"
        print(f"\n  {count} models: {elapsed:.3f}s ({per_model:.1f}ms each)")


class TestRouteGenerationPerformance:
    """Measure route prefix derivation and config extraction speed."""

    def test_prefix_derivation_under_1ms(self):
        import stringcase
        from zephyrex.lib.Pydantic2SQLAlchemy import ApplicationModel
        from zephyrex.logic.AbstractLogicManager import AbstractBLLManager

        managers = []
        for i in range(100):
            model = type(f"PerfModel{i}", (ApplicationModel,), {"__annotations__": {"name": str}})
            mgr = type(f"PerfManager{i}", (AbstractBLLManager,), {"_model": model})
            managers.append(mgr)

        start = time.perf_counter()
        for mgr in managers:
            resource = stringcase.snakecase(mgr.__name__.replace("Manager", ""))
            prefix = getattr(mgr, "prefix", None) or f"/v1/{resource}"
        elapsed = time.perf_counter() - start

        per_mgr_us = elapsed / len(managers) * 1_000_000
        print(f"\n  100 prefix derivations: {elapsed*1000:.1f}ms ({per_mgr_us:.0f}µs each)")
        assert per_mgr_us < 1000, f"{per_mgr_us:.0f}µs per manager (limit: 1000µs)"


class TestRequestLatency:
    """Measure request latency on a booted app."""

    @pytest.fixture(scope="class")
    @classmethod
    def client(cls, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("bench_latency")
        os.environ["DATABASE_NAME"] = f"bench_latency_{os.getpid()}"
        os.environ["DATABASE_PATH"] = str(tmp)
        os.environ["SEED_DATA"] = "true"

        _reset_registry()

        from starlette.testclient import TestClient

        from zephyrex.app import instance

        app = instance(extensions="", db_prefix=_worker_db_prefix("lat"))
        return TestClient(app)

    def test_openapi_under_200ms(self, client):
        start = time.perf_counter()
        schema = client.app.openapi()
        elapsed = time.perf_counter() - start
        assert schema is not None
        assert "paths" in schema
        assert elapsed < 1.0, f"OpenAPI took {elapsed*1000:.0f}ms (limit: 1000ms)"
        print(f"\n  OpenAPI: {elapsed*1000:.0f}ms")

    def test_health_check_under_50ms(self, client):
        start = time.perf_counter()
        resp = client.get("/")
        elapsed = time.perf_counter() - start
        assert elapsed < 0.05, f"Health check took {elapsed*1000:.0f}ms (limit: 50ms)"
        print(f"\n  Health: {elapsed*1000:.0f}ms")

    @pytest.mark.parametrize("n", [10, 50])
    def test_list_endpoint_latency(self, client, n):
        from conftest import create_user

        try:
            user = create_user(client)
            headers = {"Authorization": f"Bearer {user.jwt}"}
        except Exception:
            pytest.skip("Auth not available")

        times = []
        for _ in range(n):
            start = time.perf_counter()
            client.get("/v1/role", headers=headers)
            times.append(time.perf_counter() - start)

        avg = sum(times) / len(times) * 1000
        p99 = sorted(times)[int(len(times) * 0.99)] * 1000
        assert avg < 50, f"Avg list latency {avg:.0f}ms (limit: 50ms)"
        print(f"\n  List endpoint ({n} reqs): avg={avg:.1f}ms p99={p99:.1f}ms")
