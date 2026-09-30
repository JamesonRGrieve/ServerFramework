# Database Seeding

Every boot seeds the rows the framework and its loaded extensions ship with:
the root and system users, roles, extensions and their abilities, providers,
their root instances and rotations. Seeding is idempotent: a row that already
exists is left alone, so restarts and redeploys are safe.

`ModelRegistry._seed()` drives it; `StaticSeeder.py` holds the per-model
helpers.

## Declaring seed data

A bound Pydantic model declares its rows with `seed_data`, either a list or a
classmethod that builds the list at boot (optionally taking `model_registry`):

```python
class ExampleModel(ApplicationModel, metaclass=ModelMeta):
    name: str

    seed_data: ClassVar[List[Dict[str, Any]]] = [
        {"name": "Example 1"},
        {"name": "Example 2"},
    ]
```

Items may be dicts or instances of the model (validated at import, so a
mistyped field fails early). Rows are created as `SYSTEM_ID` unless the model
sets `seed_creator_id`.

An item already exists when a row matches its `id`, else its `name`, else its
`email`; existing rows are never updated by seeding.

## Naming a parent instead of its id

A seed item can name the row it belongs to; the seeder replaces the name with
the id before creating it:

| Placeholder               | Becomes                | Looked up in       |
|---------------------------|------------------------|--------------------|
| `_extension_name`         | `extension_id`         | extensions         |
| `_provider_name`          | `provider_id`          | providers          |
| `_rotation_name`          | `rotation_id`          | rotations          |
| `_provider_instance_name` | `provider_instance_id` | provider instances |

(`"extension_id": "EXT:<name>"` is the legacy spelling of `_extension_name`.)

## Order

Models are seeded parents first, by the foreign-key order of their tables.
Many reference columns carry no foreign key, so that order is not complete:
an item whose named parent does not exist yet is deferred, and deferred items
are retried after every model has been seeded, until a pass resolves nothing
more. A brand-new database is therefore fully seeded by its first boot. An
item whose parent never appears is skipped with a warning naming it.

## Troubleshooting

Seeding logs at the `SQL` level (`LOG_LEVEL=SQL` shows every lookup and
insert). A warning `Seed item for <Model> skipped: provider 'x' is not
seeded` means no loaded extension ships the parent the item names: check the
item's placeholder and that the extension shipping the parent is loaded.
`Seeding_first_boot_test.py` boots a fresh database and checks that every
parent/child family is seeded in one boot.
