"""rpg_log extension.

Owns the event-log tables for an RPG campaign: encounters, combat
actions, dialogue, interactions, and item transactions. Reads the
present-state shape from the sibling ``rpg_state`` extension and depends
on it.

Logs are editable (standard ``UpdateMixinModel`` audit fields) so GMs
can correct typos and retcon. The ``created_at`` / ``updated_at`` audit
trail preserves history.
"""
