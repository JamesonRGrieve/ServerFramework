"""rpg_state extension.

Owns the present-state schema for an RPG campaign: characters, factions,
items, locations, quests, and the unified Trait / StatusEffect property
model. Designed to be system-agnostic — DnD, Pathfinder, FFG, WH40K, and
other tabletop / video-game / roleplay systems plug in via the
``GameSystem`` reference table.

State here is the "now". Historical events (combat actions, dialogue,
transactions) live in the sibling ``rpg_log`` extension.
"""
