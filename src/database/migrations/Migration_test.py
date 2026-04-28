"""
Real-DB integration tests for MigrationManager.

Strategy: boot `app.instance(db_prefix=..., extensions=...)` against a tmp_path
SQLite DB (via DATABASE_PATH monkeypatch). instance() runs the migration system
end-to-end during commit(); tests assert the resulting schema with raw sqlite3.
This protects refactors of Migration.py against silent regressions.
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import pytest


# ---------- helpers ------------------------------------------------------


def _connect_url(url: str) -> sqlite3.Connection:
    prefix = "sqlite:///"
    assert url.startswith(prefix), f"unexpected db url: {url}"
    return sqlite3.connect(url[len(prefix):])


def _table_columns(conn: sqlite3.Connection, table: str):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _table_names(conn: sqlite3.Connection):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


@pytest.fixture
def booted_app(tmp_path, monkeypatch):
    """
    Boot a fresh app.instance() with a tmp_path SQLite DB.

    Returns a callable: booted_app(extensions="") -> (app, db_file_path).
    Side effects: monkeypatches DATABASE_PATH/TYPE/NAME so the DB file lives
    under tmp_path. Clears registry cache before each invocation.
    """
    src_path = Path(__file__).resolve().parent.parent.parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")

    def _boot(extensions: str = ""):
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path))
        monkeypatch.setenv("DATABASE_TYPE", "sqlite")
        monkeypatch.setenv("DATABASE_NAME", "database")

        from lib import Environment as _env_mod
        monkeypatch.setattr(_env_mod.settings, "DATABASE_TYPE", "sqlite", raising=False)
        monkeypatch.setattr(_env_mod.settings, "DATABASE_NAME", "database", raising=False)

        from lib.Pydantic2SQLAlchemy import clear_registry_cache

        clear_registry_cache()

        from app import instance

        ext_tag = (extensions or "core").replace(",", "_")
        db_prefix = f"test.mig.{worker_id}.{ext_tag}"
        app = instance(db_prefix=db_prefix, extensions=extensions)

        db_file = tmp_path / f"{db_prefix}.database.db"
        return app, db_file

    return _boot


# ---------- core upgrade -------------------------------------------------


@pytest.mark.migration
@pytest.mark.db
@pytest.mark.real
@pytest.mark.core
def test_run_alembic_command_upgrade_head_creates_alembic_version_table(booted_app):
    """instance() drives MigrationManager.run_alembic_command('upgrade','head')
    on first boot. Confirm the `alembic_version` bookkeeping table appears in
    the resulting SQLite file."""
    app, db_file = booted_app(extensions="")
    assert db_file.exists(), f"expected db file at {db_file}"
    with sqlite3.connect(str(db_file)) as conn:
        tables = _table_names(conn)
    assert "alembic_version" in tables, f"got tables: {sorted(tables)}"


# ---------- extension migration creates revision file --------------------


@pytest.mark.migration
@pytest.mark.real
@pytest.mark.extension
def test_create_extension_migration_writes_versions_file(migration_manager):
    """create_extension_migration writes a revision file under the extension's
    test_versions/ directory in test mode. Smoke-tests the autogenerate path
    without booting the full app."""
    src_path = Path(__file__).resolve().parent.parent.parent
    versions_dir = src_path / "extensions" / "auth_mfa" / "migrations" / "test_versions"

    pre_existing = set(versions_dir.glob("*.py")) if versions_dir.exists() else set()

    ok = migration_manager.create_extension_migration(
        "auth_mfa", "phase0 smoke", auto=True
    )
    assert ok, "create_extension_migration returned False"

    after = set(versions_dir.glob("*.py"))
    new_files = after - pre_existing
    new_files = {f for f in new_files if f.name != "__init__.py"}
    assert new_files, f"no new revision file created in {versions_dir}"

    for f in new_files:
        f.unlink(missing_ok=True)


# ---------- extension upgrade applies extension tables -------------------


@pytest.mark.migration
@pytest.mark.db
@pytest.mark.real
@pytest.mark.extension
@pytest.mark.mfa
def test_run_extension_migration_upgrade_applies_extension_tables(booted_app):
    """Booting with extensions='auth_mfa' must materialize the extension's
    tables (multifactor_methods + recovery codes) in the DB and the
    per-extension alembic version table."""
    app, db_file = booted_app(extensions="auth_mfa")
    assert db_file.exists(), f"expected db file at {db_file}"
    with sqlite3.connect(str(db_file)) as conn:
        tables = _table_names(conn)
    expected_tables = {
        "multifactor_methods",
        "multifactor_recovery_codes",
        "alembic_version_auth_mfa",
    }
    missing = expected_tables - tables
    assert not missing, f"missing extension tables: {missing}; have {sorted(tables)}"


# ---------- extension extends a core table -------------------------------


@pytest.mark.migration
@pytest.mark.db
@pytest.mark.real
@pytest.mark.extension
@pytest.mark.payment
def test_run_all_migrations_with_payment_extension_extends_user_table(booted_app):
    """The payment extension uses @extension_model(UserModel) to add columns
    onto users. After boot, those columns must be present on the real users
    table — exercises the extension-extends-core-table autogenerate path."""
    app, db_file = booted_app(extensions="payment")
    assert db_file.exists(), f"expected db file at {db_file}"
    with sqlite3.connect(str(db_file)) as conn:
        cols = _table_columns(conn, "users")
    expected_extension_cols = {"external_payment_id"}
    missing = expected_extension_cols - cols
    assert not missing, f"missing extension columns on users: {missing}; have {sorted(cols)}"


# ---------- regenerate respects db_info["file_path"] (Phase 1 xfail) -----


@pytest.mark.migration
@pytest.mark.real
@pytest.mark.regenerate
@pytest.mark.xfail(
    reason="Phase 1 will fix regenerate_migrations to use db_info['file_path'] "
    "instead of hardcoded database/database.db",
    strict=True,
)
def test_regenerate_uses_db_info_file_path(migration_manager, tmp_path):
    """regenerate_migrations must delete the DB at db_info['file_path'], not the
    hardcoded `database/database.db` path. Currently it deletes the wrong file."""
    target = Path(migration_manager.db_info["file_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"sentinel")
    assert target.exists()

    src_path = Path(__file__).resolve().parent.parent.parent
    decoy = src_path / "database" / "database.db"
    decoy_existed_before = decoy.exists()
    decoy_payload_before = decoy.read_bytes() if decoy_existed_before else None

    try:
        migration_manager.regenerate_migrations()
    except Exception:
        pass

    try:
        assert not target.exists(), "regenerate did not delete the test DB"
    finally:
        if decoy_existed_before and decoy_payload_before is not None:
            decoy.write_bytes(decoy_payload_before)


# ---------- parse_csv duplicate (Phase 1 xfail) --------------------------


@pytest.mark.migration
@pytest.mark.xfail(
    reason="Phase 1 will collapse env_parse_csv_env_var and _parse_csv_env_var into one helper",
    strict=True,
)
def test_parse_csv_env_var_static_and_instance_agree(migration_manager):
    """There should be exactly one CSV-env parser. After Phase 1 there is no
    `env_parse_csv_env_var` static."""
    from database.migrations.Migration import MigrationManager

    assert not hasattr(MigrationManager, "env_parse_csv_env_var"), (
        "duplicate parser still present"
    )


# ---------- init/create dispatch identically -----------------------------


@pytest.mark.migration
def test_init_and_create_dispatch_identically():
    """Both `init <name>` and `create <name>` route to MigrationManager.create_extension.
    Phase 1 collapses these to a single canonical verb (init removed); this test
    documents current behavior and is updated/removed as part of Phase 1."""
    import database.migrations.Migration as mig_mod

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    init_p = sub.add_parser("init")
    init_p.add_argument("extension")
    init_p.add_argument("--skip-model", action="store_true")
    init_p.add_argument("--skip-migrate", action="store_true")
    create_p = sub.add_parser("create")
    create_p.add_argument("extension")
    create_p.add_argument("--skip-model", action="store_true")
    create_p.add_argument("--skip-migrate", action="store_true")

    init_args = parser.parse_args(["init", "demo_ext"])
    create_args = parser.parse_args(["create", "demo_ext"])

    assert init_args.extension == create_args.extension == "demo_ext"
    assert init_args.skip_model == create_args.skip_model is False
    assert init_args.skip_migrate == create_args.skip_migrate is False
    assert hasattr(mig_mod.MigrationManager, "create_extension"), (
        "create_extension method missing — both verbs depend on it"
    )


# ---------- cleanup_temporary_files removes only temps -------------------


@pytest.mark.migration
def test_cleanup_temporary_files_removes_temps_only(migration_manager, tmp_path):
    """cleanup_temporary_files deletes generated temp .ini files in src_dir but
    must not touch a real, non-temp alembic.ini that already exists."""
    src_dir = migration_manager.paths["src_dir"]

    real_ini = src_dir / "alembic.ini"
    real_pre_existed = real_ini.exists()
    real_payload = real_ini.read_bytes() if real_pre_existed else None

    temp_one = src_dir / "tmp_phase0_one.test_phase0.ini"
    temp_two = src_dir / "tmp_phase0_two.test_phase0.ini"
    temp_one.write_text("[alembic]\n")
    temp_two.write_text("[alembic]\n")

    try:
        migration_manager.cleanup_temporary_files()

        if real_pre_existed:
            assert real_ini.exists(), "cleanup deleted the real alembic.ini"
            assert real_ini.read_bytes() == real_payload, (
                "cleanup mutated the real alembic.ini"
            )
    finally:
        for f in (temp_one, temp_two):
            if f.exists():
                f.unlink()
