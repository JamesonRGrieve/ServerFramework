# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression test for #241: the MFA action endpoints must actually mount.

They were previously declared via @static_route on the extension class, a
mechanism that never mounted (the collected routers were dead code). They are
now custom_routes on MultifactorMethodManager. This test proves the routes are
registered and reachable over HTTP (auth-guarded, not a route-missing 404).
"""

import os

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-32-bytes-or-more-aaaaaa")
os.environ.setdefault("DATABASE_TYPE", "sqlite")


def _boot():
    from zephyrex.lib.Environment import refresh_settings
    from zephyrex.testing.fixtures import prepare_test_registry

    prepare_test_registry()
    refresh_settings()
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    from zephyrex.app import instance

    companions = (
        "metadata",
        "auth_lockout",
        "auth_recovery_questions",
        "auth_invitations",
        "auth_session",
        "acl_rbac",
    )
    return instance(
        db_prefix=f"test.mfaroutes.{worker}",
        extensions=",".join(["auth_mfa", *companions]),
    )


_MFA_ACTION_PATHS = {
    "/v1/user/mfa/{mfa_method_id}/verify",
    "/v1/user/mfa/{mfa_method_id}/recovery/generate",
    "/v1/user/mfa/{mfa_method_id}/recovery/verify",
}


class TestMFAActionRoutesMount:
    def test_action_routes_are_registered(self):
        app = _boot()
        # Assert against the generated manager router (deterministic) — the model
        # is linked to its manager so the registry builds the router, and the
        # custom_routes appear in it.
        router = app.state.model_registry.ep_routers.get("MultifactorMethodManager")
        assert router is not None, "MFA manager router was not generated"
        paths = {getattr(rt, "path", "") for rt in router.routes}
        missing = _MFA_ACTION_PATHS - paths
        assert not missing, f"MFA action routes not in generated router: {missing}"

    def test_verify_endpoint_is_reachable_and_guarded(self):
        from fastapi.testclient import TestClient

        client = TestClient(_boot())
        # No auth -> rejected because the route EXISTS and is JWT-guarded, not a
        # generic route-missing 404.
        r = client.post("/v1/user/mfa/some-method-id/verify", json={"code": "000000"})
        assert r.status_code in (401, 403), r.text
