# SPDX-License-Identifier: AGPL-3.0-or-later
"""GraphQL database provider (Provider Rotation System, static).

Ported from the pre-zephyrex AGInfrastructure GraphQL provider into the current
static ``AbstractDatabaseExtensionProvider`` format. Talks to a GraphQL HTTP
endpoint; ``execute_query`` runs a GraphQL document. The rotated instance
carries a bearer token in ``api_key``; the endpoint, basic-auth credentials and
extra headers (a JSON object) come from its settings, each falling back to the
environment.
"""

import base64
import json
from typing import Any, ClassVar, Dict, List, Optional

from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider as AbstractDatabaseProvider,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

try:  # optional driver — guarded so discovery never fails on a missing package
    from gql import Client as _GqlClient
    from gql import gql as _gql
    from gql.transport.requests import RequestsHTTPTransport as _RequestsHTTPTransport

    _gql_available = True
except ImportError:  # pragma: no cover - optional driver
    _GqlClient = None
    _gql = None
    _RequestsHTTPTransport = None
    _gql_available = False

GRAPHQL_DEFAULT_HOST = "localhost"
GRAPHQL_DEFAULT_PORT = 4000
GRAPHQL_TRANSPORT_RETRIES = 3


class PRV_GraphQL(AbstractDatabaseProvider):
    """GraphQL endpoint provider (static, rotation-compatible)."""

    name: ClassVar[str] = "GraphQL"
    friendly_name: ClassVar[str] = "GraphQL Endpoint"
    description: ClassVar[str] = "GraphQL HTTP endpoint database provider"
    db_type: ClassVar[str] = "graphql"

    _env: ClassVar[Dict[str, Any]] = {
        "DATABASE_HOST": "localhost",
        "DATABASE_PORT": "4000",
        "DATABASE_USERNAME": "",
        "DATABASE_PASSWORD": "",
        "GRAPHQL_ENDPOINT": "",
        "GRAPHQL_API_KEY": "",
    }

    _abilities = {"database", "data_storage", "graph_db", "graphql"}

    @classmethod
    def connection_config(
        cls, instance: Optional[ProviderInstanceModel]
    ) -> Dict[str, Any]:
        endpoint = cls.resolve_setting(instance, "graphql_endpoint", "GRAPHQL_ENDPOINT")
        if not endpoint:
            host = cls.resolve_setting(
                instance, "database_host", "DATABASE_HOST", default=GRAPHQL_DEFAULT_HOST
            )
            port = cls.resolve_port(instance, "DATABASE_PORT", GRAPHQL_DEFAULT_PORT)
            endpoint = f"http://{host}:{port}/graphql"

        headers_json = cls.resolve_setting(instance, "graphql_headers")
        try:
            headers: Dict[str, str] = dict(json.loads(headers_json or "{}"))
        except (ValueError, TypeError) as exc:
            raise TransientExternalError(
                "GraphQL headers setting is not a JSON object",
                provider=cls.name,
                cause=exc,
            ) from exc
        username = cls.resolve_setting(
            instance, "database_username", "DATABASE_USERNAME"
        )
        password = cls.resolve_setting(
            instance, "database_password", "DATABASE_PASSWORD"
        )
        api_key = cls.resolve_setting(
            instance, "api_key", "GRAPHQL_API_KEY", field="api_key"
        )
        if username and password:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return {"graphql_endpoint": endpoint, "graphql_headers": headers}

    @classmethod
    def _connect(cls, config: Dict[str, Any]) -> Any:
        """A gql client connected to the endpoint, its schema fetched."""
        cls.require_driver(_gql_available, "gql")
        transport = _RequestsHTTPTransport(
            url=config["graphql_endpoint"],
            headers=config["graphql_headers"],
            verify=True,
            retries=GRAPHQL_TRANSPORT_RETRIES,
        )
        client = _GqlClient(transport=transport, fetch_schema_from_transport=True)
        try:
            client.connect_sync()
        except Exception as exc:
            raise cls.connection_failed(exc) from exc
        return client

    @classmethod
    async def execute_query(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        """Execute a GraphQL document against the endpoint."""
        client = cls._connect(cls.bond_instance(instance).config)
        if "```graphql" in query:
            query = query.split("```graphql")[1].split("```")[0]
        query = query.replace("```", "").strip()
        try:
            result = client.session.execute(_gql(query))
        except Exception as exc:
            raise cls.query_failed(exc, "GraphQL query") from exc
        finally:
            client.close_sync()
        return json.dumps(result, default=str)

    @classmethod
    async def execute_sql(
        cls, instance: ProviderInstanceModel, query: str, **kwargs: Any
    ) -> str:
        """GraphQL is not relational; SQL is refused in favour of execute_query."""
        raise InvalidInputExternalError(
            "GraphQL does not support SQL. Send a GraphQL document to "
            "execute_query instead.",
            provider=cls.name,
        )

    @classmethod
    async def get_schema(cls, instance: ProviderInstanceModel, **kwargs: Any) -> str:
        """Return the endpoint's introspected schema."""
        from graphql import print_schema

        client = cls._connect(cls.bond_instance(instance).config)
        try:
            schema = client.schema
        finally:
            client.close_sync()
        if schema is None:
            raise TransientExternalError(
                "GraphQL schema not available from transport", provider=cls.name
            )
        return str(print_schema(schema))

    @classmethod
    async def chat_with_db(
        cls, instance: ProviderInstanceModel, request: str, **kwargs: Any
    ) -> str:
        """Return the GraphQL schema plus guidance."""
        return await cls.schema_guidance(
            instance,
            request,
            "Send a GraphQL document to execute_query to run it.",
            schema_label="GraphQL schema",
            **kwargs,
        )

    @classmethod
    def validate_config(cls) -> List[str]:
        """Configuration problems of the environment-configured endpoint."""
        issues: List[str] = []
        if not _gql_available:
            issues.append("gql driver not installed")
        if not (
            cls.get_env_value("GRAPHQL_ENDPOINT") or cls.get_env_value("DATABASE_HOST")
        ):
            issues.append("GraphQL endpoint not configured")
        return issues

    @classmethod
    async def write_data(
        cls, instance: ProviderInstanceModel, data: str, **kwargs: Any
    ) -> str:
        """Run a GraphQL mutation document."""
        return await cls.execute_query(instance, data, **kwargs)
