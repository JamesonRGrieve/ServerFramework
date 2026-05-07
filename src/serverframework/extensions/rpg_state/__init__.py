"""rpg_state extension.

Hard-depends on ``genealogy``. RPG **does not** define a separate
Character table — it widens ``genealogy.PersonModel`` with the
character-specific fields (``kind``, ``campaign_id``, ``user_id``) via
``@extension_model``. The ``persons`` table is the character table for
RPG users. RPG also widens ``genealogy.RelationshipModel`` with Faction
endpoints (``faction_id``, ``target_faction_id``) so that one
relationship table holds Person↔Person, Person↔Faction, and
Faction↔Faction edges; the migration adds an endpoint-XOR CHECK
constraint.

State here is the "now": GameSystems, Campaigns, Traits (with
``kind='status_effect'`` for buffs/debuffs/conditions),
DerivativeTraits (edges defining how Traits contribute to one
another), Factions, Locations, Items, Hooks (narrative DAG; replaces
Quest+Objective), and the per-person / per-faction join tables that
wire them together. Historical events (combat actions, dialogue,
transactions) live in the sibling ``rpg_log`` extension.

Schema integrity
----------------
The codebase enforces integrity at the database layer where it is
non-negotiable. Cross-extension foreign keys are real
``ForeignKeyConstraint`` declarations; required references use
``nullable=False``; the endpoint-XOR on ``relationships`` is enforced
by a SQL CHECK constraint added when ``rpg_state`` injects faction
endpoints. Manager-layer validators handle invariants that don't fit
relational constraints (cycle detection in faction/location parent
chains; equipment-slot coherence on Locations).

Membership & visibility
-----------------------
There is no ``CampaignMember`` table. Membership is inferred:

- The campaign **owner** is ``CampaignModel.user_id`` (the GM).
- A user is a **player** in a campaign iff they own at least one
  Person (``persons.user_id`` matches and ``persons.campaign_id`` is
  the campaign).

Faction membership is **not** a separate join table either — it lives
in ``RelationshipModel`` as ``kind='member_of'`` with Person→Faction
endpoints. Discriminator carries the role label ('leader', 'member',
'treasurer'). Intensity carries reputation/standing.

Granular visibility (GM-secret NPCs, hidden quests, shared handouts)
is delegated to the optional ``acl_rbac`` extension. ``rpg_state`` does
not carry visibility columns; granularity belongs to ACL.

Progression
-----------
Progression dimensions (level, experience, spent_experience,
milestones, dice pools) live in the unified Trait catalog as
``TraitModel(kind='progression')`` rows joined via ``PersonTraitModel``.
The persons table has no ``level`` column.

Item ownership (recap; canonical comment on ItemInstanceModel)
--------------------------------------------------------------
``owner_person_id`` is **assignment / responsibility / who-would-
notice-it-missing** — *not* the equipping person. Equipping is a
spatial concern modelled by an equipment-slot ``LocationModel`` bound
to a person via ``associated_person_id``. Theft is the natural state
where the bearer (location chain) and the owner disagree without an
intervening consensual ``TransactionLog``.
"""
