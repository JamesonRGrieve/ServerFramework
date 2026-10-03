# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fine-tuning models through providers' fine-tuning APIs (OpenAI's).

Each provider instance is one fine-tuning account: its API key and
address. A job is made from a chat-format JSONL dataset, checked (see
TuningProvider) before it is uploaded. The provider trains the model and
its answer is the source of truth for a job; the local ``tuning_jobs``
table (see BLL_AI_Tuning) mirrors it whenever a job is made, refreshed
or cancelled.

When a job succeeds its ``fine_tuned_model`` names the tuned model (on
OpenAI, ``ft:<base>:<org>:<suffix>:<id>``). To use it, make an ``ai``
provider instance of the same provider (``openai``) with the account's
API key and that name as its model (the instance's ``model_name``); the
``ai`` abilities on that instance then use the tuned model. A
checkpoint's ``model`` works the same way.

Abilities act for the user named by ``requester_id``, on the accounts
that user can see and the jobs that user owns.
"""

from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.ai_tuning.BLL_AI_Tuning import TuningJobManager
from zephyrex.extensions.ai_tuning.TuningProvider import (
    checked_dataset,
    dataset_lines,
)
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency


def _row(model: Any) -> Dict[str, Any]:
    dumped: Dict[str, Any] = model.model_dump(mode="json")
    return dumped


class EXT_AI_Tuning(AbstractStaticExtension):
    name: ClassVar[str] = "ai_tuning"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Fine-tuning models through providers' fine-tuning APIs, with a "
        "local record of each job"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="ai",
                friendly_name="AI",
                optional=True,
                reason="Uses a tuned model, named as an ai instance's model",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "validate_tuning_dataset",
        "create_tuning_job",
        "list_tuning_jobs",
        "get_tuning_job",
        "refresh_tuning_job",
        "cancel_tuning_job",
        "tuning_job_events",
        "tuning_job_checkpoints",
        "list_provider_tuning_jobs",
    }

    @classmethod
    def jobs(cls, requester_id: str) -> TuningJobManager:
        manager: TuningJobManager = cls.as_requester(TuningJobManager, requester_id)
        return manager

    @classmethod
    @ability("validate_tuning_dataset")
    async def validate_tuning_dataset(cls, training_data: str) -> Dict[str, Any]:
        """Check a chat-format JSONL dataset without uploading it:
        ``{examples, bytes}``, or an error naming the first bad line."""
        content = checked_dataset(training_data)
        return {"examples": len(dataset_lines(training_data)), "bytes": len(content)}

    @classmethod
    @ability("create_tuning_job")
    async def create_tuning_job(
        cls,
        requester_id: str,
        provider_instance_id: str,
        base_model: str,
        training_data: str,
        validation_data: Optional[str] = None,
        suffix: Optional[str] = None,
        n_epochs: Optional[int] = None,
        batch_size: Optional[int] = None,
        learning_rate_multiplier: Optional[float] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Upload the dataset(s) to the account and start a job tuning
        ``base_model`` on them: the job as recorded."""
        job = await cls.jobs(requester_id).submit(
            provider_instance_id=provider_instance_id,
            base_model=base_model,
            training_data=training_data,
            validation_data=validation_data,
            suffix=suffix,
            n_epochs=n_epochs,
            batch_size=batch_size,
            learning_rate_multiplier=learning_rate_multiplier,
            seed=seed,
        )
        return _row(job)

    @classmethod
    @ability("list_tuning_jobs")
    async def list_tuning_jobs(cls, requester_id: str) -> List[Dict[str, Any]]:
        """The user's jobs as last recorded."""
        return [_row(job) for job in cls.jobs(requester_id).list()]

    @classmethod
    @ability("get_tuning_job")
    async def get_tuning_job(cls, requester_id: str, job_id: str) -> Dict[str, Any]:
        """One of the user's jobs as last recorded."""
        return _row(cls.jobs(requester_id).get(id=job_id))

    @classmethod
    @ability("refresh_tuning_job")
    async def refresh_tuning_job(cls, requester_id: str, job_id: str) -> Dict[str, Any]:
        """The job as the provider reports it now, recorded."""
        return _row(await cls.jobs(requester_id).refresh(job_id))

    @classmethod
    @ability("cancel_tuning_job")
    async def cancel_tuning_job(cls, requester_id: str, job_id: str) -> Dict[str, Any]:
        """Cancel the job at the provider: the job as recorded."""
        return _row(await cls.jobs(requester_id).cancel(job_id))

    @classmethod
    @ability("tuning_job_events")
    async def tuning_job_events(
        cls,
        requester_id: str,
        job_id: str,
        after: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """The job's events from the provider, newest first."""
        return _row(await cls.jobs(requester_id).events(job_id, after, limit))

    @classmethod
    @ability("tuning_job_checkpoints")
    async def tuning_job_checkpoints(
        cls,
        requester_id: str,
        job_id: str,
        after: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """The job's checkpoints; each ``model`` is usable as a model."""
        return _row(await cls.jobs(requester_id).checkpoints(job_id, after, limit))

    @classmethod
    @ability("list_provider_tuning_jobs")
    async def list_provider_tuning_jobs(
        cls,
        requester_id: str,
        provider_instance_id: str,
        after: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Every job on an account the user can see, as the provider lists
        them, jobs not made here included."""
        return _row(
            await cls.jobs(requester_id).provider_jobs(
                provider_instance_id, after, limit
            )
        )
