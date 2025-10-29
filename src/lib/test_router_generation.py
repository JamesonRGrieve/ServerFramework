import pytest
from app import instance
from lib.Pydantic2FastAPI import generate_routers_from_model_registry
from logic.BLL_Auth import UserManager


def test_generate_routers_includes_user_manager():
    """Ensure router generator returns a router for UserManager when models are bound."""
    # Create an isolated app instance to avoid test pollution
    app = instance(db_prefix="test_router", extensions="")
    mr = app.state.model_registry

    routers = generate_routers_from_model_registry(mr)

    assert isinstance(routers, dict), "routers must be a dict"
    assert "UserManager" in routers, "UserManager router was not generated"

    # Check the returned object is an APIRouter-like object with routes
    router = routers["UserManager"]
    assert hasattr(router, "routes"), "Generated router has no routes"
    assert any(getattr(r, "path", "").startswith("/v1/user") for r in router.routes), (
        "UserManager router does not expose /v1/user paths"
    )
