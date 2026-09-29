# SPDX-License-Identifier: AGPL-3.0-or-later
"""A create request can only set the fields its Create model declares.

The manager used to copy every request field its Create model did not
declare straight into the new row, so any column missing from a Create
model was client-settable.
"""

import uuid


def test_create_ignores_columns_outside_the_create_model(server, admin_a):
    headers = {"Authorization": f"Bearer {admin_a.jwt}"}

    created = server.post(
        "/v1/team",
        json={
            "team": {
                "name": f"mass_assignment_{uuid.uuid4().hex[:8]}",
                "token": "injected-token",
                "training_data": "injected-training-data",
            }
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    team_id = created.json()["team"]["id"]

    team = server.get(f"/v1/team/{team_id}", headers=headers).json()["team"]
    assert "token" not in team
    assert "training_data" not in team
