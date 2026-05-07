"""Initial migration for rpg_state — owns the present-state tables.

Schema is loose (no SQL FK constraints) to match existing extension
migrations (acl_rbac, metadata, auth_invitations). Referential integrity
is enforced at the ORM/manager layer via ``Reference.Optional`` mixins.

Cyclic relationship resolved by the loose schema:
- locations.container_item_instance_id → item_instances.id
- item_instances.location_id           → locations.id
"""

from alembic import op


revision = "001_rpg_state_initial"
down_revision = None
branch_labels = ("rpg_state",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS game_systems (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255),
            description TEXT,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaigns (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255),
            description TEXT,
            game_system_id VARCHAR(36),
            user_id VARCHAR(36),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_campaigns_user ON campaigns(user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS traits (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255),
            description TEXT,
            kind VARCHAR(64),
            game_system_id VARCHAR(36),
            campaign_id VARCHAR(36),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_traits_kind ON traits(kind)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_traits_system ON traits(game_system_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS status_effects (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255),
            description TEXT,
            default_duration_seconds INTEGER,
            game_system_id VARCHAR(36),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS status_effect_traits (
            id VARCHAR(36) PRIMARY KEY,
            status_effect_id VARCHAR(36),
            trait_id VARCHAR(36),
            operation VARCHAR(32),
            value REAL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_set_effect ON status_effect_traits(status_effect_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_set_trait ON status_effect_traits(trait_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS characters (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255),
            description TEXT,
            kind VARCHAR(64),
            level INTEGER,
            campaign_id VARCHAR(36),
            user_id VARCHAR(36),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_characters_campaign ON characters(campaign_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_characters_user ON characters(user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS character_traits (
            id VARCHAR(36) PRIMARY KEY,
            character_id VARCHAR(36),
            trait_id VARCHAR(36),
            value REAL,
            rank INTEGER,
            notes TEXT,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ct_character ON character_traits(character_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ct_trait ON character_traits(trait_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS character_status_effects (
            id VARCHAR(36) PRIMARY KEY,
            character_id VARCHAR(36),
            status_effect_id VARCHAR(36),
            started_at TIMESTAMP,
            expires_at TIMESTAMP,
            source_character_id VARCHAR(36),
            source_item_instance_id VARCHAR(36),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cse_character "
        "ON character_status_effects(character_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cse_active "
        "ON character_status_effects(character_id, expires_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS factions (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255),
            description TEXT,
            parent_id VARCHAR(36),
            campaign_id VARCHAR(36),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_factions_campaign ON factions(campaign_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_factions_parent ON factions(parent_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS character_factions (
            id VARCHAR(36) PRIMARY KEY,
            character_id VARCHAR(36),
            faction_id VARCHAR(36),
            role VARCHAR(64),
            reputation REAL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cf_character ON character_factions(character_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cf_faction ON character_factions(faction_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS locations (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255),
            description TEXT,
            parent_id VARCHAR(36),
            campaign_id VARCHAR(36),
            container_item_instance_id VARCHAR(36),
            associated_character_id VARCHAR(36),
            kind VARCHAR(64),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_locations_campaign ON locations(campaign_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_locations_parent ON locations(parent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_locations_container "
        "ON locations(container_item_instance_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_locations_character "
        "ON locations(associated_character_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255),
            description TEXT,
            game_system_id VARCHAR(36),
            weight REAL,
            base_value REAL,
            stack_size INTEGER DEFAULT 1,
            kind VARCHAR(64),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS item_instances (
            id VARCHAR(36) PRIMARY KEY,
            item_id VARCHAR(36),
            location_id VARCHAR(36),
            owner_character_id VARCHAR(36),
            owner_faction_id VARCHAR(36),
            quantity INTEGER DEFAULT 1,
            durability REAL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ii_location ON item_instances(location_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ii_owner_char "
        "ON item_instances(owner_character_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ii_owner_faction "
        "ON item_instances(owner_faction_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS quests (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255),
            description TEXT,
            campaign_id VARCHAR(36),
            giver_faction_id VARCHAR(36),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_quests_campaign ON quests(campaign_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS objectives (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255),
            description TEXT,
            quest_id VARCHAR(36),
            prerequisite_objective_id VARCHAR(36),
            order_index INTEGER,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_objectives_quest ON objectives(quest_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS character_quests (
            id VARCHAR(36) PRIMARY KEY,
            character_id VARCHAR(36),
            quest_id VARCHAR(36),
            status VARCHAR(32),
            accepted_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cq_character ON character_quests(character_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cq_status "
        "ON character_quests(character_id, status)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS faction_quests (
            id VARCHAR(36) PRIMARY KEY,
            faction_id VARCHAR(36),
            quest_id VARCHAR(36),
            status VARCHAR(32),
            accepted_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_fq_faction ON faction_quests(faction_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS character_objectives (
            id VARCHAR(36) PRIMARY KEY,
            character_id VARCHAR(36),
            objective_id VARCHAR(36),
            status VARCHAR(32),
            progress REAL,
            completed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_by_user_id VARCHAR(36)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_co_character "
        "ON character_objectives(character_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS character_objectives")
    op.execute("DROP TABLE IF EXISTS faction_quests")
    op.execute("DROP TABLE IF EXISTS character_quests")
    op.execute("DROP TABLE IF EXISTS objectives")
    op.execute("DROP TABLE IF EXISTS quests")
    op.execute("DROP TABLE IF EXISTS item_instances")
    op.execute("DROP TABLE IF EXISTS items")
    op.execute("DROP TABLE IF EXISTS locations")
    op.execute("DROP TABLE IF EXISTS character_factions")
    op.execute("DROP TABLE IF EXISTS factions")
    op.execute("DROP TABLE IF EXISTS character_status_effects")
    op.execute("DROP TABLE IF EXISTS character_traits")
    op.execute("DROP TABLE IF EXISTS characters")
    op.execute("DROP TABLE IF EXISTS status_effect_traits")
    op.execute("DROP TABLE IF EXISTS status_effects")
    op.execute("DROP TABLE IF EXISTS traits")
    op.execute("DROP TABLE IF EXISTS campaigns")
    op.execute("DROP TABLE IF EXISTS game_systems")
