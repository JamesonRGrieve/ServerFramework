# SPDX-License-Identifier: AGPL-3.0-or-later
"""GraphQL database provider (Provider Rotation System, static).

Ported from the pre-zephyrex AGInfrastructure GraphQL provider into the current
static ``AbstractDatabaseExtensionProvider`` format. Talks to a GraphQL HTTP
endpoint; ``execute_query`` runs a GraphQL document.
"""

import base64
from typing import Any, Dict, List

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger

try:  # optional driver — guarded so discovery never fails on a missing package
    from gql import Client as _GqlClient
    from gql import gql as _gql
    from gql.transport.requests import RequestsHTTPTransport as _RequestsHTTPTransport

    _gql_available = True
except ImportError:  # pragma: no cover - optional driver
    _GqlClient = None  # type: ignore[assignment]
    _gql = None  # type: ignore[assignment]
    _RequestsHTTPTransport = None  # type: ignore[assignment]
    _gql_available = False


class PRV_GraphQL(AbstractDatabaseProvider):
    """GraphQL endpoint provider (static, rotation-compatible)."""

    name: str = "GraphQL"
    friendly_name: str = "GraphQL Endpoint"
    description: str = "GraphQL HTTP endpoint database provider"
    db_type: str = "graphql"

    _env: Dict[str, Any] = {
        "DATABASE_HOST": "localhost",
        "DATABASE_PORT": "4000",
        "DATABASE_USERNAME": "",
        "DATABASE_PASSWORD": "",
        "GRAPHQL_ENDPOINT": "",
        "GRAPHQL_API_KEY": "",
    }

    _abilities = {"database", "data_storage", "graph_db", "graphql"}

    _connection_config: Dict[str, Any] = {}

    @classmethod
    def bond_instance(cls, config: Dict[str, Any]) -> None:
        """Configure the provider from a config dict (falls back to env)."""
        try:
            host = config.get("database_host") or env("DATABASE_HOST") or "localhost"
            port = config.get("database_port") or env("DATABASE_PORT") or 4000
            endpoint = (
                config.get("graphql_endpoint")
                or env("GRAPHQL_ENDPOINT")
                or f"http://{host}:{port}/graphql"
            )
            headers: Dict[str, str] = dict(config.get("graphql_headers") or {})
            username = config.get("database_username") or env("DATABASE_USERNAME")
            password = config.get("database_password") or env("DATABASE_PASSWORD")
            api_key = config.get("api_key") or env("GRAPHQL_API_KEY")
            if username and password:
                token = base64.b64encode(f"{username}:{password}".encode()).decode()
                headers["Authorization"] = f"Basic {token}"
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            cls._connection_config = {
                **config,
                "graphql_endpoint": endpoint,
                "graphql_headers": headers,
            }
            logger.debug("GraphQL provider bonded with endpoint: %s", endpoint)
        except Exception as e:
            logger.error(f"Failed to configure GraphQL provider: {e}")
            raise

    @classmethod
    def _get_client(cls):
        """Build a gql Client over an HTTP transport."""
        if not _gql_available:
            logger.error("gql package not available")
            return None
        cfg = cls._connection_config
        try:
            transport = _RequestsHTTPTransport(
                url=cfg.get("graphql_endpoint"),
                headers=cfg.get("graphql_headers", {}),
                verify=True,
                retries=3,
            )
            return _GqlClient(transport=transport, fetch_schema_from_transport=True)
        except Exception as e:
            logger.error(f"Error connecting to GraphQL endpoint: {e}")
            return None

    @classmethod
    async def execute_query(cls, query: str, **kwargs) -> str:
        """Execute a GraphQL document against the endpoint."""
        client = cls._get_client()
        if not client:
            return "Error connecting to GraphQL endpoint"
        try:
            if "```graphql" in query:
                query = query.split("```graphql")[1].split("```")[0]
            query = query.replace("```", "").strip()
            import json

            result = client.execute(_gql(query))
            return json.dumps(result, default=str)
        except Exception as e:
            logger.error(f"Error executing GraphQL query: {e}")
            return f"Error executing GraphQL query: {str(e)}"

    @classmethod
    async def execute_sql(cls, query: str, **kwargs) -> str:
        """GraphQL is not relational; SQL is redirected to execute_query."""
        return (
            "GraphQL does not support SQL. Send a GraphQL document to "
            "execute_query instead."
        )

    @classmethod
    async def get_schema(cls, **kwargs) -> str:
        """Return the endpoint's introspected schema, if reachable."""
        client = cls._get_client()
        if not client:
            return "Error connecting to GraphQL endpoint"
        try:
            schema = getattr(client, "schema", None)
            if schema is None:
                return "GraphQL schema not available from transport"
            try:
                from graphql import print_schema

                return print_schema(schema)
            except Exception:
                return str(schema)
        except Exception as e:
            logger.error(f"Error getting GraphQL schema: {e}")
            return f"Error getting schema: {str(e)}"

    @classmethod
    async def chat_with_db(cls, request: str, **kwargs) -> str:
        """Return the GraphQL schema plus guidance."""
        schema = await cls.get_schema(**kwargs)
        return (
            f'Natural language query: "{request}"\n\n'
            f"GraphQL schema:\n{schema}\n\n"
            "Send a GraphQL document to execute_query to run it."
        )

    @classmethod
    def validate_config(cls) -> List[str]:
        """Return a list of configuration problems (empty when healthy)."""
        issues: List[str] = []
        if not _gql_available:
            issues.append("gql driver not installed")
        endpoint = cls._connection_config.get("graphql_endpoint") or env(
            "GRAPHQL_ENDPOINT"
        )
        if not endpoint and not (env("DATABASE_HOST")):
            issues.append("GraphQL endpoint not configured")
        return issues

    @classmethod
    async def write_data(cls, data: str, **kwargs) -> str:
        """Run a GraphQL mutation document."""
        return await cls.execute_query(data, **kwargs)
