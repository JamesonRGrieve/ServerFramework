# SPDX-License-Identifier: AGPL-3.0-or-later
"""MongoDB database provider (Provider Rotation System, static).

Ported from the pre-zephyrex AGInfrastructure MongoDB provider into the current
static ``AbstractDatabaseExtensionProvider`` format. MongoDB is a document
store, so ``execute_query`` accepts a JSON command envelope rather than SQL.
"""

import json
from typing import Any, Dict, List

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger

try:  # optional driver — guarded so discovery never fails on a missing package
    from pymongo import MongoClient as _MongoClient

    _pymongo_available = True
except ImportError:  # pragma: no cover - optional driver
    _MongoClient = None  # type: ignore[assignment]
    _pymongo_available = False


class PRV_MongoDB(AbstractDatabaseProvider):
    """MongoDB document database provider (static, rotation-compatible)."""

    name: str = "MongoDB"
    friendly_name: str = "MongoDB Database"
    description: str = "MongoDB document (NoSQL) database provider"
    db_type: str = "mongodb"

    _env: Dict[str, Any] = {
        "DATABASE_HOST": "",
        "DATABASE_PORT": "27017",
        "DATABASE_NAME": "",
        "DATABASE_USERNAME": "",
        "DATABASE_PASSWORD": "",
        "MONGODB_CONNECTION_STRING": "",
    }

    _abilities = {"database", "data_storage", "nosql_db", "document_db"}

    _connection_config: Dict[str, Any] = {}

    @classmethod
    def bond_instance(cls, config: Dict[str, Any]) -> None:
        """Configure the provider from a config dict (falls back to env)."""
        try:
            port_raw = config.get("database_port") or env("DATABASE_PORT") or 27017
            cls._connection_config = {
                **config,
                "connection_string": (
                    config.get("connection_string") or env("MONGODB_CONNECTION_STRING")
                ),
                "database_host": config.get("database_host") or env("DATABASE_HOST"),
                "database_port": int(port_raw),
                "database_name": config.get("database_name") or env("DATABASE_NAME"),
                "database_username": (
                    config.get("database_username") or env("DATABASE_USERNAME")
                ),
                "database_password": (
                    config.get("database_password") or env("DATABASE_PASSWORD")
                ),
            }
            logger.debug(
                "MongoDB provider bonded with host: %s",
                cls._connection_config.get("database_host"),
            )
        except Exception as e:
            logger.error(f"Failed to configure MongoDB provider: {e}")
            raise

    @classmethod
    def _get_client(cls):
        """Open a MongoClient using the bonded configuration."""
        if not _pymongo_available:
            logger.error("pymongo package not available")
            return None
        cfg = cls._connection_config
        try:
            if cfg.get("connection_string"):
                return _MongoClient(cfg["connection_string"])
            host = cfg.get("database_host")
            port = cfg.get("database_port", 27017)
            user = cfg.get("database_username")
            password = cfg.get("database_password")
            db_name = cfg.get("database_name")
            if user and password:
                uri = f"mongodb://{user}:{password}@{host}:{port}/{db_name}"
            else:
                uri = f"mongodb://{host}:{port}/{db_name}"
            return _MongoClient(uri)
        except Exception as e:
            logger.error(f"Error connecting to MongoDB: {e}")
            return None

    @classmethod
    async def execute_query(cls, query: str, **kwargs) -> str:
        """Run a JSON command envelope: {collection, operation, filter, ...}."""
        client = cls._get_client()
        if not client:
            return "Error connecting to MongoDB Database"
        try:
            if "```json" in query:
                query = query.split("```json")[1].split("```")[0]
            spec = json.loads(query.strip())
            collection_name = spec.get("collection")
            operation = (spec.get("operation") or "find").lower()
            if not collection_name:
                return "Error: 'collection' is required in the query envelope"
            db = client[cls._connection_config.get("database_name")]
            collection = db[collection_name]
            filter_ = spec.get("filter", {})
            if operation == "find":
                limit = int(spec.get("limit", 25))
                docs = list(
                    collection.find(filter_, spec.get("projection")).limit(limit)
                )
                for d in docs:
                    d["_id"] = str(d.get("_id"))
                return json.dumps(docs, default=str)
            if operation == "count":
                return str(collection.count_documents(filter_))
            if operation == "aggregate":
                pipeline = spec.get("pipeline", [])
                docs = list(collection.aggregate(pipeline))
                return json.dumps(docs, default=str)
            if operation == "insert":
                result = collection.insert_many(spec.get("documents", []))
                return f"Inserted {len(result.inserted_ids)} document(s)."
            if operation == "update":
                result = collection.update_many(filter_, spec.get("update", {}))
                return f"Modified {result.modified_count} document(s)."
            if operation == "delete":
                result = collection.delete_many(filter_)
                return f"Deleted {result.deleted_count} document(s)."
            return f"Error: unsupported operation '{operation}'"
        except json.JSONDecodeError:
            return "Error: MongoDB query must be a valid JSON command envelope"
        except Exception as e:
            logger.error(f"Error executing MongoDB query: {e}")
            return f"Error executing query: {str(e)}"
        finally:
            try:
                client.close()
            except Exception:
                pass

    @classmethod
    async def execute_sql(cls, query: str, **kwargs) -> str:
        """MongoDB is not relational; SQL is redirected to execute_query."""
        return (
            "MongoDB does not support SQL. Send a JSON command envelope to "
            'execute_query, e.g. {"collection": "users", "operation": '
            '"find", "filter": {}}.'
        )

    @classmethod
    async def get_schema(cls, **kwargs) -> str:
        """List collections and infer each one's fields from a sample document."""
        client = cls._get_client()
        if not client:
            return "Error connecting to MongoDB Database"
        try:
            db = client[cls._connection_config.get("database_name")]
            lines: List[str] = []
            for collection_name in db.list_collection_names():
                sample = db[collection_name].find_one()
                if sample:
                    fields = ", ".join(
                        f"{k}: {type(v).__name__}" for k, v in sample.items()
                    )
                    lines.append(f"// collection {collection_name}: {{ {fields} }}")
                else:
                    lines.append(f"// collection {collection_name}: (empty)")
            return "\n".join(lines) if lines else "No collections found"
        except Exception as e:
            logger.error(f"Error getting MongoDB schema: {e}")
            return f"Error getting database schema: {str(e)}"
        finally:
            try:
                client.close()
            except Exception:
                pass

    @classmethod
    async def chat_with_db(cls, request: str, **kwargs) -> str:
        """Return the collection schema plus guidance."""
        schema = await cls.get_schema(**kwargs)
        return (
            f'Natural language query: "{request}"\n\n'
            f"Collections:\n{schema}\n\n"
            "Send a JSON command envelope to execute_query to run it."
        )

    @classmethod
    def validate_config(cls) -> List[str]:
        """Return a list of configuration problems (empty when healthy)."""
        issues: List[str] = []
        if not _pymongo_available:
            issues.append("pymongo driver not installed")
        cfg = cls._connection_config
        if not (cfg.get("connection_string") or env("MONGODB_CONNECTION_STRING")):
            if not (cfg.get("database_host") or env("DATABASE_HOST")):
                issues.append("MongoDB host or connection string not configured")
        if not (cfg.get("database_name") or env("DATABASE_NAME")):
            issues.append("MongoDB database name not configured")
        return issues

    @classmethod
    async def write_data(cls, data: str, **kwargs) -> str:
        """Insert documents via a JSON insert envelope."""
        return await cls.execute_query(data, **kwargs)
