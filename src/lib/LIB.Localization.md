# Localization Subsystem

> Item 78 — primary reference for the Localization primitive. The architectural summary is here so extension authors can localize user-facing strings (email templates, error messages, audit-log copy) without spelunking through `src/Localization.py`. Public-symbol stability is governed by `EXT.Contracts.md` (Item 52).

## Purpose
`src/Localization.py` (singleton `Localization` class plus four module-level helpers) loads `docs.<locale>.json` files from the project root, exposes per-entity / per-property / per-endpoint translated strings, and contributes a `@localized_model` SQLAlchemy decorator that derives `__tablename__`, foreign-key targets, and `back_populates` strings from the active locale's metadata. The subsystem is the single backing store for user-facing copy across BLL models, DB models, and Swagger/OpenAPI docs.

## Files
- `src/Localization.py` — the singleton, the model decorator, and the schema-docs generator.
- `docs.<locale>.json` (project root) — the per-locale dictionary. Each JSON file is keyed by `domain.entity` with sub-dictionaries for `comment`, `singular`, `plural`, `properties`, `relationships`. Adding a new locale is equivalent to dropping a new `docs.fr.json` (or similar) next to the existing `docs.en.json`.

## Public Surface
The Localization subsystem exposes one class, four helpers, and one decorator. All are imported as `from Localization import …`.

### `class Localization` (singleton)
- `Localization()` — `__new__` returns the singleton; first construction loads every `docs.*.json` it finds.
- `set_locale(locale: str) -> bool` — switches the active locale; returns `False` if the locale was not loaded.
- `get_available_locales() -> List[str]` — enumerates loaded locales.
- `get_entity_metadata(domain, entity) -> Dict[str, Any]` — returns the raw metadata dict for an entity (keys: `comment`, `singular`, `plural`, `properties`, `relationships`).
- `get_entity_comment(domain, entity) -> str` — translated table comment. Used as the SQLAlchemy `__table_args__["comment"]` value when `@localized_model` is applied.
- `get_entity_singular(domain, entity) -> str` — translated singular noun.
- `get_relationship_backref_name(domain, entity, target_entity) -> str` — derives the `back_populates` string for a relationship from the locale dictionary so paired models do not have to hand-write matching names.
- `create_localized_foreign_key(target_entity, name=None, **kwargs) -> Column` — builds a SQLAlchemy `Column` whose foreign-key target is the locale-derived table name for `target_entity`. Use via the module-level `foreign_key()` shim.
- `apply_relationship_naming(domain, entity, target_entity, **kwargs)` — fills in `back_populates` (and a few other relationship knobs) from the active locale before delegating to `sqlalchemy.orm.relationship`. Use via the module-level `relationship()` shim.
- `get_property_comment(domain, entity, property_name) -> str` — translated column comment, applied to `Column.comment` by `@localized_model`.
- `get_db_doc(class_name: str) -> str` — top-level docstring for a SQLAlchemy class; consumed by the schema-docs generator.
- `get_swagger_doc(endpoint: str) -> str` — translated Swagger / OpenAPI description for a route.
- `get_tablename_from_entity(domain, entity) -> str` — locale-derived plural string used as `__tablename__` when the decorated model omits it.
- `apply_to_class(cls)` — decorator's main worker; sets `__tablename__`, `__table_args__["comment"]`, per-column `Column.comment`, and rewrites `relationship` / `foreign_key` calls to use locale-derived names.

### Module-Level Helpers
- `relationship(*args, **kwargs)` — drop-in replacement for `sqlalchemy.orm.relationship` that consults the active locale for `back_populates`.
- `foreign_key(target_entity, name=None, **kwargs)` — produces a `ForeignKey`-bearing `Column` whose target is the locale-derived table name.
- `@localized_model` — class decorator. Applies all of the above to a SQLAlchemy declarative class. Composes with `BaseMixin` / `UpdateMixin`.
- `generate_schema_docs()` — walks every loaded model and emits a per-locale schema-doc bundle (used by the Swagger-doc generation pipeline).
- `update_entity_definition(domain, entity, ...)` — programmatic editor for locale dictionaries. Tooling-only; not part of the runtime contract.

## Architectural Constraints
- **Singleton, not per-request.** The locale is a process-global. Per-request locale switching (e.g. for an i18n-aware HTTP middleware) is currently out of scope; if and when it lands it will live behind the `RequestContext` (Item 47's request-scoped context vars).
- **Locale files are JSON.** Reading them at startup is acceptable; the framework does not yet support hot-reload of translations. Operators who change strings restart the process.
- **`@localized_model` is opt-in.** Models that do not need locale-derived metadata keep using the standard SQLAlchemy syntax. The decorator is purely additive.

## Cross-References
- [`Framework.md`](../Framework.md) — top-level overview lists Localization alongside the other foundational subsystems.
- [`LIB.Overview.md`](./LIB.Overview.md) — pointer to this file, kept under "System Utilities".
- `docs.en.json` — the canonical English dictionary; consult before adding new entities so the keys match what `@localized_model` will look up.
- `EXT.Contracts.md` (Item 52, when populated) — public-API contract entries for `Localization`, `localized_model`, `relationship`, `foreign_key`. Anything in `Localization.py` not enumerated in `EXT.Contracts.md` is internal and may change without notice.

## Extension Author Workflow
1. Add an entry to `docs.<locale>.json` keyed by `<your_extension>.<EntityName>` describing the entity's translated copy.
2. Decorate your SQLAlchemy class with `@localized_model`.
3. Use the module-level `relationship()` and `foreign_key()` helpers in place of the SQLAlchemy originals so the relationship metadata is locale-derived.
4. For user-facing error / email / audit copy outside SQLAlchemy, call `Localization().get_entity_comment(...)` / `get_property_comment(...)` directly.
