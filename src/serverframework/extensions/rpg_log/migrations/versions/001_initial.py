"""Initial migration for rpg_log — owns the event-log tables.

Cross-extension foreign keys are real ``ForeignKeyConstraint``
declarations. Manager-layer validators handle invariants that don't
fit cleanly in DDL.

Person endpoints reference ``persons.id`` (owned by ``genealogy``).
Multi-FK columns (actor / target both → persons; from_owner / to_owner
both → persons or factions) are declared as plain string columns with
named ``ForeignKeyConstraint`` rows.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "001_rpg_log_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("rpg_log",)
depends_on: Union[str, Sequence[str], None] = None

_INFO = {
    "source_module": "serverframework.extensions.rpg_log.BLL_RPGLog",
    "extension": "rpg_log",
}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _existing_index_names(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    if not _has_table("encounter_logs"):
        op.create_table(
            "encounter_logs",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("campaign_id", sa.String(36), nullable=True),
            sa.Column("location_id", sa.String(36), nullable=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=True),
            sa.Column("session_id", sa.String(36), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("ended_at", sa.DateTime(), nullable=True),
            sa.Column("outcome", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
            sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
            comment="Top-level encounter log (combat or otherwise)",
            info=_INFO,
        )

    enc_idx = _existing_index_names("encounter_logs")
    if "ix_enc_campaign" not in enc_idx:
        op.create_index("ix_enc_campaign", "encounter_logs", ["campaign_id"])
    if "ix_enc_session" not in enc_idx:
        op.create_index("ix_enc_session", "encounter_logs", ["session_id"])

    if not _has_table("encounter_participants"):
        op.create_table(
            "encounter_participants",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("encounter_log_id", sa.String(36), nullable=True),
            sa.Column("person_id", sa.String(36), nullable=True),
            sa.Column("faction_id", sa.String(36), nullable=True),
            sa.Column("side", sa.String(64), nullable=True),
            sa.Column("initiative", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["encounter_log_id"], ["encounter_logs.id"]
            ),
            sa.ForeignKeyConstraint(["person_id"], ["persons.id"]),
            sa.ForeignKeyConstraint(["faction_id"], ["factions.id"]),
            comment=(
                "Roster of an encounter. faction_id is the snapshot at "
                "encounter time even if the person defects later."
            ),
            info=_INFO,
        )

    ep_idx = _existing_index_names("encounter_participants")
    if "ix_ep_encounter" not in ep_idx:
        op.create_index(
            "ix_ep_encounter", "encounter_participants", ["encounter_log_id"]
        )
    if "ix_ep_person" not in ep_idx:
        op.create_index(
            "ix_ep_person", "encounter_participants", ["person_id"]
        )

    if not _has_table("combat_action_logs"):
        op.create_table(
            "combat_action_logs",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("encounter_log_id", sa.String(36), nullable=True),
            sa.Column("campaign_id", sa.String(36), nullable=True),
            sa.Column("actor_person_id", sa.String(36), nullable=True),
            sa.Column("target_person_id", sa.String(36), nullable=True),
            sa.Column("item_instance_id", sa.String(36), nullable=True),
            sa.Column("trait_id", sa.String(36), nullable=True),
            sa.Column("action_type", sa.String(64), nullable=True),
            sa.Column("result", sa.String(64), nullable=True),
            sa.Column("damage_amount", sa.Float(), nullable=True),
            sa.Column("round_number", sa.Integer(), nullable=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=True),
            sa.Column("session_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["encounter_log_id"], ["encounter_logs.id"]
            ),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
            sa.ForeignKeyConstraint(["actor_person_id"], ["persons.id"]),
            sa.ForeignKeyConstraint(["target_person_id"], ["persons.id"]),
            sa.ForeignKeyConstraint(
                ["item_instance_id"], ["item_instances.id"]
            ),
            sa.ForeignKeyConstraint(["trait_id"], ["traits.id"]),
            comment=(
                "One row per combat action. trait_id = spell/skill used; "
                "item_instance_id = weapon/consumable used."
            ),
            info=_INFO,
        )

    cal_idx = _existing_index_names("combat_action_logs")
    if "ix_cal_encounter" not in cal_idx:
        op.create_index(
            "ix_cal_encounter", "combat_action_logs", ["encounter_log_id"]
        )
    if "ix_cal_actor" not in cal_idx:
        op.create_index(
            "ix_cal_actor", "combat_action_logs", ["actor_person_id"]
        )
    if "ix_cal_target" not in cal_idx:
        op.create_index(
            "ix_cal_target", "combat_action_logs", ["target_person_id"]
        )

    if not _has_table("dialogue_logs"):
        op.create_table(
            "dialogue_logs",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("campaign_id", sa.String(36), nullable=True),
            sa.Column("location_id", sa.String(36), nullable=True),
            sa.Column("actor_person_id", sa.String(36), nullable=True),
            sa.Column("target_person_id", sa.String(36), nullable=True),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("tone", sa.String(64), nullable=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=True),
            sa.Column("session_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
            sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
            sa.ForeignKeyConstraint(["actor_person_id"], ["persons.id"]),
            sa.ForeignKeyConstraint(["target_person_id"], ["persons.id"]),
            comment="One uttered line.",
            info=_INFO,
        )

    if "ix_dl_actor" not in _existing_index_names("dialogue_logs"):
        op.create_index(
            "ix_dl_actor", "dialogue_logs", ["actor_person_id"]
        )

    if not _has_table("dialogue_participants"):
        op.create_table(
            "dialogue_participants",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("dialogue_log_id", sa.String(36), nullable=True),
            sa.Column("person_id", sa.String(36), nullable=True),
            sa.Column("role", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["dialogue_log_id"], ["dialogue_logs.id"]
            ),
            sa.ForeignKeyConstraint(["person_id"], ["persons.id"]),
            comment="Audience join for DialogueLog.",
            info=_INFO,
        )

    if "ix_dp_dialogue" not in _existing_index_names("dialogue_participants"):
        op.create_index(
            "ix_dp_dialogue", "dialogue_participants", ["dialogue_log_id"]
        )

    if not _has_table("interaction_logs"):
        op.create_table(
            "interaction_logs",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("campaign_id", sa.String(36), nullable=True),
            sa.Column("location_id", sa.String(36), nullable=True),
            sa.Column("item_instance_id", sa.String(36), nullable=True),
            sa.Column("actor_person_id", sa.String(36), nullable=True),
            sa.Column("interaction_type", sa.String(64), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=True),
            sa.Column("session_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
            sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
            sa.ForeignKeyConstraint(
                ["item_instance_id"], ["item_instances.id"]
            ),
            sa.ForeignKeyConstraint(["actor_person_id"], ["persons.id"]),
            comment="Non-combat / non-dialogue interactions.",
            info=_INFO,
        )

    if "ix_il_actor" not in _existing_index_names("interaction_logs"):
        op.create_index(
            "ix_il_actor", "interaction_logs", ["actor_person_id"]
        )

    if not _has_table("transaction_logs"):
        op.create_table(
            "transaction_logs",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("campaign_id", sa.String(36), nullable=True),
            sa.Column("item_instance_id", sa.String(36), nullable=True),
            sa.Column(
                "quantity", sa.Integer(), nullable=True, server_default="1"
            ),
            sa.Column("from_location_id", sa.String(36), nullable=True),
            sa.Column("to_location_id", sa.String(36), nullable=True),
            sa.Column("from_owner_person_id", sa.String(36), nullable=True),
            sa.Column("to_owner_person_id", sa.String(36), nullable=True),
            sa.Column("from_owner_faction_id", sa.String(36), nullable=True),
            sa.Column("to_owner_faction_id", sa.String(36), nullable=True),
            sa.Column("price", sa.Float(), nullable=True),
            sa.Column("kind", sa.String(64), nullable=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=True),
            sa.Column("session_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
            sa.ForeignKeyConstraint(
                ["item_instance_id"], ["item_instances.id"]
            ),
            sa.ForeignKeyConstraint(["from_location_id"], ["locations.id"]),
            sa.ForeignKeyConstraint(["to_location_id"], ["locations.id"]),
            sa.ForeignKeyConstraint(
                ["from_owner_person_id"], ["persons.id"]
            ),
            sa.ForeignKeyConstraint(
                ["to_owner_person_id"], ["persons.id"]
            ),
            sa.ForeignKeyConstraint(
                ["from_owner_faction_id"], ["factions.id"]
            ),
            sa.ForeignKeyConstraint(
                ["to_owner_faction_id"], ["factions.id"]
            ),
            comment=(
                "One row per item movement (trade, theft, looting, gifts). "
                "owner_* columns = assignment / responsibility (not equipped)."
            ),
            info=_INFO,
        )

    tl_idx = _existing_index_names("transaction_logs")
    if "ix_tl_item" not in tl_idx:
        op.create_index(
            "ix_tl_item", "transaction_logs", ["item_instance_id"]
        )
    if "ix_tl_kind" not in tl_idx:
        op.create_index("ix_tl_kind", "transaction_logs", ["kind"])


def downgrade() -> None:
    for tbl in (
        "transaction_logs",
        "interaction_logs",
        "dialogue_participants",
        "dialogue_logs",
        "combat_action_logs",
        "encounter_participants",
        "encounter_logs",
    ):
        if _has_table(tbl):
            op.drop_table(tbl)
