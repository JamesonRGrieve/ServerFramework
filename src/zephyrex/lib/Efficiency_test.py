"""MHz-normalized efficiency ratchets for framework hot paths.

CPU-bound benchmarks get 15% tolerance (tight — catches subtle regressions).
IO-bound benchmarks (DB creation, migrations, disk) get 50% (absorbs page
cache and disk variance while still catching 2x regressions).
Boot uses subprocess isolation (imports ARE the work).
"""

from __future__ import annotations

import os
from typing import Optional

import pytest
from pydantic import Field

from zephyrex.lib.EfficiencyRatchet import ratchet, ratchet_subprocess

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-32-bytes-or-more-aaaaaa")
os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("SEED_DATA", "false")

IO_TOLERANCE = 0.50
CPU_TOLERANCE = 0.15


def _worker_db_prefix(label: str) -> str:
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
    def test_instance_boot(self, tmp_path):
        ratchet_subprocess("boot", f"""
import os
os.environ["JWT_SECRET"] = "test-jwt-secret-32-bytes-or-more-aaaaaa"
os.environ["DATABASE_TYPE"] = "sqlite"
os.environ["DATABASE_NAME"] = "bench_boot"
os.environ["DATABASE_PATH"] = "{tmp_path}"
os.environ["SEED_DATA"] = "false"
from zephyrex.app import instance
_mhz_before = _read_mhz()
_t0 = time.perf_counter()
instance(extensions="", db_prefix="bench.boot")
_elapsed = time.perf_counter() - _t0
_mhz = (_mhz_before + _read_mhz()) / 2.0
""", tolerance=IO_TOLERANCE)


class TestModelRegistryPerformance:
    def test_registry_commit(self, tmp_path):
        os.environ["DATABASE_NAME"] = f"bench_registry_{os.getpid()}"
        os.environ["DATABASE_PATH"] = str(tmp_path)
        from zephyrex.database.DatabaseManager import DatabaseManager
        from zephyrex.lib.Pydantic import ModelRegistry

        def commit():
            _reset_registry()
            db_mgr = DatabaseManager(db_prefix=_worker_db_prefix("reg"))
            registry = ModelRegistry()
            registry.database_manager = db_mgr
            registry.commit()

        ratchet("registry_commit", commit, tolerance=IO_TOLERANCE)


class TestSQLAlchemyModelCreation:
    @pytest.mark.parametrize("count", [1, 10, 50])
    def test_create_models(self, count):
        from sqlalchemy.orm import DeclarativeBase
        from zephyrex.lib.Pydantic import ModelRegistry
        from zephyrex.lib.Pydantic2SQLAlchemy import ApplicationModel, create_sqlalchemy_model

        def create_all():
            base = type("BenchBase", (DeclarativeBase,), {})
            models = []
            for i in range(count):
                model = type(
                    f"BenchModel{i}", (ApplicationModel,),
                    {
                        "__annotations__": {"name": str, "value": Optional[int]},
                        "name": Field(..., description=f"Name {i}"),
                        "value": Field(None),
                    },
                )
                models.append(model)
            registry = ModelRegistry()
            for model in models:
                create_sqlalchemy_model(model, model_registry=registry, base_model=base)

        ratchet(f"create_models_{count}", create_all, tolerance=IO_TOLERANCE)


class TestRouteGenerationPerformance:
    def test_prefix_derivation(self):
        import stringcase
        from zephyrex.lib.Pydantic2SQLAlchemy import ApplicationModel
        from zephyrex.logic.AbstractLogicManager import AbstractBLLManager

        managers = []
        for i in range(100):
            model = type(f"PerfModel{i}", (ApplicationModel,), {"__annotations__": {"name": str}})
            mgr = type(f"PerfManager{i}", (AbstractBLLManager,), {"_model": model})
            managers.append(mgr)

        def derive():
            for mgr in managers:
                stringcase.snakecase(mgr.__name__.replace("Manager", ""))

        ratchet("prefix_derivation_100", derive, tolerance=CPU_TOLERANCE, iterations=100)


class TestRequestLatency:
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

    def test_openapi_generation(self, client):
        ratchet("openapi_generation", lambda: client.app.openapi(), tolerance=IO_TOLERANCE)

    def test_health_check(self, client):
        ratchet("health_check", lambda: client.get("/"), tolerance=CPU_TOLERANCE, iterations=10)
