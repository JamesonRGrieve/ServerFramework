# SPDX-License-Identifier: AGPL-3.0-or-later
"""The chain routes: generic CRUD for chains and steps, read-only runs and
step results, ``POST /v1/chain/{id}/run`` and ``POST /v1/chain-run/{id}/
cancel``. The old router imported a module that does not exist
(``extensions.chains.BLL_Chains``) and its execute route called methods no
manager had."""

from typing import Any, Dict

from zephyrex.extensions.ai_chains.chain_fixtures_test import ChainFixtures


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


class TestRoutes(ChainFixtures):
    def _create(self, server, user, path, key, body) -> Dict[str, Any]:
        response = server.post(path, json={key: body}, headers=auth(user))
        assert response.status_code == 201, response.text
        created: Dict[str, Any] = response.json()[key]
        return created

    def _chain_with_steps(self, server, user) -> Dict[str, Any]:
        chain = self._create(
            server, user, "/v1/chain", "chain", {"name": "Greeter", "max_steps": 10}
        )
        self._create(
            server,
            user,
            "/v1/chain-step",
            "chain_step",
            {
                "chain_id": chain["id"],
                "name": "greet",
                "kind": "set",
                "position": 1,
                "expression": "'hello ' + who",
                "variable": "greeting",
            },
        )
        return chain

    def test_run_a_chain_and_read_what_it_did(self, server, admin_a):
        chain = self._chain_with_steps(server, admin_a)
        ran = server.post(
            f"/v1/chain/{chain['id']}/run",
            json={"inputs": {"who": "Ada"}},
            headers=auth(admin_a),
        )
        assert ran.status_code == 200, ran.text
        run = ran.json()
        assert run["status"] == "succeeded"
        assert run["variables"]["greeting"] == "hello Ada"
        stored = server.get(f"/v1/chain-run/{run['id']}", headers=auth(admin_a))
        assert stored.status_code == 200, stored.text
        results = server.get(
            "/v1/chain-step-result",
            params={"chain_run_id": run["id"]},
            headers=auth(admin_a),
        )
        assert results.status_code == 200, results.text
        [result] = results.json()["chain_step_results"]
        assert result["step_name"] == "greet" and result["status"] == "succeeded"

    def test_no_one_else_runs_or_reads_a_chain(self, server, admin_a, admin_b):
        chain = self._chain_with_steps(server, admin_a)
        ran = server.post(
            f"/v1/chain/{chain['id']}/run", json={"inputs": {}}, headers=auth(admin_b)
        )
        assert ran.status_code in (403, 404), ran.text
        mine = server.post(
            f"/v1/chain/{chain['id']}/run",
            json={"inputs": {"who": "x"}},
            headers=auth(admin_a),
        ).json()
        assert (
            server.get(f"/v1/chain-run/{mine['id']}", headers=auth(admin_b)).status_code
            == 404
        )
        cancel = server.post(
            f"/v1/chain-run/{mine['id']}/cancel", json={}, headers=auth(admin_b)
        )
        assert cancel.status_code in (403, 404), cancel.text

    def test_runs_are_not_written_through_generic_routes(self, server, admin_a):
        chain = self._chain_with_steps(server, admin_a)
        forged = server.post(
            "/v1/chain-run",
            json={"chain_run": {"chain_id": chain["id"], "status": "succeeded"}},
            headers=auth(admin_a),
        )
        assert forged.status_code in (404, 405), forged.text
        planted = server.post(
            "/v1/chain-step-result",
            json={"chain_step_result": {"step_name": "x"}},
            headers=auth(admin_a),
        )
        assert planted.status_code in (404, 405), planted.text

    def test_a_failing_run_is_answered_not_raised(self, server, admin_a):
        chain = self._chain_with_steps(server, admin_a)
        ran = server.post(
            f"/v1/chain/{chain['id']}/run", json={"inputs": {}}, headers=auth(admin_a)
        )
        assert ran.status_code == 200, ran.text
        assert ran.json()["status"] == "failed"
        assert ran.json()["error_kind"] == "step"

    def test_bad_inputs_are_refused(self, server, admin_a):
        chain = self._chain_with_steps(server, admin_a)
        ran = server.post(
            f"/v1/chain/{chain['id']}/run",
            json={"inputs": {"not an identifier": 1}},
            headers=auth(admin_a),
        )
        assert ran.status_code == 422, ran.text
