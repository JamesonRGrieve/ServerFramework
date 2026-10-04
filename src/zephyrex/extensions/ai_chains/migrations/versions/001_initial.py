# SPDX-License-Identifier: AGPL-3.0-or-later
"""Initial migration for ai_chains: owns ``chains``, ``chain_steps``,
``chain_runs`` and ``chain_step_results``.

Each table is made only where it is missing. The ``prompt_id`` of a step
carries no foreign key constraint: the ai_prompts extension's migrations run
on their own, so its table may not exist yet.

The extension before this had no migration and no working engine; its
models (chains, chain_links, chain_link_dependencies, chain_runs,
chain_link_runs) were never created by one, but a database that made them
another way may hold them. A ``chains`` table of that shape is kept and
given the bounds it lacks. A ``chain_runs`` table of that shape (an integer
status, no inputs) cannot hold a run and records none that happened, so this
migration stops and names what the operator must drop.
"""

from typing import List, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_ai_chains_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("ext_ai_chains",)
depends_on: Union[str, Sequence[str], None] = None

INFO = {"extension": "ai_chains"}
LEGACY_RUN_TABLES = ("chain_link_runs", "chain_runs")
CHAIN_BOUNDS = (
    ("max_steps", "The most steps a run executes (loops included)", "100"),
    ("timeout_seconds", "How long a run may take before it fails", "300"),
    (
        "max_output_characters",
        "The largest output one step may produce",
        "20000",
    ),
)


def _text(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(), nullable=nullable, comment=comment)


def _integer(name: str, comment: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.Integer(), nullable=nullable, comment=comment)


def _reference(name: str, table: str, nullable: bool = True) -> sa.Column:
    return sa.Column(
        name,
        sa.String(),
        sa.ForeignKey(f"{table}.id"),
        nullable=nullable,
        comment=f"The ID of the related {name[:-3]}",
    )


def _audit() -> List[sa.schema.SchemaItem]:
    return [
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    ]


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _has(table: str) -> bool:
    return bool(sa.inspect(op.get_bind()).has_table(table))


def _chains() -> None:
    if not _has("chains"):
        op.create_table(
            "chains",
            _reference("user_id", "users"),
            _reference("team_id", "teams"),
            _text("name", "The name", nullable=False),
            _text("description", "The description"),
            sa.Column(
                "favourite", sa.Boolean(), nullable=False, comment="Marked favourite"
            ),
            *[_integer(n, c, nullable=False) for n, c, _ in CHAIN_BOUNDS],
            *_audit(),
            comment="An owned, ordered set of steps that a run executes",
            info=INFO,
        )
        op.create_index("ix_chains_deleted_at", "chains", ["deleted_at"])
        return
    present = _columns("chains")
    for name, comment, default in CHAIN_BOUNDS:
        if name not in present:
            op.add_column(
                "chains",
                sa.Column(
                    name,
                    sa.Integer(),
                    nullable=False,
                    server_default=default,
                    comment=comment,
                ),
            )


def _chain_steps() -> None:
    if _has("chain_steps"):
        return
    op.create_table(
        "chain_steps",
        _reference("chain_id", "chains", nullable=False),
        _text("prompt_id", "The prompt a prompt step fills"),
        _reference("ability_id", "abilities"),
        _text("name", "The step's name, an identifier", nullable=False),
        _integer("position", "Where the step runs, lowest first", nullable=False),
        _text("kind", "prompt | ability | condition | set", nullable=False),
        sa.Column(
            "arguments",
            sa.JSON(),
            nullable=True,
            comment="Expressions for an ability's arguments or a prompt's variables",
        ),
        _text("expression", "A condition's test, or a set step's value"),
        _text("variable", "The variable the step's output goes to"),
        _text("on_true", "The step a true condition goes to"),
        _text("on_false", "The step a false condition goes to"),
        _integer("max_loops", "The most times a condition's jump back is taken"),
        *_audit(),
        comment="One step of a chain, run in position order",
        info=INFO,
    )
    op.create_index("ix_chain_steps_chain_id", "chain_steps", ["chain_id"])
    op.create_index("ix_chain_steps_deleted_at", "chain_steps", ["deleted_at"])


def _chain_runs() -> None:
    if _has("chain_runs"):
        if "inputs" not in _columns("chain_runs"):
            present = [t for t in LEGACY_RUN_TABLES if _has(t)]
            raise RuntimeError(
                "ai_chains: the database holds the old extension's run tables, "
                "which cannot hold a run. Drop them (they record no run that "
                f"happened) and migrate again: DROP TABLE {', '.join(present)}"
            )
        return
    op.create_table(
        "chain_runs",
        _reference("chain_id", "chains", nullable=False),
        _reference("user_id", "users"),
        _text("status", "pending | running | succeeded | failed | cancelled", False),
        sa.Column("inputs", sa.JSON(), nullable=True, comment="Starting variables"),
        sa.Column(
            "variables", sa.JSON(), nullable=True, comment="Variables when it ended"
        ),
        _text("output", "The last step's output, as JSON"),
        _text("error", "Why it failed"),
        _text("error_kind", "definition | limit | timeout | step | cancelled"),
        _integer("steps_executed", "Steps executed", nullable=False),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            comment="Asked to stop",
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True, comment="Began"),
        sa.Column("completed_at", sa.DateTime(), nullable=True, comment="Ended"),
        *_audit(),
        comment="One execution of a chain, as its owner",
        info=INFO,
    )
    op.create_index("ix_chain_runs_chain_id", "chain_runs", ["chain_id"])
    op.create_index("ix_chain_runs_deleted_at", "chain_runs", ["deleted_at"])


def _chain_step_results() -> None:
    if _has("chain_step_results"):
        return
    op.create_table(
        "chain_step_results",
        _reference("chain_run_id", "chain_runs", nullable=False),
        _reference("chain_step_id", "chain_steps"),
        _text("step_name", "The step's name when it ran", nullable=False),
        _text("kind", "The step's kind when it ran", nullable=False),
        _integer("sequence", "Its place in the run, from 1", nullable=False),
        _text("status", "succeeded | failed | cancelled", nullable=False),
        _text("input", "What the step was given, as JSON"),
        _text("output", "What it produced, as JSON"),
        _text("error", "Why it failed"),
        sa.Column("started_at", sa.DateTime(), nullable=True, comment="Began"),
        sa.Column("completed_at", sa.DateTime(), nullable=True, comment="Ended"),
        _integer("duration_ms", "How long it took"),
        *_audit(),
        comment="One step a chain run executed: input, output, status, timing",
        info=INFO,
    )
    op.create_index("ix_chain_step_results_run", "chain_step_results", ["chain_run_id"])
    op.create_index(
        "ix_chain_step_results_deleted_at", "chain_step_results", ["deleted_at"]
    )


def upgrade() -> None:
    _chains()
    _chain_steps()
    _chain_runs()
    _chain_step_results()


def downgrade() -> None:
    for table in ("chain_step_results", "chain_runs", "chain_steps", "chains"):
        if _has(table):
            op.drop_table(table)
