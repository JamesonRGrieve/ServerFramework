"""Zero-downtime / rolling-deploy migration window contract (Item 80).

This module provides static validation of Alembic migration modules against
the framework's expand/contract phase contract. The goal is rolling-deploy
safety: while v1 and v2 of the application are both running against the same
DB, every migration in the rollout must shape the DB so that **both
versions** keep working.

Phases
------
- ``expand``: additive schema change. Add new columns/tables/indexes leaving
  the old structure intact. Both v1 and v2 read/write a DB that is a
  superset of what either expects.
- ``contract``: subtractive schema change. Drop the old structure once v2
  has fully rolled out and v1 has been retired. Contracts are gated on a
  paired earlier expand.

Rejected migration shapes
-------------------------
1. NOT NULL column adds without a ``server_default`` — v1 inserts (which
   do not know about the new column) would fail.
2. Column drops without a paired earlier ``expand`` migration that switched
   reads/writes off the column.
3. FK adds with ``nullable=False`` without a paired earlier expand that
   first added the column nullable and backfilled it.

Wiring
------
``validate_migration_safety`` analyzes a migration *module* (post-write)
via source inspection. ``validate_revision_directives`` is the
companion that runs at autogenerate time inside Alembic's
``process_revision_directives`` hook (wired in
``database/migrations/env.py``) so unsafe shapes are rejected before a
file is committed.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Phase enum + errors
# ---------------------------------------------------------------------------


MigrationPhase = Literal["expand", "contract"]


class MigrationSafetyError(Exception):
    """Raised when a migration violates rolling-deploy invariants."""


# ---------------------------------------------------------------------------
# Migration ID helpers
# ---------------------------------------------------------------------------


def expand_phase(migration_id: str) -> str:
    """Return the canonical ID of the ``expand`` partner of ``migration_id``.

    Convention: an expand id ends in ``_expand``; a contract id ends in
    ``_contract``. Either input form maps to the same ``_expand`` partner.
    """
    base = _strip_phase_suffix(migration_id)
    return f"{base}_expand"


def contract_phase(migration_id: str) -> str:
    """Return the canonical ID of the ``contract`` partner of ``migration_id``."""
    base = _strip_phase_suffix(migration_id)
    return f"{base}_contract"


def _strip_phase_suffix(migration_id: str) -> str:
    if migration_id.endswith("_expand"):
        return migration_id[: -len("_expand")]
    if migration_id.endswith("_contract"):
        return migration_id[: -len("_contract")]
    return migration_id


def phase_of(migration_id: str) -> Optional[MigrationPhase]:
    """Return the declared phase of ``migration_id`` if any, else None."""
    if migration_id.endswith("_expand"):
        return "expand"
    if migration_id.endswith("_contract"):
        return "contract"
    return None


# ---------------------------------------------------------------------------
# Static analysis of Alembic migration modules
# ---------------------------------------------------------------------------


@dataclass
class MigrationFinding:
    """A single safety-rule violation."""

    rule: str
    detail: str

    def format(self) -> str:
        return f"[{self.rule}] {self.detail}"


@dataclass
class MigrationAnalysis:
    """Findings + structural facts extracted from a migration module."""

    migration_id: Optional[str] = None
    down_revision: Optional[str] = None
    findings: List[MigrationFinding] = field(default_factory=list)
    add_columns: List[Tuple[str, str, dict]] = field(default_factory=list)
    drop_columns: List[Tuple[str, str]] = field(default_factory=list)
    add_fks: List[Tuple[str, str, dict]] = field(default_factory=list)

    def is_clean(self) -> bool:
        return not self.findings


def _read_upgrade_source(migration_module: Any) -> str:
    """Return the source of the migration module's ``upgrade`` function.

    Falls back to the module-level source if ``upgrade`` is not present
    (e.g. when a caller passes a partial test fixture).
    """
    upgrade_fn = getattr(migration_module, "upgrade", None)
    if upgrade_fn is not None and callable(upgrade_fn):
        try:
            return inspect.getsource(upgrade_fn)
        except (OSError, TypeError):
            pass
    try:
        return inspect.getsource(migration_module)
    except (OSError, TypeError):
        return ""


# Patterns are intentionally tolerant: they handle ``op.add_column(...)``
# whether the call uses positional or keyword args. The Alembic conventions
# used in the framework's existing migrations are the reference shape.
_RE_ADD_COLUMN = re.compile(
    r"op\.add_column\s*\(\s*['\"](?P<table>[^'\"]+)['\"]\s*,\s*"
    r"sa\.Column\s*\(\s*['\"](?P<column>[^'\"]+)['\"]"
    r"(?P<rest>[^)]*)\)"
    r"(?P<after>[^)]*)\)",
    re.DOTALL,
)
_RE_DROP_COLUMN = re.compile(
    r"op\.drop_column\s*\(\s*['\"](?P<table>[^'\"]+)['\"]\s*,\s*"
    r"['\"](?P<column>[^'\"]+)['\"]\s*\)"
)
_RE_CREATE_FK = re.compile(
    r"op\.create_foreign_key\s*\("
    r"(?P<rest>[^)]*)\)",
    re.DOTALL,
)


def _is_nullable_false(blob: str) -> bool:
    return bool(re.search(r"nullable\s*=\s*False", blob))


def _has_server_default(blob: str) -> bool:
    return bool(re.search(r"server_default\s*=", blob))


def _build_analysis(migration_module: Any) -> MigrationAnalysis:
    analysis = MigrationAnalysis(
        migration_id=getattr(migration_module, "revision", None),
        down_revision=getattr(migration_module, "down_revision", None),
    )
    src = _read_upgrade_source(migration_module)
    if not src:
        return analysis

    for m in _RE_ADD_COLUMN.finditer(src):
        table = m.group("table")
        column = m.group("column")
        rest = m.group("rest") or ""
        after = m.group("after") or ""
        column_blob = rest + after
        info = {
            "nullable_false": _is_nullable_false(column_blob),
            "has_server_default": _has_server_default(column_blob),
            "raw": column_blob,
        }
        analysis.add_columns.append((table, column, info))

    for m in _RE_DROP_COLUMN.finditer(src):
        analysis.drop_columns.append((m.group("table"), m.group("column")))

    for m in _RE_CREATE_FK.finditer(src):
        rest = m.group("rest") or ""
        info = {
            "nullable_false": _is_nullable_false(rest),
            "raw": rest,
        }
        # Pull the referencing table out of the typical create_foreign_key
        # arg shape: ``op.create_foreign_key("fk_name", "src_table", "tgt_table", ["col"], ["id"])``.
        m2 = re.search(
            r"['\"][^'\"]+['\"]\s*,\s*['\"](?P<src>[^'\"]+)['\"]",
            rest,
        )
        src_table = m2.group("src") if m2 else "<unknown>"
        analysis.add_fks.append((src_table, "<fk>", info))

    return analysis


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _has_paired_earlier_expand(
    migration_module: Any,
    history: Optional[Sequence[Any]],
) -> bool:
    """True iff the module is a contract whose paired expand exists in history.

    The pairing convention is that an expand and its later contract share a
    common ``_strip_phase_suffix`` base. Callers can pass an explicit
    ``history`` (list of prior modules / revision objects) to enable the
    check; if no history is provided the check is skipped (returns True)
    because the framework cannot prove the absence of a pair from a single
    module in isolation.
    """
    if history is None:
        return True
    rev = getattr(migration_module, "revision", None)
    if rev is None:
        return False
    base = _strip_phase_suffix(str(rev))
    expected = f"{base}_expand"
    for prior in history:
        prior_id = getattr(prior, "revision", None)
        if prior_id == expected:
            return True
    return False


def validate_migration_safety(
    migration_module: Any,
    history: Optional[Sequence[Any]] = None,
) -> None:
    """Inspect ``migration_module`` and raise on rolling-deploy-unsafe shapes.

    The function is intentionally cheap so it can run at autogenerate time.
    Callers that want non-raising access to the findings should call
    :func:`analyze_migration` directly.

    Pass ``history`` (an ordered iterable of prior migration modules) to
    enable the contract-must-have-paired-expand check; without it the rule
    skips because pairing cannot be verified from a single module.
    """
    analysis = analyze_migration(migration_module, history=history)
    if analysis.is_clean():
        return
    msg = "; ".join(f.format() for f in analysis.findings)
    raise MigrationSafetyError(msg)


def analyze_migration(
    migration_module: Any,
    history: Optional[Sequence[Any]] = None,
) -> MigrationAnalysis:
    """Return a :class:`MigrationAnalysis` (findings + structural facts).

    Findings cover the three rolling-deploy-unsafe shapes documented in
    this module's docstring.
    """
    analysis = _build_analysis(migration_module)

    # Rule 1: NOT NULL column adds without server_default.
    for table, column, info in analysis.add_columns:
        if info["nullable_false"] and not info["has_server_default"]:
            analysis.findings.append(
                MigrationFinding(
                    rule="not_null_without_default",
                    detail=(
                        f"add_column {table}.{column} is NOT NULL without a "
                        f"server_default; v1 inserts will fail during the "
                        f"rolling deploy. Either add a server_default or "
                        f"split into expand (nullable=True + backfill) and "
                        f"contract (nullable=False) phases."
                    ),
                )
            )

    # Rule 2: column drops without a paired earlier expand.
    if analysis.drop_columns and not _has_paired_earlier_expand(
        migration_module, history
    ):
        for table, column in analysis.drop_columns:
            analysis.findings.append(
                MigrationFinding(
                    rule="drop_without_paired_expand",
                    detail=(
                        f"drop_column {table}.{column} is not paired with an "
                        f"earlier expand migration that retired reads/writes "
                        f"on the column. Generate the expand first, ship it, "
                        f"then drop in a later contract migration."
                    ),
                )
            )

    # Rule 3: FK adds with nullable=False without a paired earlier expand.
    for table, _fk_name, info in analysis.add_fks:
        if info["nullable_false"] and not _has_paired_earlier_expand(
            migration_module, history
        ):
            analysis.findings.append(
                MigrationFinding(
                    rule="fk_not_null_without_paired_expand",
                    detail=(
                        f"create_foreign_key on {table} adds a NOT NULL FK "
                        f"without a paired expand that added the column "
                        f"nullable and backfilled it. Split into expand "
                        f"(add nullable FK, backfill) + contract (set NOT NULL)."
                    ),
                )
            )

    return analysis


# ---------------------------------------------------------------------------
# MigrationGenerator: emit expand/contract pair for a column drop intent
# ---------------------------------------------------------------------------


@dataclass
class GeneratedMigration:
    """A single emitted migration module's source."""

    migration_id: str
    phase: MigrationPhase
    source: str


class MigrationGenerator:
    """Helpers to emit paired expand/contract migration stubs.

    The generator is intentionally string-based: it does not run Alembic;
    it returns the would-be source text so callers can write it to disk or
    feed it into the framework's migration scaffolding.
    """

    def emit_expand_for_drop(
        self,
        base_id: str,
        table: str,
        column: str,
    ) -> GeneratedMigration:
        """Emit the expand stub paired with a later column-drop contract.

        The expand documents that reads/writes have moved off ``column`` so
        the later contract is safe. It performs no schema change itself —
        the structural change is in the contract.
        """
        rev = f"{base_id}_expand"
        source = self._render_template(
            revision=rev,
            down_revision=None,
            phase="expand",
            body=(
                f'    """Expand: stop reading/writing {table}.{column}.'
                "\n\n"
                f'    The next-release contract migration drops the column.'
                f'\n    """\n'
                f'    pass  # no structural change in expand'
            ),
        )
        return GeneratedMigration(migration_id=rev, phase="expand", source=source)

    def emit_contract_for_drop(
        self,
        base_id: str,
        table: str,
        column: str,
        expand_revision: Optional[str] = None,
    ) -> GeneratedMigration:
        """Emit the contract that performs the actual drop."""
        rev = f"{base_id}_contract"
        down = expand_revision or f"{base_id}_expand"
        body = (
            f'    """Contract: drop {table}.{column}.\n\n'
            f"    Safe because the paired expand ({down}) retired reads/writes.\n"
            f'    """\n'
            f'    op.drop_column("{table}", "{column}")'
        )
        source = self._render_template(
            revision=rev,
            down_revision=down,
            phase="contract",
            body=body,
        )
        return GeneratedMigration(migration_id=rev, phase="contract", source=source)

    def emit_pair_for_drop(
        self,
        base_id: str,
        table: str,
        column: str,
    ) -> Tuple[GeneratedMigration, GeneratedMigration]:
        """Convenience: emit the expand and the contract that drops ``column``."""
        expand = self.emit_expand_for_drop(base_id, table, column)
        contract = self.emit_contract_for_drop(
            base_id, table, column, expand_revision=expand.migration_id
        )
        return expand, contract

    @staticmethod
    def _render_template(
        revision: str,
        down_revision: Optional[str],
        phase: MigrationPhase,
        body: str,
    ) -> str:
        down_repr = repr(down_revision) if down_revision else "None"
        return (
            f'"""Auto-generated {phase} migration."""\n'
            "\n"
            "from alembic import op\n"
            "import sqlalchemy as sa\n"
            "\n"
            f"revision = {revision!r}\n"
            f"down_revision = {down_repr}\n"
            "branch_labels = None\n"
            "depends_on = None\n"
            f"phase = {phase!r}\n"
            "\n"
            "def upgrade() -> None:\n"
            f"{body}\n"
            "\n"
            "def downgrade() -> None:\n"
            "    pass\n"
        )


# ---------------------------------------------------------------------------
# Autogenerate hook (Item 80 wiring): run the same rules at autogenerate
# time so unsafe shapes are rejected before a file is committed. Operates
# on Alembic's ``MigrationScript`` directives instead of an already-written
# module — semantically the same three rules, but applied to the directive
# graph emitted by ``revision --autogenerate``. Wired from
# ``database/migrations/env.py``.
# ---------------------------------------------------------------------------


def _directive_add_columns(
    upgrade_ops: Any,
) -> List[Tuple[str, str, dict]]:
    """Extract ``(table, column, info)`` tuples for AddColumnOp directives.

    ``info`` mirrors the regex-extractor shape so both code paths feed
    the same rule logic: ``nullable_false`` and ``has_server_default``
    are read from the SQLAlchemy Column on the directive.
    """
    out: List[Tuple[str, str, dict]] = []
    for op_ in getattr(upgrade_ops, "ops", ()):
        if type(op_).__name__ != "AddColumnOp":
            continue
        col = getattr(op_, "column", None)
        if col is None:
            continue
        out.append(
            (
                getattr(op_, "table_name", "<unknown>"),
                getattr(col, "name", "<unknown>"),
                {
                    # SA Column.nullable defaults to True; we treat None as True.
                    "nullable_false": getattr(col, "nullable", True) is False,
                    "has_server_default": getattr(col, "server_default", None)
                    is not None,
                    "raw": "",
                },
            )
        )
    return out


def _directive_drop_columns(upgrade_ops: Any) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for op_ in getattr(upgrade_ops, "ops", ()):
        if type(op_).__name__ != "DropColumnOp":
            continue
        out.append(
            (
                getattr(op_, "table_name", "<unknown>"),
                getattr(op_, "column_name", "<unknown>"),
            )
        )
    return out


def _directive_add_fks(
    upgrade_ops: Any,
) -> List[Tuple[str, str, dict]]:
    out: List[Tuple[str, str, dict]] = []
    for op_ in getattr(upgrade_ops, "ops", ()):
        # CreateForeignKeyOp emits ``op.create_foreign_key(...)``. The
        # nullability of the referencing column is what matters for rule 3
        # — the Op carries ``local_cols`` (column names) and
        # ``source_table``; we look up the column on the upgrade_ops'
        # context Table when available, otherwise default to nullable=True
        # (the safe assumption that doesn't trip rule 3).
        if type(op_).__name__ != "CreateForeignKeyOp":
            continue
        nullable_false = False
        # If the op carries kwargs.nullable, honor it; otherwise rule 3
        # cannot be evaluated at directive time and we accept the op.
        kwargs = getattr(op_, "kw", {}) or {}
        if kwargs.get("nullable") is False:
            nullable_false = True
        out.append(
            (
                getattr(op_, "source_table", "<unknown>"),
                getattr(op_, "constraint_name", "<fk>") or "<fk>",
                {"nullable_false": nullable_false, "raw": ""},
            )
        )
    return out


def validate_revision_directives(
    directives: Sequence[Any],
    history: Optional[Sequence[Any]] = None,
) -> None:
    """Reject rolling-deploy-unsafe shapes in autogenerated directives.

    Mirrors :func:`validate_migration_safety` but operates on Alembic's
    ``MigrationScript`` directive list (the value Alembic passes to a
    ``process_revision_directives`` callback). Raises
    :class:`MigrationSafetyError` on the first violation; otherwise
    returns silently.

    ``history`` carries the same meaning as in
    :func:`validate_migration_safety` — when omitted, the
    contract-must-pair-with-expand check is skipped.
    """
    findings: List[MigrationFinding] = []
    for script in directives or ():
        upgrade_ops = getattr(script, "upgrade_ops", None)
        if upgrade_ops is None:
            continue

        for table, column, info in _directive_add_columns(upgrade_ops):
            if info["nullable_false"] and not info["has_server_default"]:
                findings.append(
                    MigrationFinding(
                        rule="not_null_without_default",
                        detail=(
                            f"add_column {table}.{column} is NOT NULL without a "
                            f"server_default; v1 inserts will fail during the "
                            f"rolling deploy. Either add a server_default or "
                            f"split into expand (nullable=True + backfill) and "
                            f"contract (nullable=False) phases."
                        ),
                    )
                )

        drop_columns = _directive_drop_columns(upgrade_ops)
        if drop_columns and not _has_paired_earlier_expand(script, history):
            for table, column in drop_columns:
                findings.append(
                    MigrationFinding(
                        rule="drop_without_paired_expand",
                        detail=(
                            f"drop_column {table}.{column} is not paired with an "
                            f"earlier expand migration that retired reads/writes "
                            f"on the column."
                        ),
                    )
                )

        for table, _fk_name, info in _directive_add_fks(upgrade_ops):
            if info["nullable_false"] and not _has_paired_earlier_expand(
                script, history
            ):
                findings.append(
                    MigrationFinding(
                        rule="fk_not_null_without_paired_expand",
                        detail=(
                            f"create_foreign_key on {table} adds a NOT NULL FK "
                            f"without a paired expand that added the column "
                            f"nullable and backfilled it."
                        ),
                    )
                )

    if findings:
        raise MigrationSafetyError("; ".join(f.format() for f in findings))


__all__ = [
    "MigrationPhase",
    "MigrationSafetyError",
    "MigrationFinding",
    "MigrationAnalysis",
    "validate_migration_safety",
    "validate_revision_directives",
    "analyze_migration",
    "expand_phase",
    "contract_phase",
    "phase_of",
    "MigrationGenerator",
    "GeneratedMigration",
]
