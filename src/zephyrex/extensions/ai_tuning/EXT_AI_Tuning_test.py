# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI tuning: the dataset and parameter checks; OpenAI's fine-tuning wire
(what is sent, as a local server receives it, and the documented answers
read back); the local job table (owned by the requester, read-only over
REST, mirroring the provider); the routes and abilities, which reach
only accounts the caller can see; refused keys typed as auth failures,
including a real call with a bogus key (xfail when OpenAI is
unreachable); and a read-only live call with a real key."""

import json
import uuid
from typing import Any, Dict

import httpx
import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_tuning.BLL_AI_Tuning import TuningJobManager
from zephyrex.extensions.ai_tuning.EXT_AI_Tuning import EXT_AI_Tuning
from zephyrex.extensions.ai_tuning.PRV_OpenAI_Tuning import PRV_OpenAI_Tuning
from zephyrex.extensions.ai_tuning.TuningProvider import (
    MAX_DATASET_BYTES,
    MAX_PAGE,
    checked_dataset,
    checked_hyperparameters,
    checked_limit,
    checked_model,
    checked_seed,
    checked_suffix,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
)

JSON = {"Content-Type": "application/json"}
EXAMPLE = {
    "messages": [
        {"role": "system", "content": "Marv is a sarcastic chatbot."},
        {"role": "user", "content": "What's the capital of France?"},
        {"role": "assistant", "content": "Paris, as if everyone doesn't know."},
    ]
}
DATASET = "\n".join(json.dumps(EXAMPLE) for _ in range(10)) + "\n"
JOB_ID = "ftjob-abc123"
TUNED = "ft:gpt-4o-mini-2024-07-18:my-org:marv:7p4lURel"
BOGUS = "sk-bogus-0000000000000000000000000000"

# OpenAI's documented answers (API reference: files, fine_tuning.job,
# fine_tuning.job.event, fine_tuning.job.checkpoint).
FILE = {
    "id": "file-abc123",
    "object": "file",
    "bytes": 120000,
    "created_at": 1677610602,
    "filename": "training.jsonl",
    "purpose": "fine-tune",
}
JOB = {
    "object": "fine_tuning.job",
    "id": JOB_ID,
    "model": "gpt-4o-mini-2024-07-18",
    "created_at": 1721764800,
    "fine_tuned_model": None,
    "organization_id": "org-123",
    "result_files": [],
    "status": "validating_files",
    "validation_file": "file-abc123",
    "training_file": "file-abc123",
    "hyperparameters": {"n_epochs": 3, "batch_size": "auto"},
    "method": {
        "type": "supervised",
        "supervised": {
            "hyperparameters": {
                "batch_size": "auto",
                "learning_rate_multiplier": "auto",
                "n_epochs": 3,
            }
        },
    },
    "seed": 42,
    "error": None,
    "trained_tokens": None,
    "finished_at": None,
    "metadata": None,
}
SUCCEEDED = {
    **JOB,
    "status": "succeeded",
    "fine_tuned_model": TUNED,
    "trained_tokens": 5768,
    "finished_at": 1721768400,
}
CANCELLED = {**JOB, "status": "cancelled"}
EVENTS = {
    "object": "list",
    "data": [
        {
            "object": "fine_tuning.job.event",
            "id": "ft-event-ddTJfwuMVpfLXseO0Am0Gqjm",
            "created_at": 1721764800,
            "level": "info",
            "message": "Fine tuning job successfully completed",
            "data": None,
            "type": "message",
        },
        {
            "object": "fine_tuning.job.event",
            "id": "ft-event-tyiGuB72evQncpH87xe505Sv",
            "created_at": 1721764700,
            "level": "info",
            "message": "Step 100/100: training loss=0.12",
            "data": {"step": 100, "train_loss": 0.12},
            "type": "metrics",
        },
    ],
    "has_more": True,
}
CHECKPOINTS = {
    "object": "list",
    "data": [
        {
            "object": "fine_tuning.job.checkpoint",
            "id": "ftckpt_zc4Q7MP6XxulcVzj4MZdwsAB",
            "created_at": 1721764867,
            "fine_tuned_model_checkpoint": f"{TUNED}:ckpt-step-2000",
            "metrics": {
                "full_valid_loss": 0.134,
                "full_valid_mean_token_accuracy": 0.874,
            },
            "fine_tuning_job_id": JOB_ID,
            "step_number": 2000,
        }
    ],
    "first_id": "ftckpt_zc4Q7MP6XxulcVzj4MZdwsAB",
    "last_id": "ftckpt_zc4Q7MP6XxulcVzj4MZdwsAB",
    "has_more": False,
}
JOBS = {"object": "list", "data": [SUCCEEDED], "has_more": False}


def answer(body: Any, status: int = 200):
    return (status, JSON, json.dumps(body).encode())


OPENAI = {
    "/files": answer(FILE),
    "/fine_tuning/jobs": answer(JOB),
    "/fine_tuning/jobs?limit=20": answer(JOBS),
    f"/fine_tuning/jobs/{JOB_ID}": answer(SUCCEEDED),
    f"/fine_tuning/jobs/{JOB_ID}/cancel": answer(CANCELLED),
    f"/fine_tuning/jobs/{JOB_ID}/events?limit=20": answer(EVENTS),
    f"/fine_tuning/jobs/{JOB_ID}/events?limit=5&after=ft-event-x": answer(EVENTS),
    f"/fine_tuning/jobs/{JOB_ID}/checkpoints?limit=10": answer(CHECKPOINTS),
}


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


def dataset(*examples: Any, count: int = 10) -> str:
    rows = list(examples) + [EXAMPLE] * (count - len(examples))
    return "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows)


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


class TestChecks:
    def test_a_chat_dataset(self):
        assert checked_dataset(DATASET) == DATASET.encode()
        assert checked_dataset(DATASET.rstrip("\n")) == DATASET.rstrip("\n").encode()

    async def test_the_validate_ability_counts_examples(self):
        found = await EXT_AI_Tuning.validate_tuning_dataset(DATASET)
        assert found == {"examples": 10, "bytes": len(DATASET.encode())}

    def test_tool_calls_tools_and_weights_are_chat_format(self):
        example = {
            "messages": [
                {"role": "user", "content": "Weather in Paris?"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_id",
                            "type": "function",
                            "function": {"name": "weather", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_id", "content": "12C"},
                {"role": "assistant", "content": "12C.", "weight": 1},
            ],
            "tools": [{"type": "function", "function": {"name": "weather"}}],
            "parallel_tool_calls": False,
        }
        checked_dataset(dataset(example))

    @pytest.mark.parametrize(
        "example, reason",
        [
            ("{not json", "line 1 is not JSON"),
            ("[1, 2]", "line 1 is not a JSON object"),
            ({"prompt": "a", "completion": "b"}, "unexpected keys"),
            ({"messages": []}, "messages is a non-empty list"),
            ({"messages": [{"role": "user", "content": "hi"}]}, "assistant message"),
            ({"messages": [{"role": "robot", "content": "hi"}]}, "role is one of"),
            ({"messages": [{"role": "user"}]}, "a user message has content"),
            (
                {"messages": [{"role": "assistant", "content": 5}]},
                "content is text",
            ),
            (
                {"messages": [{"role": "assistant", "content": "a", "weight": 2}]},
                "weight is 0 or 1",
            ),
            (
                {
                    "messages": [
                        {"role": "user", "content": "a", "weight": 1},
                        {"role": "assistant", "content": "b"},
                    ]
                },
                "weight is 0 or 1",
            ),
            (
                {"messages": [{"role": "assistant", "content": "a"}], "tools": {}},
                "tools is a list",
            ),
        ],
    )
    def test_a_bad_example_names_its_line(self, example, reason):
        with pytest.raises(InvalidInputExternalError, match=reason):
            checked_dataset(dataset(example))

    def test_an_empty_line_inside(self):
        with pytest.raises(InvalidInputExternalError, match="line 2 is empty"):
            checked_dataset(dataset(json.dumps(EXAMPLE), "  "))

    def test_too_few_examples(self):
        with pytest.raises(InvalidInputExternalError, match="not 9"):
            checked_dataset(dataset(count=9))

    def test_too_large(self):
        with pytest.raises(InvalidInputExternalError, match="MiB"):
            checked_dataset("x" * (MAX_DATASET_BYTES + 1))

    @pytest.mark.parametrize("data", ["", "   ", None, 7])
    def test_not_text(self, data):
        with pytest.raises(InvalidInputExternalError, match="JSONL text"):
            checked_dataset(data)

    def test_hyperparameters(self):
        assert checked_hyperparameters(None, None, None) == {}
        assert checked_hyperparameters(3, 8, 2) == {
            "n_epochs": 3,
            "batch_size": 8,
            "learning_rate_multiplier": 2.0,
        }
        for bad in ((0, None, None), (True, None, None), (None, 1.5, None)):
            with pytest.raises(InvalidInputExternalError):
                checked_hyperparameters(*bad)
        with pytest.raises(InvalidInputExternalError):
            checked_hyperparameters(None, None, -0.1)

    def test_names_seed_and_limits(self):
        assert checked_model("gpt-4o-mini-2024-07-18") == "gpt-4o-mini-2024-07-18"
        assert checked_suffix(None) is None and checked_suffix("marv") == "marv"
        assert checked_seed(None) is None and checked_seed(42) == 42
        assert checked_limit(MAX_PAGE) == MAX_PAGE
        for check, bad in (
            (checked_model, ""),
            (checked_model, "m" * 257),
            (checked_suffix, "s" * 65),
            (checked_suffix, ""),
            (checked_seed, "1"),
            (checked_limit, 0),
            (checked_limit, MAX_PAGE + 1),
        ):
            with pytest.raises(InvalidInputExternalError):
                check(bad)


class TestTuning(ExtensionServerMixin):
    extension_class = EXT_AI_Tuning

    @pytest.fixture(scope="module")
    def provider_ids(self, server, admin_a) -> Dict[str, str]:
        providers = server.get("/v1/provider", headers=auth(admin_a)).json()[
            "providers"
        ]
        return {p["name"]: p["id"] for p in providers}

    @pytest.fixture
    def account(self, server, provider_ids):
        """A provider instance ``owner`` adds, at ``base_url`` when given."""
        registry = server.app.state.model_registry

        def _add(owner, base_url=None, api_key="sk-test", provider=None):
            response = server.post(
                "/v1/provider/instance",
                json={
                    "provider_instance": {
                        "name": f"tuning-{uuid.uuid4().hex}",
                        "provider_id": provider_ids[provider or PRV_OpenAI_Tuning.name],
                        "api_key": api_key,
                    }
                },
                headers=auth(owner),
            )
            assert response.status_code == 201, response.text
            created: Dict[str, Any] = response.json()["provider_instance"]
            if base_url:
                ProviderInstanceSettingManager(
                    model_registry=registry, requester_id=env("ROOT_ID")
                ).create(
                    provider_instance_id=created["id"], key="base_url", value=base_url
                )
            return created

        return _add

    @pytest.fixture
    def openai(self, local_http_server, account, admin_a):
        server = local_http_server(OPENAI)
        return server, account(admin_a, server.base_url)

    def instance(self, server, instance_id) -> ProviderInstanceModel:
        return ProviderInstanceModel.model_validate(
            ProviderInstanceManager(
                model_registry=server.app.state.model_registry,
                requester_id=env("ROOT_ID"),
            ).get(id=instance_id),
            from_attributes=True,
        )

    def submit(self, server, user, account_id, **fields):
        return server.post(
            "/v1/tuning_job/submit",
            json={
                "provider_instance_id": account_id,
                "base_model": "gpt-4o-mini-2024-07-18",
                "training_data": DATASET,
                **fields,
            },
            headers=auth(user),
        )

    # The wire, as the local server receives it.

    async def test_the_upload_is_a_fine_tune_file(self, server, openai):
        upstream, account = openai
        uploaded = await PRV_OpenAI_Tuning.upload_dataset(
            self.instance(server, account["id"]), DATASET.encode(), "training.jsonl"
        )
        assert uploaded == {
            "file_id": "file-abc123",
            "bytes": 120000,
            "filename": "training.jsonl",
        }
        sent = upstream.requests[0]
        assert sent.method == "POST" and sent.path == "/files"
        assert sent.headers["content-type"].startswith("multipart/form-data")
        assert sent.headers["authorization"] == "Bearer sk-test"
        assert b'name="purpose"' in sent.body and b"fine-tune" in sent.body
        assert b'filename="training.jsonl"' in sent.body
        assert DATASET.encode() in sent.body

    async def test_a_job_sends_only_what_was_asked(self, server, openai):
        upstream, account = openai
        instance = self.instance(server, account["id"])
        job = await PRV_OpenAI_Tuning.create_job(
            instance, "gpt-4o-mini-2024-07-18", "file-abc123", None, None, {}, None
        )
        assert json.loads(upstream.requests[0].body) == {
            "model": "gpt-4o-mini-2024-07-18",
            "training_file": "file-abc123",
        }
        assert job["provider_job_id"] == JOB_ID
        assert job["status"] == "validating_files"
        assert job["hyperparameters"] == {
            "batch_size": "auto",
            "learning_rate_multiplier": "auto",
            "n_epochs": 3,
        }

    async def test_hyperparameters_go_under_the_supervised_method(self, server, openai):
        upstream, account = openai
        await PRV_OpenAI_Tuning.create_job(
            self.instance(server, account["id"]),
            "gpt-4o-mini-2024-07-18",
            "file-abc123",
            "file-def456",
            "marv",
            {"n_epochs": 3},
            42,
        )
        assert json.loads(upstream.requests[0].body) == {
            "model": "gpt-4o-mini-2024-07-18",
            "training_file": "file-abc123",
            "validation_file": "file-def456",
            "suffix": "marv",
            "seed": 42,
            "method": {
                "type": "supervised",
                "supervised": {"hyperparameters": {"n_epochs": 3}},
            },
        }

    async def test_a_finished_job_names_the_tuned_model(self, server, openai):
        upstream, account = openai
        job = await PRV_OpenAI_Tuning.get_job(
            self.instance(server, account["id"]), JOB_ID
        )
        assert job["fine_tuned_model"] == TUNED and job["trained_tokens"] == 5768
        assert job["finished_at"].isoformat() == "2024-07-23T21:00:00+00:00"
        assert upstream.requests[0].method == "GET"

    async def test_cancel_posts_to_the_job(self, server, openai):
        upstream, account = openai
        job = await PRV_OpenAI_Tuning.cancel_job(
            self.instance(server, account["id"]), JOB_ID
        )
        assert job["status"] == "cancelled"
        assert upstream.requests[0].method == "POST"
        assert upstream.requests[0].path == f"/fine_tuning/jobs/{JOB_ID}/cancel"

    async def test_events_page_after_a_cursor(self, server, openai):
        upstream, account = openai
        page = await PRV_OpenAI_Tuning.job_events(
            self.instance(server, account["id"]), JOB_ID, "ft-event-x", 5
        )
        assert page["has_more"] is True
        assert page["data"][1] == {
            "id": "ft-event-tyiGuB72evQncpH87xe505Sv",
            "created_at": "2024-07-23T19:58:20+00:00",
            "level": "info",
            "message": "Step 100/100: training loss=0.12",
            "type": "metrics",
            "data": {"step": 100, "train_loss": 0.12},
        }

    async def test_a_job_id_is_one_path_segment(self, server, openai):
        upstream, account = openai
        with pytest.raises(InvalidInputExternalError, match="not a valid id"):
            await PRV_OpenAI_Tuning.get_job(
                self.instance(server, account["id"]), "../files"
            )
        assert not upstream.requests

    async def test_an_account_without_a_key_calls_nothing(
        self, server, openai, account, admin_a
    ):
        upstream, _ = openai
        keyless = account(admin_a, upstream.base_url, api_key=None)
        with pytest.raises(InvalidInputExternalError, match="no API key"):
            await PRV_OpenAI_Tuning.list_jobs(
                self.instance(server, keyless["id"]), None, 20
            )
        assert not upstream.requests

    @pytest.mark.parametrize(
        "status, body, error",
        [
            (401, {"error": {"code": "invalid_api_key"}}, AuthExternalError),
            (500, {"error": {"message": "boom"}}, TransientExternalError),
        ],
    )
    async def test_failures_are_typed(
        self, server, local_http_server, account, admin_a, status, body, error
    ):
        upstream = local_http_server(
            {"/fine_tuning/jobs?limit=20": answer(body, status)}
        )
        refused = account(admin_a, upstream.base_url)
        with pytest.raises(error):
            await PRV_OpenAI_Tuning.list_jobs(
                self.instance(server, refused["id"]), None, 20
            )

    async def test_a_refused_request_carries_openais_reason(
        self, server, local_http_server, account, admin_a
    ):
        reason = "Model gpt-9 is not available for fine-tuning or does not exist."
        upstream = local_http_server(
            {"/fine_tuning/jobs": answer({"error": {"message": reason}}, 400)}
        )
        refused = account(admin_a, upstream.base_url)
        with pytest.raises(InvalidInputExternalError, match="not available"):
            await PRV_OpenAI_Tuning.create_job(
                self.instance(server, refused["id"]),
                "gpt-9",
                "file-1",
                None,
                None,
                {},
                None,
            )

    async def test_a_lan_address_needs_an_egress_allowance(
        self, server, account, admin_a
    ):
        lan = account(admin_a, "http://192.168.1.20:8000/v1")
        with pytest.raises(InvalidInputExternalError, match="SSRF"):
            await PRV_OpenAI_Tuning.list_jobs(
                self.instance(server, lan["id"]), None, 20
            )

    # The routes and the table.

    def test_submit_uploads_starts_and_records_the_job(self, server, openai, admin_a):
        upstream, account = openai
        response = self.submit(
            server,
            admin_a,
            account["id"],
            validation_data=DATASET,
            suffix="marv",
            n_epochs=3,
        )
        assert response.status_code == 200, response.text
        job = response.json()
        assert job["user_id"] == admin_a.id
        assert job["provider"] == "openai_fine_tuning"
        assert job["provider_instance_id"] == account["id"]
        assert job["provider_job_id"] == JOB_ID
        assert job["status"] == "validating_files"
        assert job["suffix"] == "marv" and job["fine_tuned_model"] is None
        assert [r.path for r in upstream.requests] == [
            "/files",
            "/files",
            "/fine_tuning/jobs",
        ]
        assert b'filename="validation.jsonl"' in upstream.requests[1].body
        sent = json.loads(upstream.requests[2].body)
        assert sent["validation_file"] == "file-abc123"
        assert sent["method"]["supervised"]["hyperparameters"] == {"n_epochs": 3}

    def test_a_bad_dataset_is_refused_before_any_upload(self, server, openai, admin_a):
        upstream, account = openai
        response = self.submit(
            server, admin_a, account["id"], training_data=dataset(count=3)
        )
        assert response.status_code == 400, response.text
        assert "examples" in response.text
        assert not upstream.requests

    def test_another_user_cannot_train_on_the_account(self, server, openai, admin_b):
        upstream, account = openai
        response = self.submit(server, admin_b, account["id"])
        assert response.status_code == 404, response.text
        assert not upstream.requests

    def test_an_account_of_another_kind_is_refused(
        self, server, local_http_server, account, admin_a
    ):
        upstream = local_http_server(OPENAI)
        chat = account(admin_a, upstream.base_url, provider="openai")
        response = self.submit(server, admin_a, chat["id"])
        assert response.status_code == 400, response.text
        assert "not a fine-tuning account" in response.text
        assert not upstream.requests

    def test_a_refused_key_is_a_bad_gateway(
        self, server, local_http_server, account, admin_a
    ):
        upstream = local_http_server(
            {"/files": answer({"error": {"code": "invalid_api_key"}}, 401)}
        )
        refused = account(admin_a, upstream.base_url, api_key="sk-wrong")
        response = self.submit(server, admin_a, refused["id"])
        assert response.status_code == 502, response.text
        assert "refused" in response.text

    def test_refresh_and_cancel_mirror_the_provider(self, server, openai, admin_a):
        _, account = openai
        job = self.submit(server, admin_a, account["id"]).json()

        refreshed = server.post(
            f"/v1/tuning_job/{job['id']}/refresh", json={}, headers=auth(admin_a)
        )
        assert refreshed.status_code == 200, refreshed.text
        found = refreshed.json()
        assert found["status"] == "succeeded" and found["fine_tuned_model"] == TUNED
        assert found["trained_tokens"] == 5768 and found["finished_at"]

        stored = server.get(f"/v1/tuning_job/{job['id']}", headers=auth(admin_a))
        assert stored.json()["tuning_job"]["fine_tuned_model"] == TUNED

        cancelled = server.post(
            f"/v1/tuning_job/{job['id']}/cancel", json={}, headers=auth(admin_a)
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"

    def test_events_checkpoints_and_the_accounts_jobs(self, server, openai, admin_a):
        _, account = openai
        job = self.submit(server, admin_a, account["id"]).json()

        events = server.get(f"/v1/tuning_job/{job['id']}/events", headers=auth(admin_a))
        assert events.status_code == 200, events.text
        assert events.json()["data"][0]["message"].startswith("Fine tuning job")

        checkpoints = server.get(
            f"/v1/tuning_job/{job['id']}/checkpoints", headers=auth(admin_a)
        )
        assert checkpoints.status_code == 200, checkpoints.text
        checkpoint = checkpoints.json()["data"][0]
        assert checkpoint["model"] == f"{TUNED}:ckpt-step-2000"
        assert checkpoint["step"] == 2000
        assert checkpoint["metrics"]["full_valid_loss"] == 0.134

        jobs = server.get(
            f"/v1/tuning_job/account/{account['id']}/jobs", headers=auth(admin_a)
        )
        assert jobs.status_code == 200, jobs.text
        assert jobs.json()["data"][0]["fine_tuned_model"] == TUNED

    def test_a_job_is_its_owners_alone(self, server, openai, admin_a, admin_b):
        upstream, account = openai
        job = self.submit(server, admin_a, account["id"]).json()
        made = len(upstream.requests)

        assert (
            server.get(f"/v1/tuning_job/{job['id']}", headers=auth(admin_b)).status_code
            == 404
        )
        theirs = server.get("/v1/tuning_job", headers=auth(admin_b)).json()
        assert job["id"] not in [row["id"] for row in theirs["tuning_jobs"]]
        for action in ("refresh", "cancel"):
            response = server.post(
                f"/v1/tuning_job/{job['id']}/{action}", json={}, headers=auth(admin_b)
            )
            assert response.status_code == 404, response.text
        events = server.get(f"/v1/tuning_job/{job['id']}/events", headers=auth(admin_b))
        assert events.status_code == 404
        assert len(upstream.requests) == made

    def test_the_table_is_read_only_over_rest(self, server):
        paths = server.app.openapi()["paths"]
        writes = {
            (method, path)
            for path, operations in paths.items()
            if path.startswith("/v1/tuning_job")
            for method in operations
            if method != "get"
        }
        assert writes == {
            ("post", "/v1/tuning_job/search"),
            ("post", "/v1/tuning_job/submit"),
            ("post", "/v1/tuning_job/{job_id}/refresh"),
            ("post", "/v1/tuning_job/{job_id}/cancel"),
        }

    def _fields(self, account_id: str) -> Dict[str, Any]:
        from datetime import UTC, datetime

        return {
            "provider": "openai_fine_tuning",
            "provider_instance_id": account_id,
            "provider_job_id": f"ftjob-{uuid.uuid4().hex}",
            "base_model": "gpt-4o-mini-2024-07-18",
            "status": "queued",
            "refreshed_at": datetime.now(UTC),
        }

    def test_the_owner_is_the_requester(self, server, openai, admin_a, admin_b):
        _, account = openai
        registry = server.app.state.model_registry
        jobs = TuningJobManager(model_registry=registry, requester_id=admin_b.id)
        named = jobs.create(**self._fields(account["id"]), user_id=admin_a.id)
        assert named.user_id == admin_b.id
        batch = jobs.create(
            entities=[
                {**self._fields(account["id"]), "user_id": admin_a.id},
                self._fields(account["id"]),
            ]
        )
        assert [job.user_id for job in batch] == [admin_b.id, admin_b.id]

        as_root = TuningJobManager(model_registry=registry, requester_id=env("ROOT_ID"))
        assert (
            as_root.create(**self._fields(account["id"]), user_id=admin_a.id).user_id
            == admin_a.id
        )

    def test_an_update_never_moves_the_owner(self, server, openai, admin_a, admin_b):
        _, account = openai
        registry = server.app.state.model_registry
        jobs = TuningJobManager(model_registry=registry, requester_id=admin_a.id)
        job = jobs.create(**self._fields(account["id"]))
        moved = jobs.update(job.id, user_id=admin_b.id, status="running")
        assert moved.user_id == admin_a.id and moved.status == "running"

    # The abilities.

    async def test_abilities_act_for_the_requester(
        self, server, openai, admin_a, admin_b
    ):
        _, account = openai
        made = await EXT_AI_Tuning.create_tuning_job(
            admin_a.id, account["id"], "gpt-4o-mini-2024-07-18", DATASET
        )
        assert made["user_id"] == admin_a.id
        listed = await EXT_AI_Tuning.list_tuning_jobs(admin_a.id)
        assert made["id"] in [job["id"] for job in listed]
        refreshed = await EXT_AI_Tuning.refresh_tuning_job(admin_a.id, made["id"])
        assert refreshed["fine_tuned_model"] == TUNED
        checkpoints = await EXT_AI_Tuning.tuning_job_checkpoints(admin_a.id, made["id"])
        assert checkpoints["data"][0]["step"] == 2000
        events = await EXT_AI_Tuning.tuning_job_events(admin_a.id, made["id"])
        assert len(events["data"]) == 2
        jobs = await EXT_AI_Tuning.list_provider_tuning_jobs(admin_a.id, account["id"])
        assert jobs["data"][0]["provider_job_id"] == JOB_ID

        with pytest.raises(HTTPException) as raised:
            await EXT_AI_Tuning.get_tuning_job(admin_b.id, made["id"])
        assert raised.value.status_code == 404
        with pytest.raises(HTTPException) as raised:
            await EXT_AI_Tuning.create_tuning_job(
                admin_b.id, account["id"], "gpt-4o-mini-2024-07-18", DATASET
            )
        assert raised.value.status_code == 404

    async def test_abilities_need_a_requester(self, server):
        with pytest.raises(HTTPException) as raised:
            await EXT_AI_Tuning.list_tuning_jobs("")
        assert raised.value.status_code == 400

    # Real OpenAI.

    async def test_openai_refuses_a_bogus_key(self, server, account, admin_a):
        if not _online("https://api.openai.com"):
            pytest.xfail("https://api.openai.com is unreachable")
        bogus = account(admin_a, api_key=BOGUS)
        with pytest.raises(AuthExternalError):
            await PRV_OpenAI_Tuning.list_jobs(
                self.instance(server, bogus["id"]), None, 1
            )

    @pytest.mark.external_api(provider="openai_fine_tuning")
    async def test_live_jobs_and_events(
        self, server, account, admin_a, sandbox_credentials_for
    ):
        """Read-only: the account's jobs, and the newest job's events."""
        key = sandbox_credentials_for("openai_fine_tuning")["OPENAI_API_KEY"]
        live = self.instance(server, account(admin_a, api_key=key)["id"])
        page = await PRV_OpenAI_Tuning.list_jobs(live, None, 1)
        assert isinstance(page["has_more"], bool)
        for job in page["data"]:
            events = await PRV_OpenAI_Tuning.job_events(
                live, job["provider_job_id"], None, 1
            )
            assert isinstance(events["data"], list)
