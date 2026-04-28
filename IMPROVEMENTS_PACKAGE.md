# Pip Package Conversion — Outstanding Work

This document tracks the remaining work to ship `serverframework` as a
fully self-contained pip package, picking up from commit `cf5cc68`
("Lay groundwork for pip-installable framework") on branch
`claude/pip-package-conversion-wO0OQ`.

The groundwork commit was deliberately additive: it introduced the
`serverframework` façade, the `lib/Paths.py` resolution layer, and an
`extensions_path` parameter on `ExtensionRegistry`, all without
disturbing existing call sites. The items below are the breaking or
ecosystem-shaping changes that were intentionally left for follow-up.

## Severity legend

- **Critical** — required before the package can be safely published to
  PyPI; without these the wheel will collide with other packages or
  fail to load extensions in non-default layouts.
- **High** — needed to honor the stated end state ("`main.py` is just
  an `import` plus a couple of parameters and an extensions path").
- **Medium** — cleanups that compound on the work above; defer if
  needed but they make every later change cheaper.
- **Low** — ergonomics and polish.

---

## Item P1 — Rename top-level packages under a single namespace
**Severity: Critical**

### Problem
The framework currently exposes `lib/`, `logic/`, `database/`,
`endpoints/`, `extensions/`, `sdk/`, and `pydantic2/` as **top-level**
packages. Names like `lib` and `database` are virtually guaranteed to
collide with other packages on a consumer's `sys.path`, and `logic` /
`endpoints` are generic enough that any non-trivial application is
likely to want them too.

The façade we shipped in `cf5cc68` papers over this for the moment by
inserting `src/` onto `sys.path` at import time, but that's a
short-term hack. As soon as a consumer has their own `lib/` or
`database/` package, imports will resolve to whichever one happened to
land on the path first.

### Deliverable
Move every top-level package under a single namespace, e.g.:

```
src/serverframework/
    lib/
    logic/
    database/
    endpoints/
    extensions/
    pydantic2/
    app.py
    bootstrap.py
    __init__.py
```

Then rewrite every absolute import in the tree:

- `from lib.Logging import logger` → `from serverframework.lib.Logging import logger`
- `from logic.BLL_Auth import UserManager` → `from serverframework.logic.BLL_Auth import UserManager`
- `from database.DatabaseManager import DatabaseManager` → `from serverframework.database.DatabaseManager import DatabaseManager`
- …and so on for every `from extensions.…`, `from endpoints.…`,
  `from pydantic2.…` site.

This touches **hundreds of import sites** but the change is mechanical
and can be driven by a codemod (`ruff check --select I --fix` won't
help; use a targeted `sed` or `libcst` script).

### Notes / pitfalls
- Test discovery patterns (`pytest`'s `python_files = "*_test.py"`
  with `--import-mode=importlib`) need to keep working — verify after
  the rename.
- The `extensions/<name>/EXT_…` import strings constructed dynamically
  inside `ExtensionRegistry` need updating: today they say
  `f"extensions.{ext_name}.{file}"`, after the rename they need to say
  `f"serverframework.extensions.{ext_name}.{file}"` (and the path
  override case from P2 below produces neither).
- Migration env scripts under `database/migrations/env.py` likely
  reference `database.…` modules — update those too.
- `sdk/` is excluded from this rename; see Item P5.

### Removes the need for
- The `sys.path.insert(0, str(_SRC_DIR))` hack in
  `serverframework/__init__.py`.
- The `from app import …` line in the same file (becomes
  `from serverframework.app import …`).

---

## Item P2 — Out-of-tree extension import support
**Severity: Critical**

### Problem
`ExtensionRegistry.__init__` already accepts `extensions_path` as of
`cf5cc68`, and the path-resolution helpers honor it. But the actual
**module loading** still goes through `importlib.import_module(
"extensions.<name>.<file>")`, which only works when the extensions
directory lives at `<sys.path entry>/extensions/`. If a consumer
points us at `./my_extensions`, the directory walk finds the right
files but `importlib.import_module` cannot import them.

### Deliverable
Replace every `importlib.import_module(...)` call that targets an
extension module with `importlib.util.spec_from_file_location` +
`module_from_spec` + `spec.loader.exec_module`, using a synthesized
module name (e.g. `serverframework_ext_<name>_<file>` or registered
under `extensions.<name>.<file>` so existing intra-extension imports
keep resolving).

Sites to fix:

- `extensions/AbstractExtensionProvider.py` —
  `_register_dependencies` (the `dep_module_pattern` branch),
  `discover_extension_models`, `_discover_extension_providers`, the
  `classproperty` versions on `AbstractStaticExtension` (`providers`,
  `types`, `models`).
- `lib/Pydantic.py` — `scoped_import` (the BLL/PRV walker around line
  2330) and the EP-loader around line 2945.

A reusable helper in `lib/Paths.py` (or a new
`lib/ExtensionLoader.py`) is worth pulling out — every site does the
same dance.

### Why this is critical
Without it, the `extensions_path` parameter we already shipped is a
lie: registration walks the right directory but the registry ends up
empty for any extension whose source lives outside the package.

### Notes / pitfalls
- Intra-extension imports (`from extensions.payment.BLL_Payment import
  …` inside `extensions/payment/EP_Payment.py`) need to keep working.
  Easiest: register the synthesized module under both its package-
  qualified and file-based names in `sys.modules`.
- Migration discovery for out-of-tree extensions has the same problem;
  see Item P3.

---

## Item P3 — Extension-aware migration discovery
**Severity: High**

### Problem
Each extension can ship its own `migrations/versions/` tree, and
Alembic discovers them via `database/migrations/env.py`. That env
script currently assumes `<src>/extensions/<name>/migrations/` —
fine when extensions live in-package, broken when they don't.

### Deliverable
1. Make `env.py` (and any `MigrationManager` discovery) consult
   `lib.Paths.extensions_dir()` instead of computing the path
   inline.
2. When the registry is constructed with `extensions_path`, that path
   becomes the search root for migrations as well as for code.
3. Confirm that Alembic's `script_location` setup tolerates multiple
   roots (one for the framework's core migrations, N for each
   extension). May need to splice extension migration directories in
   at runtime.

### Why now
Once Item P1 lands, the `database.migrations` package moves under
`serverframework.database.migrations` and the env script moves with
it. That's a natural moment to also fix the discovery path, since the
file is being touched anyway.

---

## Item P4 — Console entry point + `python -m serverframework`
**Severity: High**

### Problem
The promised end state is "`main.py` is just `from serverframework
import run; run(...)`". That works as of `cf5cc68`. But for the case
where someone wants to invoke the server without writing a `main.py`
at all, we should also expose:

- A console script: `server-framework run --extensions payment
  --extensions-path ./exts`
- A module entry point: `python -m serverframework run …`

### Deliverable
1. Add a `serverframework/__main__.py` that parses argv (argparse) and
   forwards to `run()`.
2. Add a `[project.scripts]` entry to `pyproject.toml`:
   ```toml
   [project.scripts]
   server-framework = "serverframework.cli:main"
   ```
3. Make the existing `python app.py` path forward to the same CLI so
   there's one source of truth for the run loop.

### Notes
The bootstrap (`bootstrap.py`) is now self-contained and can be
invoked from the CLI as a separate subcommand
(`server-framework bootstrap`) for the "first run on a fresh
checkout" case.

---

## Item P5 — Split SDK into its own pip package
**Severity: High**

### Problem
The SDK is a separate ship by design: it should be deployed by the
server based on what extensions are loaded, not bundled with the
server. Today it lives under `src/sdk/` and is included in the
server's wheel.

### Deliverable
1. Move `src/sdk/` to its own package (still in this repo for now —
   monorepo with two `pyproject.toml` files is fine).
2. Give it a `pyproject.toml` of its own with `name =
   "serverframework-sdk"`.
3. Decide on the extension-discovery mechanism. Recommendation:
   **Python entry points** under a group like
   `serverframework.sdk_extension`. Each extension package that wants
   to expose SDK surface declares an entry point; the SDK enumerates
   them at import time and assembles a client.
4. Remove `sdk*` from the server's `[tool.setuptools.packages.find]`
   include list once the split lands.

### Why this matters
- Decouples SDK release cadence from server release cadence.
- Lets consumers `pip install serverframework-sdk` without dragging in
  fastapi, sqlalchemy, alembic, etc.
- Aligns with the user's stated mental model: "the SDK would be a
  separate ship that the server itself could deploy based on its
  extensions."

### Open question
Whether to also explore a server-side codegen or OpenAPI-driven
approach (the server already exposes `/openapi.json`). Entry points
gives static typing fidelity; OpenAPI gives extension-agnosticism
without a publish step. Pick one; both is over-engineering.

---

## Item P6 — Lazy environment variable lookup
**Severity: Medium**

### Problem
Several places in `app.py` invoke `env(...)` in **default argument
expressions**, e.g.:

```python
def instance(db_prefix: str = "", extensions: str = env("APP_EXTENSIONS")):
```

Default arguments evaluate at module-import time, so the value of
`APP_EXTENSIONS` is captured the moment `app.py` is first imported.
That's fine when `python app.py` sets up env first, but it breaks the
"import the package, then configure it" flow that the façade enables:

```python
import serverframework        # APP_EXTENSIONS captured now (probably empty)
os.environ["APP_EXTENSIONS"] = "payment"
serverframework.run()         # too late
```

The façade's `run()` works around this by setting env *before*
importing `app`, but anyone calling `instance()` directly is exposed.

### Deliverable
Replace `default=env(...)` patterns with `default=None` plus an
in-body fallback:

```python
def instance(db_prefix: str = "", extensions: Optional[str] = None):
    if extensions is None:
        extensions = env("APP_EXTENSIONS")
    ...
```

Sites to audit:
- `app.py` — `instance`, `create_registry_with_db_manager`.
- Anywhere else `grep -n "= env(" src/**/*.py` turns up at function
  signatures.

### Notes
This is the kind of change that's easy to do wrong: if `extensions=""`
and `extensions=None` are meant to behave differently (one means "no
extensions", the other means "use the env default"), preserve that
distinction.

---

## Item P7 — Drop `sys.path` mutation from the façade
**Severity: Medium**

### Problem
`serverframework/__init__.py` currently does:

```python
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
from app import build_app, instance
```

This is the mechanism that lets the façade import the un-renamed
top-level modules. It's load-bearing today but evil long-term:
mutating `sys.path` from a library is exactly the sort of thing that
makes packages hard to vendor, embed, or run from a zipapp.

### Deliverable
Remove the `sys.path` insertion and the `from app import …` once Item
P1 lands. Replace with `from serverframework.app import …`.

This is purely a cleanup that's blocked on P1.

---

## Item P8 — Remove `version` sibling file in favor of metadata
**Severity: Low**

### Problem
`build_app` reads a sibling `version` file (now with an
`importlib.metadata.version` fallback). Once the package is installed
from a wheel, the metadata is authoritative; the sibling file is dead
weight.

### Deliverable
1. Remove `src/version` from the repo.
2. Remove the file-read fallback in `build_app`.
3. Source `[project.version]` from a single place (e.g. dynamic
   versioning via `setuptools-scm` keyed off git tags) so the version
   string is never out of sync with the release.

### Why low priority
Works fine as-is; this is paperwork.

---

## Item P9 — Documented public API surface
**Severity: Low**

### Problem
`serverframework/__init__.py` re-exports `instance`, `build_app`,
`run`, and `set_extensions_root`. There's no formal contract about
what's stable vs. internal — anyone who imports
`serverframework.lib.Pydantic.ModelRegistry` is doing so at their own
risk, but nothing tells them so.

### Deliverable
1. Add `__all__` to `serverframework/__init__.py` listing the
   committed public API.
2. Document in the package docstring that anything else is internal
   and may change without notice.
3. Optionally: add a `serverframework.types` module that re-exports
   the Pydantic models consumers need to type-hint against.

### Why low priority
Nobody is depending on internals yet; lock this down before the first
external consumer ships.

---

## Cross-cutting: testing strategy

None of the items above can land safely without a test pass. The
existing `pytest` suite is the natural check — it should keep passing
through every rename. Suggested order of operations:

1. Run the suite on `claude/pip-package-conversion-wO0OQ` as it
   stands today; record the baseline.
2. Land P2 (out-of-tree extension import support) first — it's the
   highest-leverage change and doesn't require touching imports
   everywhere.
3. Land P1 (the rename) as a single atomic commit. Run the suite
   immediately. Expect noise in conftest / import-mode interactions;
   budget half a day for fallout.
4. P3 / P4 / P5 are independent of each other once P1 is in.
5. P6 / P7 / P8 / P9 are cleanup and can be done in any order.

## Dependency graph

```
P1 (rename) ──┬──► P3 (migration discovery)
              ├──► P4 (CLI entry point)
              ├──► P7 (drop sys.path hack)
              └──► P9 (public API doc)

P2 (out-of-tree imports) ──► (independent)

P5 (SDK split) ──► (independent of all above)

P6 (lazy env)   ──► (independent)

P8 (version)    ──► (independent)
```

P1 is the keystone. Everything else is either independent of it or
strictly downstream.
