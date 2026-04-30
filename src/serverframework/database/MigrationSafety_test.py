"""Tests for ``database.MigrationSafety`` (Item 80).

Pure-utility tests; no DB or Alembic runtime is required. Synthetic
"migration modules" are SimpleNamespace objects whose ``upgrade`` is a real
function so :func:`inspect.getsource` can extract its source.
"""

from __future__ import annotations

import textwrap
import types

import pytest

from serverframework.database.MigrationSafety import (
    GeneratedMigration,
    MigrationGenerator,
    MigrationSafetyError,
    analyze_migration,
    contract_phase,
    expand_phase,
    phase_of,
    validate_migration_safety,
)


def _module_with_upgrade_source(
    source: str, *, revision: str = "rev1", down_revision: str = None
) -> types.ModuleType:
    """Build a real module whose ``upgrade`` source is ``source``.

    A real module is required because :func:`inspect.getsource` walks
    ``__loader__`` / ``__file__``; building a synthetic module via ``exec``
    sidesteps the file lookup by storing the source on the module as a
    fallback. This helper does the latter.
    """
    mod = types.ModuleType(f"_synth_migration_{revision}")
    mod.revision = revision
    mod.down_revision = down_revision
    full = textwrap.dedent(
        f"""
        from alembic import op
        import sqlalchemy as sa

        def upgrade():
        {textwrap.indent(textwrap.dedent(source), "            ")}

        def downgrade():
            pass
        """
    )
    # Compile and execute into the module's namespace; record the source on
    # the module so analyze_migration's getsource fallback finds it.
    code = compile(full, f"<synth-{revision}>", "exec")
    exec(code, mod.__dict__)  # noqa: S102 - test-only
    # Stash source on the upgrade function for inspect.getsource fallback.
    mod._source_blob = full
    # Patch inspect.getsource lookup by attaching the source as the function's
    # ``__source__`` and overriding via a wrapper. Simpler: just inline the
    # source into a closure-free function with linecache.
    import linecache

    linecache.cache[f"<synth-{revision}>"] = (
        len(full),
        None,
        full.splitlines(keepends=True),
        f"<synth-{revision}>",
    )
    return mod


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_expand_phase_generates_expand_suffix():
    assert expand_phase("base42") == "base42_expand"
    assert expand_phase("base42_expand") == "base42_expand"
    assert expand_phase("base42_contract") == "base42_expand"


@pytest.mark.unit
def test_contract_phase_generates_contract_suffix():
    assert contract_phase("base42") == "base42_contract"
    assert contract_phase("base42_expand") == "base42_contract"
    assert contract_phase("base42_contract") == "base42_contract"


@pytest.mark.unit
def test_phase_of():
    assert phase_of("foo_expand") == "expand"
    assert phase_of("foo_contract") == "contract"
    assert phase_of("foo") is None


# ---------------------------------------------------------------------------
# Rule 1: NOT NULL column adds without server_default
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_rejects_not_null_add_without_default():
    mod = _module_with_upgrade_source(
        """
        op.add_column('users', sa.Column('email_status', sa.String(), nullable=False))
        """,
        revision="r1",
    )
    with pytest.raises(MigrationSafetyError) as exc:
        validate_migration_safety(mod)
    assert "not_null_without_default" in str(exc.value)


@pytest.mark.unit
def test_validate_accepts_not_null_add_with_server_default():
    mod = _module_with_upgrade_source(
        """
        op.add_column(
            'users',
            sa.Column('email_status', sa.String(), nullable=False, server_default='pending'),
        )
        """,
        revision="r2",
    )
    validate_migration_safety(mod)  # does not raise


@pytest.mark.unit
def test_validate_accepts_nullable_add():
    mod = _module_with_upgrade_source(
        """
        op.add_column('users', sa.Column('display_name', sa.String(), nullable=True))
        """,
        revision="r3",
    )
    validate_migration_safety(mod)  # does not raise


# ---------------------------------------------------------------------------
# Rule 2: column drops require paired expand in history
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_rejects_drop_without_paired_expand_when_history_provided():
    mod = _module_with_upgrade_source(
        """
        op.drop_column('users', 'legacy_flag')
        """,
        revision="r4_contract",
    )
    # Empty history => no expand exists => reject.
    with pytest.raises(MigrationSafetyError) as exc:
        validate_migration_safety(mod, history=[])
    assert "drop_without_paired_expand" in str(exc.value)


@pytest.mark.unit
def test_validate_accepts_drop_when_paired_expand_in_history():
    expand_mod = types.SimpleNamespace(revision="r4_expand")
    mod = _module_with_upgrade_source(
        """
        op.drop_column('users', 'legacy_flag')
        """,
        revision="r4_contract",
    )
    validate_migration_safety(mod, history=[expand_mod])  # does not raise


@pytest.mark.unit
def test_validate_drop_without_history_does_not_check_pairing():
    """Without history, the pairing rule cannot be enforced; it is skipped."""
    mod = _module_with_upgrade_source(
        """
        op.drop_column('users', 'legacy_flag')
        """,
        revision="r4_contract",
    )
    # No history => rule skips => clean.
    validate_migration_safety(mod)


# ---------------------------------------------------------------------------
# Rule 3: FK adds with nullable=False require paired expand
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_rejects_fk_not_null_without_paired_expand():
    mod = _module_with_upgrade_source(
        """
        op.create_foreign_key(
            'fk_users_org',
            'users',
            'orgs',
            ['org_id'],
            ['id'],
            nullable=False,
        )
        """,
        revision="r5_contract",
    )
    with pytest.raises(MigrationSafetyError) as exc:
        validate_migration_safety(mod, history=[])
    assert "fk_not_null_without_paired_expand" in str(exc.value)


@pytest.mark.unit
def test_validate_accepts_nullable_fk_add():
    mod = _module_with_upgrade_source(
        """
        op.create_foreign_key(
            'fk_users_org',
            'users',
            'orgs',
            ['org_id'],
            ['id'],
        )
        """,
        revision="r6",
    )
    validate_migration_safety(mod, history=[])  # does not raise (nullable by default)


# ---------------------------------------------------------------------------
# analyze_migration returns structured findings
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_analyze_migration_returns_findings_without_raising():
    mod = _module_with_upgrade_source(
        """
        op.add_column('users', sa.Column('a', sa.String(), nullable=False))
        op.add_column('users', sa.Column('b', sa.String(), nullable=True))
        """,
        revision="r7",
    )
    analysis = analyze_migration(mod)
    assert len(analysis.findings) == 1
    assert analysis.findings[0].rule == "not_null_without_default"
    # Two add_column calls observed.
    assert len(analysis.add_columns) == 2


@pytest.mark.unit
def test_analyze_clean_migration_has_no_findings():
    mod = _module_with_upgrade_source(
        """
        op.add_column('users', sa.Column('display', sa.String(), nullable=True))
        """,
        revision="r8",
    )
    analysis = analyze_migration(mod)
    assert analysis.is_clean()
    assert analysis.findings == []


# ---------------------------------------------------------------------------
# MigrationGenerator
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_generator_emits_paired_expand_and_contract_for_drop():
    gen = MigrationGenerator()
    expand, contract = gen.emit_pair_for_drop(
        base_id="abc1234", table="users", column="legacy_flag"
    )
    assert isinstance(expand, GeneratedMigration)
    assert isinstance(contract, GeneratedMigration)
    assert expand.migration_id == "abc1234_expand"
    assert expand.phase == "expand"
    assert contract.migration_id == "abc1234_contract"
    assert contract.phase == "contract"
    assert "legacy_flag" in expand.source
    assert 'op.drop_column("users", "legacy_flag")' in contract.source
    assert "down_revision = 'abc1234_expand'" in contract.source


@pytest.mark.unit
def test_generator_emit_expand_only():
    gen = MigrationGenerator()
    expand = gen.emit_expand_for_drop("xyz", "t", "c")
    assert expand.phase == "expand"
    assert "no structural change" in expand.source


@pytest.mark.unit
def test_generator_emit_contract_only_with_explicit_expand_id():
    gen = MigrationGenerator()
    contract = gen.emit_contract_for_drop(
        "xyz", "t", "c", expand_revision="my_expand"
    )
    assert "down_revision = 'my_expand'" in contract.source
    assert 'op.drop_column("t", "c")' in contract.source


# ---------------------------------------------------------------------------
# Integration: an emitted contract has the right shape to satisfy the validator
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_emitted_contract_passes_validator_when_expand_in_history():
    gen = MigrationGenerator()
    expand, contract = gen.emit_pair_for_drop(
        base_id="abc", table="t", column="c"
    )
    # Build a module whose ``upgrade`` matches the contract's drop_column.
    mod = _module_with_upgrade_source(
        """
        op.drop_column('t', 'c')
        """,
        revision=contract.migration_id,
    )
    expand_mod = types.SimpleNamespace(revision=expand.migration_id)
    validate_migration_safety(mod, history=[expand_mod])  # does not raise
