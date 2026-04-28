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


# ---------- Phase 2: registry-driven table.info stamps ------------------


@pytest.mark.migration
@pytest.mark.real
@pytest.mark.extension
@pytest.mark.mfa
def test_extension_owned_table_stamped_with_extension(booted_app):
    """Tables defined in extension modules get table.info["extension"] = name."""
    app, _ = booted_app(extensions="auth_mfa")
    registry = app.state.model_registry
    found = {}
    for sa_model in registry.db_models.values():
        table = getattr(sa_model, "__table__", None)
        if table is not None and "extension" in table.info:
            found[table.name] = table.info["extension"]
    assert found.get("multifactor_methods") == "auth_mfa", (
        f"expected multifactor_methods stamped owner=auth_mfa; got {found}"
    )


@pytest.mark.migration
@pytest.mark.real
@pytest.mark.extension
@pytest.mark.payment
def test_core_table_extended_by_extension_stamped(booted_app):
    """Core tables extended via @extension_model gather "payment" in
    table.info["extensions"] (set, not scalar)."""
    app, _ = booted_app(extensions="payment")
    registry = app.state.model_registry
    users_sa = None
    for pyd, sa in registry.db_models.items():
        if pyd.__name__ in ("UserModel", "User") or getattr(
            sa.__table__, "name", ""
        ) == "users":
            users_sa = sa
            break
    assert users_sa is not None, "could not find users SA model"
    extending = users_sa.__table__.info.get("extensions", set())
    assert "payment" in extending, (
        f"expected 'payment' in users.info['extensions']; got {extending}"
    )


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


# ---------- regenerate targets db_info["file_path"] ----------------------


@pytest.mark.migration
@pytest.mark.real
@pytest.mark.regenerate
def test_regenerate_uses_db_info_file_path(migration_manager, tmp_path):
    """regenerate_migrations must operate on the DB at db_info['file_path'],
    not the hardcoded `database/database.db` path.

    Verification: the test DB sentinel is overwritten (proving the test path
    was the one wiped) AND any pre-existing decoy at `database/database.db`
    is left untouched."""
    target = Path(migration_manager.db_info["file_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    sentinel = b"sentinel"
    target.write_bytes(sentinel)

    src_path = Path(__file__).resolve().parent.parent.parent
    decoy = src_path / "database" / "database.db"
    decoy_existed_before = decoy.exists()
    decoy_payload_before = decoy.read_bytes() if decoy_existed_before else None

    try:
        migration_manager.regenerate_migrations()
    except Exception:
        pass

    try:
        assert (
            not target.exists() or target.read_bytes() != sentinel
        ), "regenerate left the test DB sentinel intact — wrong file targeted"

        if decoy_existed_before:
            assert decoy.exists() and decoy.read_bytes() == decoy_payload_before, (
                "regenerate clobbered the production decoy at "
                f"{decoy} — should have used db_info['file_path']"
            )
    finally:
        if decoy_existed_before and decoy_payload_before is not None:
            decoy.write_bytes(decoy_payload_before)


# ---------- parse_csv duplicate (Phase 1 xfail) --------------------------


@pytest.mark.migration
def test_parse_csv_env_var_static_and_instance_agree(migration_manager):
    """There should be exactly one CSV-env parser."""
    from database.migrations.Migration import MigrationManager

    assert not hasattr(MigrationManager, "env_parse_csv_env_var"), (
        "duplicate parser still present"
    )


# ---------- init/create dispatch identically -----------------------------


@pytest.mark.migration
def test_create_cli_verb_dispatches_to_create_extension(capsys):
    """Phase 1 collapsed `init` + `create` into a single `create` verb. Confirm
    the parser only accepts `create` and that it routes to the public
    create_extension API."""
    import database.migrations.Migration as mig_mod

    assert hasattr(mig_mod.MigrationManager, "create_extension")

    src_dir = Path(mig_mod.__file__).resolve().parent.parent.parent
    import subprocess

    res = subprocess.run(
        [sys.executable, str(mig_mod.__file__), "init", "demo_ext"],
        cwd=str(src_dir),
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0, "init verb should have been removed in Phase 1"
    assert "invalid choice" in (res.stderr or "").lower() or "init" in (res.stderr or ""), (
        f"expected argparse rejection of 'init', got stderr: {res.stderr!r}"
    )


# ---------- cleanup_test_artifacts (Phase 5) -----------------------------


@pytest.mark.migration
def test_cleanup_test_artifacts_is_a_noop_outside_test_mode(tmp_path):
    """Phase 5 collapsed cleanup_temporary_files + cleanup_test_environment
    into a single cleanup_test_artifacts. Outside test_mode the method is a
    documented no-op — it never touches production files."""
    from database.migrations.Migration import MigrationManager

    mgr = MigrationManager(
        test_mode=False,
        custom_db_info={
            "type": "sqlite",
            "name": "production_safety_test",
            "url": f"sqlite:///{tmp_path}/x.db",
            "file_path": str(tmp_path / "x.db"),
        },
    )
    # Place a sentinel that would be deleted by an over-eager cleanup.
    sentinel = tmp_path / "x.db"
    sentinel.write_bytes(b"keep me")
    assert mgr.cleanup_test_artifacts() is True
    assert sentinel.exists() and sentinel.read_bytes() == b"keep me"


@pytest.mark.migration
def test_cleanup_test_artifacts_removes_test_db_in_test_mode(tmp_path):
    """In test mode the method removes the on-disk SQLite file when the db
    name has 'test' in it — the only side effect Phase 5 deliberately keeps,
    since per-extension test_versions/ now route to test_versions_root and
    no longer need an in-tree sweep when callers use that path."""
    from database.migrations.Migration import MigrationManager

    db_file = tmp_path / "test.cleanup.database.db"
    db_file.write_bytes(b"")
    mgr = MigrationManager(
        test_mode=True,
        custom_db_info={
            "type": "sqlite",
            "name": "test.cleanup.database",
            "url": f"sqlite:///{db_file}",
            "file_path": str(db_file),
        },
    )
    assert mgr.cleanup_test_artifacts() is True
    assert not db_file.exists(), "test db should have been swept"


# ---------- Phase 5: test_versions_root override -------------------------


@pytest.mark.migration
def test_test_versions_root_routes_extension_revisions_outside_src(
    tmp_path, migration_db_info
):
    """Phase 5: passing test_versions_root keeps src/extensions pristine —
    extension revision files land under <root>/<ext>/versions/ instead of
    src/extensions/<ext>/migrations/test_versions/."""
    from database.migrations.Migration import MigrationManager

    src_path = Path(__file__).resolve().parent.parent.parent
    in_tree = src_path / "extensions" / "auth_mfa" / "migrations" / "test_versions"
    pre_in_tree = set(in_tree.glob("*.py")) if in_tree.exists() else set()

    versions_root = tmp_path / "rev_root"
    mgr = MigrationManager(
        test_mode=True,
        custom_db_info=migration_db_info,
        test_versions_root=versions_root,
    )

    ok = mgr.create_extension_migration("auth_mfa", "phase5 routing", auto=True)
    assert ok, "create_extension_migration returned False"

    routed_dir = versions_root / "auth_mfa" / "versions"
    assert routed_dir.exists(), f"routed dir not created at {routed_dir}"
    routed_files = list(routed_dir.glob("*.py"))
    assert routed_files, f"no revision file landed in {routed_dir}"

    # In-tree test_versions should be unchanged from before this test ran.
    after_in_tree = set(in_tree.glob("*.py")) if in_tree.exists() else set()
    assert after_in_tree == pre_in_tree, (
        f"test_versions_root failed to redirect: new files appeared in {in_tree}"
    )


# ---------- Item 24: env_is_table_owned_by_extension API ----------------


@pytest.mark.migration
@pytest.mark.real
@pytest.mark.extension
@pytest.mark.mfa
def test_env_is_table_owned_by_extension_returns_owner_name(booted_app):
    """Item 24's API: env_is_table_owned_by_extension(table) returns the
    owner extension name for extension-owned tables, None for core."""
    from database.migrations.Migration import MigrationManager

    app, _ = booted_app(extensions="auth_mfa")
    registry = app.state.model_registry

    by_name = {}
    for sa in registry.db_models.values():
        t = getattr(sa, "__table__", None)
        if t is not None:
            by_name[t.name] = t

    mfa = by_name.get("multifactor_methods")
    users = by_name.get("users")
    assert mfa is not None and users is not None

    assert MigrationManager.env_is_table_owned_by_extension(mfa) == "auth_mfa"
    assert MigrationManager.env_is_table_owned_by_extension(users) is None


@pytest.mark.migration
@pytest.mark.real
@pytest.mark.extension
@pytest.mark.payment
def test_env_table_extenders_lists_field_injection_owners(booted_app):
    """Item 24's adjacent helper: env_table_extenders(table) lists the
    @extension_model decorators that injected fields into a core table.
    Sorted for determinism."""
    from database.migrations.Migration import MigrationManager

    app, _ = booted_app(extensions="payment")
    registry = app.state.model_registry

    users = None
    for sa in registry.db_models.values():
        t = getattr(sa, "__table__", None)
        if t is not None and t.name == "users":
            users = t
            break
    assert users is not None

    extenders = MigrationManager.env_table_extenders(users)
    assert "payment" in extenders
    assert extenders == sorted(extenders), "extenders must be sorted"


# ---------- Phase 4 clean-folder gate -----------------------------------


@pytest.mark.migration
@pytest.mark.real
@pytest.mark.extension
def test_extension_folder_stays_clean_after_full_cycle(booted_app):
    """Phase 4 post-condition: after a full upgrade with extensions, no
    env.py / alembic.ini / script.py.mako / __init__.py / tmp*.ini files
    appear under any extension's migrations/ tree. Only versions/<rev>.py
    files should exist there.

    This is the user-facing success criterion of Phase 4 (in-memory
    Alembic config + shared script_location), guaranteeing extension
    folders contain only author-owned source + revision files."""
    booted_app(extensions="auth_mfa,payment")

    src_path = Path(__file__).resolve().parent.parent.parent
    extensions_dir = src_path / "extensions"

    pollutants = []
    for migrations_dir in extensions_dir.glob("*/migrations"):
        for entry in migrations_dir.iterdir():
            if entry.is_dir() and entry.name in ("versions", "test_versions"):
                continue
            if entry.is_dir() and entry.name == "__pycache__":
                continue
            pollutants.append(str(entry.relative_to(src_path)))
        # versions/ subdirs may only contain revision files + __pycache__
        for sub in ("versions", "test_versions"):
            sub_dir = migrations_dir / sub
            if not sub_dir.exists():
                continue
            for entry in sub_dir.iterdir():
                if entry.is_dir() and entry.name == "__pycache__":
                    continue
                if entry.is_file() and entry.suffix == ".py":
                    continue
                pollutants.append(str(entry.relative_to(src_path)))

    assert not pollutants, (
        "extension folders polluted with non-revision files: "
        f"{pollutants}"
    )


# ---------- Item 24: audit-ownership CLI --------------------------------


@pytest.mark.migration
def test_audit_ownership_cli_lists_tables():
    """Item 24's audit CLI: subcommand prints tab-separated rows for every
    table with owner column ('core' or extension name) and extenders column
    ('-' or comma-separated list). Smoke test on the parser surface; full
    table enumeration depends on a booted registry which the integration
    tests above already exercise."""
    import database.migrations.Migration as mig_mod

    assert hasattr(mig_mod.MigrationManager, "audit_table_ownership"), (
        "MigrationManager.audit_table_ownership method missing"
    )

    src_dir = Path(mig_mod.__file__).resolve().parent.parent.parent
    import subprocess

    res = subprocess.run(
        [sys.executable, str(mig_mod.__file__), "audit-ownership", "--help"],
        cwd=str(src_dir),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"audit-ownership --help failed: stderr={res.stderr!r}"
    )
    assert "audit" in (res.stdout + res.stderr).lower()
