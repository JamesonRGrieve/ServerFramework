# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenAI fine-tuning: datasets uploaded through the Files API (purpose
``fine-tune``), and supervised jobs, their events and checkpoints through
``/fine_tuning/jobs``.

An instance's key is its own: there is no environment fallback, so an
account a user adds never trains, or lists jobs, on the server's key."""

import json
from datetime import UTC, datetime
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai_tuning.TuningProvider import AbstractTuningProvider
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

JOBS = "/fine_tuning/jobs"


def _moment(seconds: Any) -> Optional[datetime]:
    """A Unix timestamp as a UTC moment."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    return datetime.fromtimestamp(seconds, UTC)


def _stamp(seconds: Any) -> Optional[str]:
    moment = _moment(seconds)
    return moment.isoformat() if moment else None


def _error_text(error: Any) -> Optional[str]:
    if not isinstance(error, dict) or not error.get("message"):
        return None
    code = error.get("code")
    return f"{code}: {error['message']}" if code else str(error["message"])


def openai_job(job: Any) -> Dict[str, Any]:
    """A ``fine_tuning.job`` object in the shared job shape."""
    if not isinstance(job, dict) or not isinstance(job.get("id"), str):
        raise TransientExternalError("OpenAI answered without a fine-tuning job")
    method = job.get("method") or {}
    supervised = method.get("supervised") if isinstance(method, dict) else None
    hyperparameters = (supervised or {}).get("hyperparameters") or job.get(
        "hyperparameters"
    )
    return {
        "provider_job_id": job["id"],
        "base_model": str(job.get("model") or ""),
        "status": str(job.get("status") or "unknown"),
        "fine_tuned_model": job.get("fine_tuned_model"),
        "training_file_id": job.get("training_file"),
        "validation_file_id": job.get("validation_file"),
        "error": _error_text(job.get("error")),
        "trained_tokens": job.get("trained_tokens"),
        "hyperparameters": hyperparameters or None,
        "finished_at": _moment(job.get("finished_at")),
    }


def openai_event(event: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": event.get("id"),
        "created_at": _stamp(event.get("created_at")),
        "level": event.get("level"),
        "message": event.get("message"),
        "type": event.get("type"),
        "data": event.get("data"),
    }


def openai_checkpoint(checkpoint: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": checkpoint.get("id"),
        "created_at": _stamp(checkpoint.get("created_at")),
        "model": checkpoint.get("fine_tuned_model_checkpoint"),
        "step": checkpoint.get("step_number"),
        "metrics": checkpoint.get("metrics") or {},
    }


def _rows(page: Any) -> List[Mapping[str, Any]]:
    data = page.get("data") if isinstance(page, dict) else None
    if not isinstance(data, list):
        raise TransientExternalError("OpenAI answered without a list")
    return [row for row in data if isinstance(row, dict)]


def _upstream_message(error: InvalidInputExternalError) -> Optional[str]:
    """The message in an OpenAI error body: ``{"error": {"message"}}``."""
    try:
        body = json.loads(str(error.upstream_payload or ""))
    except ValueError:
        return None
    detail = body.get("error") if isinstance(body, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        return str(detail["message"])
    return None


class PRV_OpenAI_Tuning(AbstractTuningProvider):
    name: ClassVar[str] = "openai_fine_tuning"
    friendly_name: ClassVar[str] = "OpenAI fine-tuning"
    description: ClassVar[str] = "Fine-tunes OpenAI models on an OpenAI account"
    finished_statuses: ClassVar[Set[str]] = {"succeeded", "failed", "cancelled"}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("api_key", "API key", secret=True, field="api_key"),
        InstanceSetting("base_url", "API address", default="https://api.openai.com/v1"),
    )

    @classmethod
    def base(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "base_url") or "").rstrip("/")

    @classmethod
    def headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        key = cls.setting(instance, "api_key")
        if not key:
            raise InvalidInputExternalError(
                f"This {cls.friendly_name} account has no API key", provider=cls.name
            )
        return {"Authorization": f"Bearer {key}"}

    @classmethod
    async def call(
        cls, instance: ProviderInstanceModel, method: str, path: str, **kwargs: Any
    ) -> Any:
        """``method`` ``path`` on the account; a request OpenAI refuses as
        the caller's carries OpenAI's own reason."""
        try:
            return await cls.http().request(
                method,
                f"{cls.base(instance)}{path}",
                headers=cls.headers(instance),
                **kwargs,
            )
        except InvalidInputExternalError as exc:
            reason = _upstream_message(exc)
            if reason is None:
                raise
            raise InvalidInputExternalError(
                f"{cls.friendly_name}: {reason}",
                provider=cls.name,
                upstream_status=exc.upstream_status,
                upstream_payload=exc.upstream_payload,
            ) from exc

    @classmethod
    def page_params(cls, after: Optional[str], limit: int) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after
        return params

    @classmethod
    async def upload_dataset(
        cls, instance: ProviderInstanceModel, content: bytes, filename: str
    ) -> Dict[str, Any]:
        answer = await cls.call(
            instance,
            "POST",
            "/files",
            data={"purpose": "fine-tune"},
            files={"file": (filename, content, "application/jsonl")},
        )
        if not isinstance(answer, dict) or not isinstance(answer.get("id"), str):
            raise TransientExternalError(
                "OpenAI answered the upload without a file", provider=cls.name
            )
        return {
            "file_id": answer["id"],
            "bytes": answer.get("bytes"),
            "filename": answer.get("filename"),
        }

    @classmethod
    async def create_job(
        cls,
        instance: ProviderInstanceModel,
        base_model: str,
        training_file_id: str,
        validation_file_id: Optional[str],
        suffix: Optional[str],
        hyperparameters: Dict[str, Any],
        seed: Optional[int],
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"model": base_model, "training_file": training_file_id}
        if validation_file_id:
            body["validation_file"] = validation_file_id
        if suffix:
            body["suffix"] = suffix
        if seed is not None:
            body["seed"] = seed
        if hyperparameters:
            body["method"] = {
                "type": "supervised",
                "supervised": {"hyperparameters": hyperparameters},
            }
        return openai_job(await cls.call(instance, "POST", JOBS, json=body))

    @classmethod
    async def list_jobs(
        cls, instance: ProviderInstanceModel, after: Optional[str], limit: int
    ) -> Dict[str, Any]:
        page = await cls.call(
            instance, "GET", JOBS, params=cls.page_params(after, limit)
        )
        return {
            "data": [openai_job(job) for job in _rows(page)],
            "has_more": bool(page.get("has_more")),
        }

    @classmethod
    def job_path(cls, job_id: str, *rest: str) -> str:
        return "/".join((JOBS, path_segment(job_id, "job id"), *rest))

    @classmethod
    async def get_job(
        cls, instance: ProviderInstanceModel, job_id: str
    ) -> Dict[str, Any]:
        return openai_job(await cls.call(instance, "GET", cls.job_path(job_id)))

    @classmethod
    async def cancel_job(
        cls, instance: ProviderInstanceModel, job_id: str
    ) -> Dict[str, Any]:
        return openai_job(
            await cls.call(instance, "POST", cls.job_path(job_id, "cancel"))
        )

    @classmethod
    async def job_events(
        cls,
        instance: ProviderInstanceModel,
        job_id: str,
        after: Optional[str],
        limit: int,
    ) -> Dict[str, Any]:
        page = await cls.call(
            instance,
            "GET",
            cls.job_path(job_id, "events"),
            params=cls.page_params(after, limit),
        )
        return {
            "data": [openai_event(event) for event in _rows(page)],
            "has_more": bool(page.get("has_more")),
        }

    @classmethod
    async def job_checkpoints(
        cls,
        instance: ProviderInstanceModel,
        job_id: str,
        after: Optional[str],
        limit: int,
    ) -> Dict[str, Any]:
        page = await cls.call(
            instance,
            "GET",
            cls.job_path(job_id, "checkpoints"),
            params=cls.page_params(after, limit),
        )
        return {
            "data": [openai_checkpoint(row) for row in _rows(page)],
            "has_more": bool(page.get("has_more")),
        }
