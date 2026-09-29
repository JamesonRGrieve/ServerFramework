# SPDX-License-Identifier: AGPL-3.0-or-later
"""MongoDB database provider (Provider Rotation System, static).

Ported from the pre-zephyrex AGInfrastructure MongoDB provider into the current
static ``AbstractDatabaseExtensionProvider`` format. MongoDB is a document
store, so ``execute_query`` accepts a JSON command envelope rather than SQL.
The rotated instance carries the password in ``api_key`` and the database name
in its ``database_name`` setting; a ``connection_string`` setting (or
``MONGODB_CONNECTION_STRING``) replaces host/port/credentials.
"""

import json
from typing import Any, ClassVar, Dict, List, Optional

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

try:  # optional driver — guarded so discovery never fails on a missing package
    from pymongo import MongoClient as _MongoClient

    _pymongo_available = True
except ImportError:  # pragma: no cover - optional driver
    _MongoClient = None  # type: ignore[assignment]
    _pymongo_available = False

MONGODB_DEFAULT_PORT = 27017
MONGODB_DEFAULT_FIND_LIMIT = 25
# Bounds the reachability ping so an unreachable server fails over promptly.
MONGODB_SERVER_SELECTION_TIMEOUT_MS = 5000
MONGODB_OPERATIONS = {"find", "count", "aggregate", "insert", "update", "delete"}


class PRV_MongoDB(AbstractDatabaseProvider):
    """MongoDB document database provider (static, rotation-compatible)."""

    name: ClassVar[str] = "MongoDB"
    friendly_name: ClassVar[str] = "MongoDB Database"
    description: ClassVar[str] = "MongoDB document (NoSQL) database provider"
    db_type: ClassVar[str] = "mongodb"

    _env: ClassVar[Dict[str, Any]] = {
        "DATABASE_HOST": "",
        "DATABASE_PORT": "27017",
        "DATABASE_NAME": "",
        "DATABASE_USERNAME": "",
        "DATABASE_PASSWORD": "",
        "MONGODB_CONNECTION_STRING": "",
    }

    _abilities = {"database", "data_storage", "nosql_db", "document_db"}

    @classmethod
    def connection_config(
        cls, instance: Optional[ProviderInstanceModel]
    ) -> Dict[str, Any]:
        return {
            **cls.server_config(instance, MONGODB_DEFAULT_PORT),
            "connection_string": cls.resolve_setting(
                instance, "connection_string", "MONGODB_CONNECTION_STRING"
            ),
        }

    @classmethod
    def _get_client(cls, config: Dict[str, Any]) -> Any:
        """A MongoClient for the resolved configuration, verified reachable."""
        cls.require_driver(_pymongo_available, "pymongo")
        cls.require_config(config, "database_name")
        if config["connection_string"]:
            uri = config["connection_string"]
        else:
            cls.require_config(config, "database_host")
            user = config["database_username"]
            password = config["database_password"]
            credentials = f"{user}:{password}@" if user and password else ""
            uri = (
                f"mongodb://{credentials}{config['database_host']}:"
                f"{config['database_port']}/{config['database_name']}"
            )
        try:
            client = _MongoClient(
                uri, serverSelectionTimeoutMS=MONGODB_SERVER_SELECTION_TIMEOUT_MS
            )
            client.admin.command("ping")
        except Exception as exc:
            raise cls.connection_failed(exc) from exc
        return client

    @classmethod
    def _parse_envelope(cls, query: str) -> Dict[str, Any]:
        """The JSON command envelope ``{collection, operation, filter, ...}``."""
        if "```json" in query:
            query = query.split("```json")[1].split("```")[0]
        try:
            spec = json.loads(query.strip())
        except json.JSONDecodeError as exc:
            raise InvalidInputExternalError(
                "MongoDB query must be a valid JSON command envelope",
                provider=cls.name,
                cause=exc,
            ) from exc
        if not isinstance(spec, dict) or not spec.get("collection"):
            raise InvalidInputExternalError(
                "'collection' is required in the query envelope", provider=cls.name
            )
        operation = str(spec.get("operation") or "find").lower()
        if operation not in MONGODB_OPERATIONS:
            raise InvalidInputExternalError(
                f"unsupported operation '{operation}'", provider=cls.name
            )
        spec["operation"] = operation
        return spec

    @staticmethod
    def _run_operation(collection: Any, spec: Dict[str, Any]) -> str:
        filter_ = spec.get("filter", {})
        operation = spec["operation"]
        if operation == "find":
            limit = int(spec.get("limit", MONGODB_DEFAULT_FIND_LIMIT))
            docs = list(collection.find(filter_, spec.get("projection")).limit(limit))
            for doc in docs:
                doc["_id"] = str(doc.get("_id"))
            return json.dumps(docs, default=str)
        if operation == "count":
            return str(collection.count_documents(filter_))
        if operation == "aggregate":
            docs = list(collection.aggregate(spec.get("pipeline", [])))
            return json.dumps(docs, default=str)
        if operation == "insert":
            result = collection.insert_many(spec.get("documents", []))
            return f"Inserted {len(result.inserted_ids)} document(s)."
        if operation == "update":
            result = collection.update_many(filter_, spec.get("update", {}))
            return f"Modified {result.modified_count} document(s)."
        result = collection.delete_many(filter_)
        return f"Deleted {result.deleted_count} document(s)."

    @classmethod
    async def execute_query(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        """Run a JSON command envelope: {collection, operation, filter, ...}."""
        spec = cls._parse_envelope(query)
        config = cls.bond_instance(instance).config
        client = cls._get_client(config)
        try:
            collection = client[config["database_name"]][spec["collection"]]
            return cls._run_operation(collection, spec)
        except Exception as exc:
            raise cls.query_failed(exc, "MongoDB query") from exc
        finally:
            client.close()

    @classmethod
    async def execute_sql(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        """MongoDB is not relational; SQL is refused in favour of execute_query."""
        raise InvalidInputExternalError(
            "MongoDB does not support SQL. Send a JSON command envelope to "
            'execute_query, e.g. {"collection": "users", "operation": '
            '"find", "filter": {}}.',
            provider=cls.name,
        )

    @classmethod
    async def get_schema(cls, instance: ProviderInstanceModel, **kwargs: Any) -> str:
        """List collections and infer each one's fields from a sample document."""
        config = cls.bond_instance(instance).config
        client = cls._get_client(config)
        lines: List[str] = []
        try:
            db = client[config["database_name"]]
            for collection_name in db.list_collection_names():
                sample = db[collection_name].find_one()
                if sample:
                    fields = ", ".join(
                        f"{k}: {type(v).__name__}" for k, v in sample.items()
                    )
                    lines.append(f"// collection {collection_name}: {{ {fields} }}")
                else:
                    lines.append(f"// collection {collection_name}: (empty)")
        except Exception as exc:
            raise cls.query_failed(exc, "schema query") from exc
        finally:
            client.close()
        return "\n".join(lines) if lines else "No collections found"

    @classmethod
    async def chat_with_db(
        cls, instance: ProviderInstanceModel, request: str, **kwargs: Any
    ) -> str:
        """Return the collection schema plus guidance."""
        return await cls.schema_guidance(
            instance,
            request,
            "Send a JSON command envelope to execute_query to run it.",
            schema_label="Collections",
            **kwargs,
        )

    @classmethod
    def validate_config(cls) -> List[str]:
        """Configuration problems of the environment-configured database."""
        issues: List[str] = []
        if not _pymongo_available:
            issues.append("pymongo driver not installed")
        config = cls.connection_config(None)
        if not (config["connection_string"] or config["database_host"]):
            issues.append("MongoDB host or connection string not configured")
        if not config["database_name"]:
            issues.append("MongoDB database name not configured")
        return issues

    @classmethod
    async def write_data(
        cls, instance: ProviderInstanceModel, data: str, **kwargs: Any
    ) -> str:
        """Insert documents via a JSON insert envelope."""
        return await cls.execute_query(instance, data, **kwargs)
