"""genealogy extension.

Owns ``PersonModel`` and ``RelationshipModel`` — a directed labelled-edge
graph between entities.

Standalone genealogy
--------------------
On its own, the extension is a self-hosted-Ancestry-style tree:
``Person`` records (name, birth/death, biography, gender) connected by
``Relationship`` rows whose endpoints are both Persons. The ``kind``
column carries the ancestry / partnership / sibling / social labels;
``discriminator`` carries the subkind ("biological" vs "adopted",
"marriage" vs "engagement"); ``valid_from`` / ``valid_to`` carry
temporal validity. GEDCOM I/O and family-tree algorithms (ancestors_of,
descendants_of, MRCA, kinship_degree) are layered on top.

Cross-extension extension
-------------------------
``RelationshipModel`` is designed to be widened by downstream extensions
via ``@extension_model`` (see ``payment`` for the canonical example). A
downstream extension may inject additional endpoint columns (e.g.
``faction_id`` / ``target_faction_id``) and is responsible for adding
the corresponding ALTER TABLE in its own migration plus a CHECK
constraint enforcing endpoint XOR (exactly one endpoint per side).

Genealogy itself does not know about Factions, Organizations, or any
other entity kind. It owns ``person_id`` (subject) and
``target_person_id`` (object) and trusts downstream extensions to keep
their injected endpoints consistent.

Edge doctrine
-------------
- **Directed.** Every row is one directed edge. Symmetric semantics
  (rivalry, partnership) are achieved by inserting two rows or by query
  convention ("OR person_id=X OR target_person_id=X"); not by a
  ``is_symmetric`` flag.
- **Validity is nullable both ends.** ``valid_from = NULL`` means "from
  the dawn of time"; ``valid_to = NULL`` means "still in effect".
- **`kind` is free-form** so downstream extensions can introduce their
  own labels without a schema change.
- **`intensity` is signed**: -1.0 nemesis ↔ +1.0 closest. Reused for
  reputation, regard, partnership closeness, etc.
- **`qualifier` is conditional context** ("since the war", "while she
  lived"). Distinct from ``discriminator`` (the subkind label).
"""
