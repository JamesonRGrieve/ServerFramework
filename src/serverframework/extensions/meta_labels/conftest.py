import os

import pytest
from starlette.testclient import TestClient

from conftest import (
    CORE_COMPANION_EXTENSIONS,
    add_user_to_team,
    create_role,
    create_team,
    create_user,
    generate_test_email,
    prepare_test_registry,
    setup_python_path,
)
from serverframework.lib.Environment import env

_META_LABELS_EXTENSIONS = ",".join((*CORE_COMPANION_EXTENSIONS, "meta_labels"))


@pytest.fixture(scope="session")
def server():
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    db_prefix = f"test.meta_labels.{worker_id}"
    prepare_test_registry()
    setup_python_path()
    from serverframework.app import instance

    app = instance(db_prefix=db_prefix, extensions=_META_LABELS_EXTENSIONS)
    mr = app.state.model_registry
    engine = mr.DB.manager._setup_engine
    if engine is not None:
        mr.DB.manager.Base.metadata.create_all(bind=engine)
    yield TestClient(app)


@pytest.fixture(scope="session")
def admin_a(server):
    return create_user(server, email=generate_test_email("admin_a_ml"), last_name="AdminA")


@pytest.fixture(scope="session")
def team_a(server, admin_a):
    return create_team(server, admin_a.id, name="Team A Labels")


