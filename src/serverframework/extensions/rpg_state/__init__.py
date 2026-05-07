"""rpg_state extension.

Owns the present-state schema for an RPG campaign: characters, factions,
items, locations, quests, and the unified Trait / StatusEffect property
model. Designed to be system-agnostic — DnD, Pathfinder, FFG, WH40K, and
other tabletop / video-game / roleplay systems plug in via the
``GameSystem`` reference table.

State here is the "now". Historical events (combat actions, dialogue,
transactions) live in the sibling ``rpg_log`` extension.

Membership & visibility
-----------------------
There is intentionally no ``CampaignMember`` table. Membership is
inferred:

- The campaign **owner** is ``CampaignModel.user_id`` (the GM).
- A user is a **player** in a campaign iff they own at least one
  ``CharacterModel`` with that ``campaign_id`` (i.e. their ``user_id``
  matches and ``kind`` is typically 'pc').

Anything finer-grained — observers, co-GMs, per-row visibility ("this
NPC is GM-only"), shared handouts, per-character notes that other
players shouldn't see — is delegated to the ``acl_rbac`` extension as an
**optional** dependency. ``rpg_state`` does **not** carry a
``visibility`` column on its entities; granularity belongs to ACL, not
to a coarse public/gm enum that would constrain the GM.

Progression
-----------
Progression dimensions (level, experience, spent XP, milestones, dice
pools, etc.) are **Traits**, not columns on ``CharacterModel``. By
convention they live as ``TraitModel(kind='progression')`` rows named
'level' / 'experience' / 'spent_experience' / ..., and each character's
current value lives on ``CharacterTraitModel.value``. This keeps level-,
XP-, and milestone-based systems equally first-class.

Item ownership (recap; canonical comment on ItemInstanceModel)
--------------------------------------------------------------
``owner_character_id`` is **assignment / responsibility / who-would-
notice-it-missing** — *not* the equipping character. Equipping is a
spatial concern modelled by an equipment-slot ``LocationModel`` bound to
a character. The item's bearer (location chain) and its owner may
disagree without contradiction; theft is the natural state where they
disagree without an intervening consensual ``TransactionLog``.
"""
