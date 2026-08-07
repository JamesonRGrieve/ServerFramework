"""
Database extension business logic - integrates database providers into core Provider system
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from zephyrex.extensions.database.EXT_Database import EXT_Database
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic2SQLAlchemy import extension_model
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager, HookContext, hook_bll


class DatabaseManager(AbstractBLLManager):
    """
    Minimal database manager for extension system compatibility.
    This manager provides database utility functions and serves as a hook target.
    """

    def execute_query(self, query: str, **kwargs) -> Dict[str, Any]:
        """Execute a database query"""
        return execute_database_query(self, query, **kwargs)  # type: ignore[return-value]

    def get_schema(self, **kwargs) -> Dict[str, Any]:
        """Get database schema"""
        return get_database_schema(self, **kwargs)  # type: ignore[return-value]


# Seed data functions for Provider Registration
def seed_data() -> List[Dict[str, Any]]:
    """
    Return seed data for database providers and instances.
    Delegates to the static extension method.
    """
    return EXT_Database.get_seed_data()  # type: ignore[no-any-return]


# Database Extension Methods
async def execute_database_query(
    self, query: str, database_type: Optional[str] = None, **kwargs
) -> dict:
    """
    Execute a database query using the Provider Rotation System.
    This method is injected into appropriate managers.

    Args:
        self: The manager instance
        query: SQL or database-specific query to execute
        database_type: Optional database type filter
        **kwargs: Additional parameters for the query

    Returns:
        Dict containing query results
    """
    try:
        # Use the static method from the extension
        result = await EXT_Database.execute_sql(query, **kwargs)

        return {
            "success": True,
            "result": result,
            "query": query,
        }
    except Exception as e:
        logger.error(f"Error executing database query: {e}")
        return {
            "success": False,
            "error": str(e),
            "query": query,
        }


async def get_database_schema(
    self, database_type: Optional[str] = None, **kwargs
) -> dict:
    """
    Get database schema using the Provider Rotation System.
    This method is injected into appropriate managers.

    Args:
        self: The manager instance
        database_type: Optional database type filter
        **kwargs: Additional parameters

    Returns:
        Dict containing schema information
    """
    try:
        # Use the static method from the extension
        result = await EXT_Database.get_schema(**kwargs)

        return {
            "success": True,
            "schema": result,
        }
    except Exception as e:
        logger.error(f"Error getting database schema: {e}")
        return {
            "success": False,
            "error": str(e),
        }


async def chat_with_database(
    self, request: str, database_type: Optional[str] = None, **kwargs
) -> dict:
    """
    Chat with database using natural language via Provider Rotation System.
    This method is injected into appropriate managers.

    Args:
        self: The manager instance
        request: Natural language request
        database_type: Optional database type filter
        **kwargs: Additional parameters

    Returns:
        Dict containing chat response
    """
    try:
        # Use the static method from the extension
        result = await EXT_Database.chat_with_db(request, **kwargs)

        return {
            "success": True,
            "response": result,
            "request": request,
        }
    except Exception as e:
        logger.error(f"Error chatting with database: {e}")
        return {
            "success": False,
            "error": str(e),
            "request": request,
        }


# Method injection for providers/extensions managers
try:
    from zephyrex.logic.BLL_Providers import ProviderManager

    ProviderManager.execute_database_query = execute_database_query
    ProviderManager.get_database_schema = get_database_schema
    ProviderManager.chat_with_database = chat_with_database
    logger.debug("Injected database methods into ProviderManager")
except ImportError:
    logger.debug("ProviderManager not available for method injection")

try:
    from zephyrex.logic.BLL_Extensions import ExtensionManager

    ExtensionManager.execute_database_query = execute_database_query
    ExtensionManager.get_database_schema = get_database_schema
    ExtensionManager.chat_with_database = chat_with_database
    logger.debug("Injected database methods into ExtensionManager")
except ImportError:
    logger.debug("ExtensionManager not available for method injection")


# Hooks for database-related events
@hook_bll(DatabaseManager.execute_query, timing="before")
def log_database_query(context: HookContext):
    """
    Hook to log database queries for audit purposes.
    """
    try:
        query = context.kwargs.get("query", "")
        user_id = getattr(context.manager, "requester_id", None)

        logger.info(f"Database query executed by user {user_id}: {query[:100]}...")

        # Store audit log if meta logging is available
        if hasattr(context.manager, "audit_log"):
            context.manager.audit_log(
                action="database_query",
                resource_type="database",
                resource_id="system",
                additional_data={"query_preview": query[:200]},
            )
    except Exception as e:
        logger.warning(f"Error in database query logging hook: {e}")


@hook_bll(DatabaseManager.get_schema, timing="before")
def log_schema_access(context: HookContext):
    """
    Hook to log database schema access for security auditing.
    """
    try:
        user_id = getattr(context.manager, "requester_id", None)

        logger.info(f"Database schema accessed by user {user_id}")

        # Store audit log if meta logging is available
        if hasattr(context.manager, "audit_log"):
            context.manager.audit_log(
                action="schema_access",
                resource_type="database",
                resource_id="system",
                additional_data={"access_type": "schema"},
            )
    except Exception as e:
        logger.warning(f"Error in schema access logging hook: {e}")


# Database health check and monitoring
class DatabaseHealthCheck:
    """
    Health check utilities for database providers.
    """

    @staticmethod
    def check_all_providers() -> Dict[str, Any]:
        """
        Check health of all configured database providers.

        Returns:
            Dict containing health status of all providers
        """
        health_status = {
            "overall_healthy": True,
            "providers": {},
            "timestamp": None,
        }

        try:
            # Check each configured provider
            for provider_class in EXT_Database.providers():
                provider_name = getattr(provider_class, "name", "Unknown")

                try:
                    # Use provider's validation method if available
                    if hasattr(provider_class, "validate_config"):
                        issues = provider_class.validate_config()
                        healthy = len(issues) == 0

                        health_status["providers"][provider_name] = {  # type: ignore[index]
                            "healthy": healthy,
                            "issues": issues,
                        }

                        if not healthy:
                            health_status["overall_healthy"] = False
                    else:
                        health_status["providers"][provider_name] = {  # type: ignore[index]
                            "healthy": True,
                            "issues": [],
                        }

                except Exception as e:
                    health_status["providers"][provider_name] = {  # type: ignore[index]
                        "healthy": False,
                        "issues": [f"Health check error: {str(e)}"],
                    }
                    health_status["overall_healthy"] = False

            from datetime import datetime, timezone

            health_status["timestamp"] = datetime.now(timezone.utc).isoformat()

        except Exception as e:
            logger.error(f"Error checking database provider health: {e}")
            health_status["overall_healthy"] = False
            health_status["error"] = str(e)

        return health_status


def initialize_database_extension():
    """
    Initialize the database extension and register providers.
    This function should be called during application startup.
    """
    try:
        logger.info("Initializing database extension...")

        # Get health status
        health_status = DatabaseHealthCheck.check_all_providers()

        if health_status["overall_healthy"]:
            logger.info("Database extension initialized successfully")
        else:
            logger.warning("Database extension initialized with issues")
            logger.debug(f"Health status: {health_status}")

        return health_status

    except Exception as e:
        logger.error(f"Error initializing database extension: {e}")
        return {
            "overall_healthy": False,
            "error": str(e),
            "providers": {},
        }
