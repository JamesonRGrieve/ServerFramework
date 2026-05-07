"""Initial migration for rpg_state.

Hard-depends on ``genealogy``. This migration:

1. Injects the RPG character columns (``kind``, ``campaign_id``,
   ``user_id``) onto the ``persons`` table via ``op.add_column``. The
   ``persons`` table becomes the character table for RPG users.
2. Injects Faction endpoints (``faction_id``, ``target_faction_id``)
   onto the ``relationships`` table, relaxes nullability on the
   default Person endpoints, and adds a CHECK constraint enforcing
   endpoint XOR (exactly one endpoint per side).
3. Creates the rpg_state-owned tables: game_systems, campaigns, traits,
   status_effects, status_effect_traits, person_traits,
   person_status_effects, factions, locations, items, item_instances,
   quests, objectives, person_quests, faction_quests, person_objectives.

Foreign keys are enforced at the database layer where the references
are stable. The cyclic Location ↔ ItemInstance reference (a satchel's
interior is a Location whose ``container_item_instance_id`` points at
the satchel ItemInstance) is resolved by adding the
``container_item_instance_id`` and ``location_id`` FKs after both
tables exist.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "001_rpg_state_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = ("rpg_state",)
depends_on: Union[str, Sequence[str], None] = None

_INFO = {
    "source_module": "serverframework.extensions.rpg_state.BLL_RPGState",
    "extension": "rpg_state",
}


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _existing_index_names(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table)}


def _existing_column_names(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    # ---------------------------------------------------------------
    # game_systems
    # ---------------------------------------------------------------
    if not _has_table("game_systems"):
        op.create_table(
            "game_systems",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            comment="Reference catalog of supported RPG systems",
            info=_INFO,
        )

    # ---------------------------------------------------------------
    # campaigns
    # ---------------------------------------------------------------
    if not _has_table("campaigns"):
        op.create_table(
            "campaigns",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("game_system_id", sa.String(36), nullable=True),
            sa.Column("user_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["game_system_id"], ["game_systems.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            comment="Top-level campaign owning all in-game state. user_id = GM/owner.",
            info=_INFO,
        )

    if "ix_campaigns_user" not in _existing_index_names("campaigns"):
        op.create_index("ix_campaigns_user", "campaigns", ["user_id"])

    # ---------------------------------------------------------------
    # traits
    # ---------------------------------------------------------------
    if not _has_table("traits"):
        op.create_table(
            "traits",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("kind", sa.String(64), nullable=True),
            sa.Column("game_system_id", sa.String(36), nullable=True),
            sa.Column("campaign_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["game_system_id"], ["game_systems.id"]),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
            comment="Unified property catalog (attributes, skills, spells, talents, feats, resources, progression).",
            info=_INFO,
        )

    traits_idx = _existing_index_names("traits")
    if "ix_traits_kind" not in traits_idx:
        op.create_index("ix_traits_kind", "traits", ["kind"])
    if "ix_traits_system" not in traits_idx:
        op.create_index("ix_traits_system", "traits", ["game_system_id"])

    # ---------------------------------------------------------------
    # status_effects + status_effect_traits
    # ---------------------------------------------------------------
    if not _has_table("status_effects"):
        op.create_table(
            "status_effects",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("default_duration_seconds", sa.Integer(), nullable=True),
            sa.Column("game_system_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["game_system_id"], ["game_systems.id"]),
            comment="Status effect templates (buffs, debuffs, conditions, damage).",
            info=_INFO,
        )

    if not _has_table("status_effect_traits"):
        op.create_table(
            "status_effect_traits",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("status_effect_id", sa.String(36), nullable=True),
            sa.Column("trait_id", sa.String(36), nullable=True),
            sa.Column("operation", sa.String(32), nullable=True),
            sa.Column("value", sa.Float(), nullable=True),
            sa.Column("qualifier", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["status_effect_id"], ["status_effects.id"]),
            sa.ForeignKeyConstraint(["trait_id"], ["traits.id"]),
            comment="Per-trait modifiers contributed by a StatusEffect template.",
            info=_INFO,
        )

    set_idx = _existing_index_names("status_effect_traits")
    if "ix_set_effect" not in set_idx:
        op.create_index(
            "ix_set_effect", "status_effect_traits", ["status_effect_id"]
        )
    if "ix_set_trait" not in set_idx:
        op.create_index("ix_set_trait", "status_effect_traits", ["trait_id"])

    # ---------------------------------------------------------------
    # Inject character columns onto persons table.
    # genealogy owns the table; rpg_state owns these columns.
    # ---------------------------------------------------------------
    person_cols = _existing_column_names("persons")
    if "kind" not in person_cols:
        op.add_column(
            "persons",
            sa.Column(
                "kind",
                sa.String(64),
                nullable=True,
                comment="pc|npc|monster|creature|vehicle|construct|... (rpg_state)",
            ),
        )
    if "campaign_id" not in person_cols:
        op.add_column(
            "persons",
            sa.Column(
                "campaign_id",
                sa.String(36),
                sa.ForeignKey("campaigns.id"),
                nullable=True,
                comment="Campaign this actor belongs to (rpg_state)",
            ),
        )
    if "user_id" not in person_cols:
        op.add_column(
            "persons",
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=True,
                comment="Controlling player; NULL = NPC (rpg_state)",
            ),
        )

    persons_idx = _existing_index_names("persons")
    if "ix_persons_campaign" not in persons_idx:
        op.create_index("ix_persons_campaign", "persons", ["campaign_id"])
    if "ix_persons_user" not in persons_idx:
        op.create_index("ix_persons_user", "persons", ["user_id"])

    # ---------------------------------------------------------------
    # person_traits
    # ---------------------------------------------------------------
    if not _has_table("person_traits"):
        op.create_table(
            "person_traits",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("person_id", sa.String(36), nullable=True),
            sa.Column("trait_id", sa.String(36), nullable=True),
            sa.Column("value", sa.Float(), nullable=True),
            sa.Column("rank", sa.Integer(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["person_id"], ["persons.id"]),
            sa.ForeignKeyConstraint(["trait_id"], ["traits.id"]),
            comment="Per-person base trait values.",
            info=_INFO,
        )

    pt_idx = _existing_index_names("person_traits")
    if "ix_pt_person" not in pt_idx:
        op.create_index("ix_pt_person", "person_traits", ["person_id"])
    if "ix_pt_trait" not in pt_idx:
        op.create_index("ix_pt_trait", "person_traits", ["trait_id"])

    # ---------------------------------------------------------------
    # person_status_effects
    # ---------------------------------------------------------------
    if not _has_table("person_status_effects"):
        op.create_table(
            "person_status_effects",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("person_id", sa.String(36), nullable=True),
            sa.Column("status_effect_id", sa.String(36), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("source_person_id", sa.String(36), nullable=True),
            sa.Column("source_item_instance_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["person_id"], ["persons.id"]),
            sa.ForeignKeyConstraint(["status_effect_id"], ["status_effects.id"]),
            sa.ForeignKeyConstraint(["source_person_id"], ["persons.id"]),
            comment="Active StatusEffect attachments per person.",
            info=_INFO,
        )

    pse_idx = _existing_index_names("person_status_effects")
    if "ix_pse_person" not in pse_idx:
        op.create_index("ix_pse_person", "person_status_effects", ["person_id"])
    if "ix_pse_active" not in pse_idx:
        op.create_index(
            "ix_pse_active",
            "person_status_effects",
            ["person_id", "expires_at"],
        )

    # ---------------------------------------------------------------
    # factions
    # ---------------------------------------------------------------
    if not _has_table("factions"):
        op.create_table(
            "factions",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("parent_id", sa.String(36), nullable=True),
            sa.Column("campaign_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["parent_id"], ["factions.id"]),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
            comment=(
                "Hierarchical faction (parent_id self-FK fast-path for the "
                "primary tree). Membership lives in relationships(kind='member_of')."
            ),
            info=_INFO,
        )

    fac_idx = _existing_index_names("factions")
    if "ix_factions_campaign" not in fac_idx:
        op.create_index("ix_factions_campaign", "factions", ["campaign_id"])
    if "ix_factions_parent" not in fac_idx:
        op.create_index("ix_factions_parent", "factions", ["parent_id"])

    # ---------------------------------------------------------------
    # locations  (cyclic FK with item_instances; resolved post-create)
    # ---------------------------------------------------------------
    if not _has_table("locations"):
        op.create_table(
            "locations",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("parent_id", sa.String(36), nullable=True),
            sa.Column("campaign_id", sa.String(36), nullable=True),
            # FK on container_item_instance_id is added post-create,
            # after item_instances exists, to break the cycle.
            sa.Column("container_item_instance_id", sa.String(36), nullable=True),
            sa.Column("associated_person_id", sa.String(36), nullable=True),
            sa.Column("kind", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["parent_id"], ["locations.id"]),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
            sa.ForeignKeyConstraint(["associated_person_id"], ["persons.id"]),
            comment=(
                "Recursive spatial node. Equipment is a Location bound to "
                "a person via associated_person_id; equipping is a move."
            ),
            info=_INFO,
        )

    loc_idx = _existing_index_names("locations")
    if "ix_locations_campaign" not in loc_idx:
        op.create_index("ix_locations_campaign", "locations", ["campaign_id"])
    if "ix_locations_parent" not in loc_idx:
        op.create_index("ix_locations_parent", "locations", ["parent_id"])
    if "ix_locations_container" not in loc_idx:
        op.create_index(
            "ix_locations_container",
            "locations",
            ["container_item_instance_id"],
        )
    if "ix_locations_person" not in loc_idx:
        op.create_index(
            "ix_locations_person", "locations", ["associated_person_id"]
        )

    # ---------------------------------------------------------------
    # items + item_instances
    # ---------------------------------------------------------------
    if not _has_table("items"):
        op.create_table(
            "items",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("game_system_id", sa.String(36), nullable=True),
            sa.Column("weight", sa.Float(), nullable=True),
            sa.Column("base_value", sa.Float(), nullable=True),
            sa.Column("stack_size", sa.Integer(), nullable=True, server_default="1"),
            sa.Column("kind", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["game_system_id"], ["game_systems.id"]),
            comment="Item template (catalog).",
            info=_INFO,
        )

    if not _has_table("item_instances"):
        op.create_table(
            "item_instances",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("item_id", sa.String(36), nullable=True),
            sa.Column("location_id", sa.String(36), nullable=True),
            sa.Column("owner_person_id", sa.String(36), nullable=True),
            sa.Column("owner_faction_id", sa.String(36), nullable=True),
            sa.Column("quantity", sa.Integer(), nullable=True, server_default="1"),
            sa.Column("durability", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
            sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
            sa.ForeignKeyConstraint(["owner_person_id"], ["persons.id"]),
            sa.ForeignKeyConstraint(["owner_faction_id"], ["factions.id"]),
            comment=(
                "Per-instance item row. owner_* = assignment / "
                "responsibility / who-would-notice-missing — distinct "
                "from equipping (which is spatial via location_id)."
            ),
            info=_INFO,
        )

    ii_idx = _existing_index_names("item_instances")
    if "ix_ii_location" not in ii_idx:
        op.create_index("ix_ii_location", "item_instances", ["location_id"])
    if "ix_ii_owner_person" not in ii_idx:
        op.create_index(
            "ix_ii_owner_person", "item_instances", ["owner_person_id"]
        )
    if "ix_ii_owner_faction" not in ii_idx:
        op.create_index(
            "ix_ii_owner_faction", "item_instances", ["owner_faction_id"]
        )

    # Close the Location ↔ ItemInstance cycle: add the FK now that both
    # tables exist. Use batch_alter_table for SQLite compatibility.
    with op.batch_alter_table("locations") as batch_op:
        batch_op.create_foreign_key(
            "fk_locations_container_item_instance",
            "item_instances",
            ["container_item_instance_id"],
            ["id"],
        )

    # ---------------------------------------------------------------
    # quests + objectives
    # ---------------------------------------------------------------
    if not _has_table("quests"):
        op.create_table(
            "quests",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("campaign_id", sa.String(36), nullable=True),
            sa.Column("giver_faction_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
            sa.ForeignKeyConstraint(["giver_faction_id"], ["factions.id"]),
            comment="Quest template",
            info=_INFO,
        )

    if "ix_quests_campaign" not in _existing_index_names("quests"):
        op.create_index("ix_quests_campaign", "quests", ["campaign_id"])

    if not _has_table("objectives"):
        op.create_table(
            "objectives",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("quest_id", sa.String(36), nullable=True),
            sa.Column("prerequisite_objective_id", sa.String(36), nullable=True),
            sa.Column("order_index", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["quest_id"], ["quests.id"]),
            sa.ForeignKeyConstraint(
                ["prerequisite_objective_id"], ["objectives.id"]
            ),
            comment="Objective template; chains via prerequisite_objective_id.",
            info=_INFO,
        )

    if "ix_objectives_quest" not in _existing_index_names("objectives"):
        op.create_index("ix_objectives_quest", "objectives", ["quest_id"])

    # ---------------------------------------------------------------
    # person_quests + faction_quests + person_objectives
    # ---------------------------------------------------------------
    if not _has_table("person_quests"):
        op.create_table(
            "person_quests",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("person_id", sa.String(36), nullable=True),
            sa.Column("quest_id", sa.String(36), nullable=True),
            sa.Column("status", sa.String(32), nullable=True),
            sa.Column("accepted_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["person_id"], ["persons.id"]),
            sa.ForeignKeyConstraint(["quest_id"], ["quests.id"]),
            comment="Per-person quest progress instance",
            info=_INFO,
        )

    pq_idx = _existing_index_names("person_quests")
    if "ix_pq_person" not in pq_idx:
        op.create_index("ix_pq_person", "person_quests", ["person_id"])
    if "ix_pq_status" not in pq_idx:
        op.create_index(
            "ix_pq_status", "person_quests", ["person_id", "status"]
        )

    if not _has_table("faction_quests"):
        op.create_table(
            "faction_quests",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("faction_id", sa.String(36), nullable=True),
            sa.Column("quest_id", sa.String(36), nullable=True),
            sa.Column("status", sa.String(32), nullable=True),
            sa.Column("accepted_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["faction_id"], ["factions.id"]),
            sa.ForeignKeyConstraint(["quest_id"], ["quests.id"]),
            comment="Per-faction quest progress instance",
            info=_INFO,
        )

    if "ix_fq_faction" not in _existing_index_names("faction_quests"):
        op.create_index("ix_fq_faction", "faction_quests", ["faction_id"])

    if not _has_table("person_objectives"):
        op.create_table(
            "person_objectives",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("person_id", sa.String(36), nullable=True),
            sa.Column("objective_id", sa.String(36), nullable=True),
            sa.Column("status", sa.String(32), nullable=True),
            sa.Column("progress", sa.Float(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by_user_id", sa.String(36), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["person_id"], ["persons.id"]),
            sa.ForeignKeyConstraint(["objective_id"], ["objectives.id"]),
            comment="Per-person objective progress instance",
            info=_INFO,
        )

    if "ix_po_person" not in _existing_index_names("person_objectives"):
        op.create_index("ix_po_person", "person_objectives", ["person_id"])

    # ---------------------------------------------------------------
    # Inject Faction endpoints onto the relationships table, relax
    # nullability on the default Person endpoints, and add the
    # endpoint-XOR CHECK constraint. Owned by rpg_state via _INFO.
    # ---------------------------------------------------------------
    rel_cols = _existing_column_names("relationships")
    with op.batch_alter_table("relationships") as batch_op:
        if "faction_id" not in rel_cols:
            batch_op.add_column(
                sa.Column(
                    "faction_id",
                    sa.String(36),
                    sa.ForeignKey("factions.id"),
                    nullable=True,
                    comment="Subject-side Faction endpoint (rpg_state)",
                )
            )
        if "target_faction_id" not in rel_cols:
            batch_op.add_column(
                sa.Column(
                    "target_faction_id",
                    sa.String(36),
                    sa.ForeignKey("factions.id"),
                    nullable=True,
                    comment="Object-side Faction endpoint (rpg_state)",
                )
            )
        # Both Person endpoints become nullable; existence enforced by the
        # endpoint-XOR CHECK below rather than per-column NOT NULL.
        batch_op.alter_column("person_id", existing_type=sa.String(36), nullable=True)
        batch_op.alter_column(
            "target_person_id", existing_type=sa.String(36), nullable=True
        )
        # Endpoint XOR: exactly one endpoint per side.
        # Boolean-as-int trick is portable across SQLite, Postgres, MySQL.
        batch_op.create_check_constraint(
            "ck_relationships_subject_xor",
            "((person_id IS NOT NULL) + (faction_id IS NOT NULL)) = 1",
        )
        batch_op.create_check_constraint(
            "ck_relationships_target_xor",
            "((target_person_id IS NOT NULL) + (target_faction_id IS NOT NULL)) = 1",
        )

    rel_idx = _existing_index_names("relationships")
    if "ix_rel_subject_faction" not in rel_idx:
        op.create_index(
            "ix_rel_subject_faction", "relationships", ["faction_id"]
        )
    if "ix_rel_target_faction" not in rel_idx:
        op.create_index(
            "ix_rel_target_faction", "relationships", ["target_faction_id"]
        )


def downgrade() -> None:
    # Drop CHECKs and faction columns from relationships, restore
    # NOT NULL on Person endpoints.
    rel_idx = _existing_index_names("relationships")
    for name in ("ix_rel_target_faction", "ix_rel_subject_faction"):
        if name in rel_idx:
            op.drop_index(name, table_name="relationships")

    rel_cols = _existing_column_names("relationships")
    with op.batch_alter_table("relationships") as batch_op:
        batch_op.drop_constraint(
            "ck_relationships_target_xor", type_="check"
        )
        batch_op.drop_constraint(
            "ck_relationships_subject_xor", type_="check"
        )
        if "target_faction_id" in rel_cols:
            batch_op.drop_column("target_faction_id")
        if "faction_id" in rel_cols:
            batch_op.drop_column("faction_id")
        batch_op.alter_column(
            "target_person_id", existing_type=sa.String(36), nullable=False
        )
        batch_op.alter_column(
            "person_id", existing_type=sa.String(36), nullable=False
        )

    # Drop rpg_state-owned tables in reverse dependency order.
    for tbl in (
        "person_objectives",
        "faction_quests",
        "person_quests",
        "objectives",
        "quests",
    ):
        if _has_table(tbl):
            op.drop_table(tbl)

    # Break the Location ↔ ItemInstance cycle before dropping.
    if _has_table("locations"):
        with op.batch_alter_table("locations") as batch_op:
            try:
                batch_op.drop_constraint(
                    "fk_locations_container_item_instance", type_="foreignkey"
                )
            except Exception:
                pass

    for tbl in (
        "item_instances",
        "items",
        "locations",
        "factions",
        "person_status_effects",
        "person_traits",
    ):
        if _has_table(tbl):
            op.drop_table(tbl)

    # Drop injected character columns from persons.
    person_cols = _existing_column_names("persons")
    with op.batch_alter_table("persons") as batch_op:
        for col in ("user_id", "campaign_id", "kind"):
            if col in person_cols:
                batch_op.drop_column(col)

    for tbl in (
        "status_effect_traits",
        "status_effects",
        "traits",
        "campaigns",
        "game_systems",
    ):
        if _has_table(tbl):
            op.drop_table(tbl)
