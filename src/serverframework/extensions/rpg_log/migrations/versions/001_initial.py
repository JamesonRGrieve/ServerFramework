"""Initial migration for rpg_log — owns the event-log tables.

Schema is loose (no SQL FK constraints) to match existing extension
migrations. Referential integrity is enforced at the ORM/manager layer.
"""

from alembic import op


revision = "001_rpg_log_initial"
down_revision = None
branch_labels = ("rpg_log",)
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS encounter_logs (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255),
            description TEXT,
            campaign_id VARCHAR(36),
            location_id VARCHAR(36),
            occurred_at TIMESTAMP,
            session_id VARCHAR(36),
            started_at TIMESTAMP,
            ended_at TIMESTAMP,
            outcome VARCHAR(255),
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
        "CREATE INDEX IF NOT EXISTS ix_enc_campaign ON encounter_logs(campaign_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_enc_session ON encounter_logs(session_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS encounter_participants (
            id VARCHAR(36) PRIMARY KEY,
            encounter_log_id VARCHAR(36),
            character_id VARCHAR(36),
            faction_id VARCHAR(36),
            side VARCHAR(64),
            initiative REAL,
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
        "CREATE INDEX IF NOT EXISTS ix_ep_encounter "
        "ON encounter_participants(encounter_log_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ep_character "
        "ON encounter_participants(character_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS combat_action_logs (
            id VARCHAR(36) PRIMARY KEY,
            encounter_log_id VARCHAR(36),
            campaign_id VARCHAR(36),
            actor_character_id VARCHAR(36),
            target_character_id VARCHAR(36),
            item_instance_id VARCHAR(36),
            trait_id VARCHAR(36),
            action_type VARCHAR(64),
            result VARCHAR(64),
            damage_amount REAL,
            round_number INTEGER,
            occurred_at TIMESTAMP,
            session_id VARCHAR(36),
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
        "CREATE INDEX IF NOT EXISTS ix_cal_encounter "
        "ON combat_action_logs(encounter_log_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cal_actor "
        "ON combat_action_logs(actor_character_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cal_target "
        "ON combat_action_logs(target_character_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dialogue_logs (
            id VARCHAR(36) PRIMARY KEY,
            campaign_id VARCHAR(36),
            location_id VARCHAR(36),
            actor_character_id VARCHAR(36),
            target_character_id VARCHAR(36),
            text TEXT,
            tone VARCHAR(64),
            occurred_at TIMESTAMP,
            session_id VARCHAR(36),
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
        "CREATE INDEX IF NOT EXISTS ix_dl_actor "
        "ON dialogue_logs(actor_character_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dialogue_participants (
            id VARCHAR(36) PRIMARY KEY,
            dialogue_log_id VARCHAR(36),
            character_id VARCHAR(36),
            role VARCHAR(64),
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
        "CREATE INDEX IF NOT EXISTS ix_dp_dialogue "
        "ON dialogue_participants(dialogue_log_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS interaction_logs (
            id VARCHAR(36) PRIMARY KEY,
            campaign_id VARCHAR(36),
            location_id VARCHAR(36),
            item_instance_id VARCHAR(36),
            actor_character_id VARCHAR(36),
            interaction_type VARCHAR(64),
            notes TEXT,
            occurred_at TIMESTAMP,
            session_id VARCHAR(36),
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
        "CREATE INDEX IF NOT EXISTS ix_il_actor "
        "ON interaction_logs(actor_character_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS transaction_logs (
            id VARCHAR(36) PRIMARY KEY,
            campaign_id VARCHAR(36),
            item_instance_id VARCHAR(36),
            quantity INTEGER DEFAULT 1,
            from_location_id VARCHAR(36),
            to_location_id VARCHAR(36),
            from_owner_character_id VARCHAR(36),
            to_owner_character_id VARCHAR(36),
            from_owner_faction_id VARCHAR(36),
            to_owner_faction_id VARCHAR(36),
            price REAL,
            kind VARCHAR(64),
            occurred_at TIMESTAMP,
            session_id VARCHAR(36),
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
        "CREATE INDEX IF NOT EXISTS ix_tl_item ON transaction_logs(item_instance_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tl_kind ON transaction_logs(kind)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS transaction_logs")
    op.execute("DROP TABLE IF EXISTS interaction_logs")
    op.execute("DROP TABLE IF EXISTS dialogue_participants")
    op.execute("DROP TABLE IF EXISTS dialogue_logs")
    op.execute("DROP TABLE IF EXISTS combat_action_logs")
    op.execute("DROP TABLE IF EXISTS encounter_participants")
    op.execute("DROP TABLE IF EXISTS encounter_logs")
