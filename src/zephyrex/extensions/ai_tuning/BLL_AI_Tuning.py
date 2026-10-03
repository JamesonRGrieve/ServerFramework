# SPDX-License-Identifier: AGPL-3.0-or-later
"""The local record of each fine-tuning job.

A job belongs to the user who made it: its owner is the requester
whatever the caller names (ROOT and SYSTEM may name another), and an
update never moves it. The provider is the source of truth for a job;
the row mirrors the provider's answer each time the job is made,
refreshed or cancelled. Over REST the table is read-only (get, list,
search), with routes that act on the provider:

- ``POST /v1/tuning_job/submit``: check and upload the dataset(s) to an
  account the caller can see, start a job, and record it;
- ``POST /v1/tuning_job/{job_id}/refresh`` and ``/cancel``;
- ``GET /v1/tuning_job/{job_id}/events`` and ``/checkpoints``;
- ``GET /v1/tuning_job/account/{provider_instance_id}/jobs``: the
  account's jobs as the provider lists them.
"""

import logging
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    Iterator,
    List,
    Optional,
    Tuple,
    Type,
)

from fastapi import HTTPException
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.database.StaticPermissions import is_root_id, is_system_id
from zephyrex.extensions.ai_tuning.TuningProvider import (
    AbstractTuningProvider,
    checked_dataset,
    checked_hyperparameters,
    checked_limit,
    checked_model,
    checked_seed,
    checked_suffix,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    BaseExternalError,
    InvalidInputExternalError,
    RateLimitExternalError,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderManager,
)
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel

logger = logging.getLogger(__name__)

READ_ONLY = [RouteType.GET, RouteType.LIST, RouteType.SEARCH]
TAGS = ("AI Tuning",)
# What a refresh or cancel copies from the provider's answer.
MIRRORED = (
    "status",
    "fine_tuned_model",
    "error",
    "trained_tokens",
    "hyperparameters",
    "finished_at",
)


def _server_side(requester_id: str) -> bool:
    """ROOT and SYSTEM act on others' behalf; users act as themselves."""
    return is_root_id(requester_id) or is_system_id(requester_id)


def _each(
    kwargs: Dict[str, Any], prepare: Callable[[Dict[str, Any]], Dict[str, Any]]
) -> Dict[str, Any]:
    """``kwargs`` for a create, or each of a batch's ``entities``, prepared."""
    if isinstance(kwargs.get("entities"), list):
        return {**kwargs, "entities": [prepare(dict(e)) for e in kwargs["entities"]]}
    return prepare(dict(kwargs))


class TuningJobModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["TuningJobManager"]]
    provider: str = Field(..., description="The provider (openai_fine_tuning, …)")
    provider_instance_id: str = Field(..., description="The account it runs on")
    provider_job_id: str = Field(..., description="The provider's job id")
    base_model: str = Field(..., description="The model tuned")
    training_file_id: Optional[str] = Field(None, description="The training file")
    validation_file_id: Optional[str] = Field(None, description="The validation file")
    suffix: Optional[str] = Field(None, description="Added to the tuned model's name")
    status: str = Field(..., description="The provider's status for the job")
    fine_tuned_model: Optional[str] = Field(
        None, description="The tuned model's name, once the job succeeds"
    )
    error: Optional[str] = Field(None, description="Why the job failed")
    trained_tokens: Optional[int] = Field(None, description="Billable tokens trained")
    hyperparameters: Optional[Dict[str, Any]] = Field(
        None, description="The hyperparameters the job used"
    )
    finished_at: Optional[datetime] = Field(None, description="When the job finished")
    refreshed_at: datetime = Field(..., description="When the provider was last read")

    table_comment: ClassVar[str] = (
        "Fine-tuning jobs (a local mirror of each provider's job)"
    )

    class Create(BaseModel):
        user_id: Optional[str] = None
        provider: str
        provider_instance_id: str
        provider_job_id: str
        base_model: str
        training_file_id: Optional[str] = None
        validation_file_id: Optional[str] = None
        suffix: Optional[str] = None
        status: str
        fine_tuned_model: Optional[str] = None
        error: Optional[str] = None
        trained_tokens: Optional[int] = None
        hyperparameters: Optional[Dict[str, Any]] = None
        finished_at: Optional[datetime] = None
        refreshed_at: datetime

    class Update(BaseModel):
        status: Optional[str] = None
        fine_tuned_model: Optional[str] = None
        error: Optional[str] = None
        trained_tokens: Optional[int] = None
        hyperparameters: Optional[Dict[str, Any]] = None
        finished_at: Optional[datetime] = None
        refreshed_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        provider_instance_id: Optional[StringSearchModel] = None
        provider_job_id: Optional[StringSearchModel] = None
        base_model: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None
        fine_tuned_model: Optional[StringSearchModel] = None


class SubmitTuningJob(RouteModel):
    provider_instance_id: str = Field(..., description="The account to train on")
    base_model: str = Field(..., description="The model to tune")
    training_data: str = Field(..., description="Chat-format JSONL, one example a line")
    validation_data: Optional[str] = Field(None, description="Chat-format JSONL")
    suffix: Optional[str] = Field(None, description="Added to the tuned model's name")
    n_epochs: Optional[int] = Field(None, description="Epochs (provider's choice)")
    batch_size: Optional[int] = Field(None, description="Batch size")
    learning_rate_multiplier: Optional[float] = Field(
        None, description="Learning-rate multiplier"
    )
    seed: Optional[int] = Field(None, description="For a reproducible job")


class JobAction(RouteModel):
    """A refresh or cancel carries nothing but the job in its path."""


class ProviderJob(RouteModel):
    provider_job_id: str
    base_model: str
    status: str
    fine_tuned_model: Optional[str] = None
    training_file_id: Optional[str] = None
    validation_file_id: Optional[str] = None
    error: Optional[str] = None
    trained_tokens: Optional[int] = None
    hyperparameters: Optional[Dict[str, Any]] = None
    finished_at: Optional[datetime] = None


class ProviderJobsPage(RouteModel):
    data: List[ProviderJob]
    has_more: bool


class JobEvent(RouteModel):
    id: Optional[str] = None
    created_at: Optional[str] = None
    level: Optional[str] = None
    message: Optional[str] = None
    type: Optional[str] = None
    data: Optional[Any] = None


class JobEventsPage(RouteModel):
    data: List[JobEvent]
    has_more: bool


class JobCheckpoint(RouteModel):
    id: Optional[str] = None
    created_at: Optional[str] = None
    model: Optional[str] = Field(None, description="Usable as a model name")
    step: Optional[int] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)


class JobCheckpointsPage(RouteModel):
    data: List[JobCheckpoint]
    has_more: bool


@contextmanager
def provider_answers() -> Iterator[None]:
    """A provider's typed failure as the HTTP answer a route gives."""
    try:
        yield
    except InvalidInputExternalError as exc:
        status = 404 if exc.upstream_status == 404 else 400
        raise HTTPException(status_code=status, detail=exc.message) from exc
    except RateLimitExternalError as exc:
        raise HTTPException(
            status_code=429, detail="The provider is limiting this account's requests"
        ) from exc
    except AuthExternalError as exc:
        raise HTTPException(
            status_code=502, detail="The provider refused this account's API key"
        ) from exc
    except BaseExternalError as exc:
        logger.warning("A fine-tuning provider failed: %s", exc)
        raise HTTPException(
            status_code=502, detail="The provider could not be reached or failed"
        ) from exc


class TuningJobManager(AbstractBLLManager, RouterMixin):
    _model = TuningJobModel
    routes_to_register: ClassVar[Optional[List[RouteType]]] = READ_ONLY

    def create(self, **kwargs: Any) -> Any:
        """Jobs are the requester's; ROOT and SYSTEM may name the owner."""
        requester_id = self.requester.id

        def owned(fields: Dict[str, Any]) -> Dict[str, Any]:
            if not _server_side(requester_id) or not fields.get("user_id"):
                fields["user_id"] = requester_id
            return fields

        return super().create(**_each(kwargs, owned))

    def update(self, id: str, **kwargs: Any) -> Any:
        """An update never moves a job to another owner or team."""
        kwargs.pop("user_id", None)
        kwargs.pop("team_id", None)
        return super().update(id, **kwargs)

    def account(
        self, provider_instance_id: str
    ) -> Tuple[Type[AbstractTuningProvider], ProviderInstanceModel]:
        """The fine-tuning provider and instance for an account the
        requester can see (404 for one they cannot)."""
        ProviderInstanceManager(
            model_registry=self.model_registry, requester_id=self.requester.id
        ).get(id=provider_instance_id)
        root = env("ROOT_ID")
        instance = ProviderInstanceModel.model_validate(
            ProviderInstanceManager(
                model_registry=self.model_registry, requester_id=root
            ).get(id=provider_instance_id),
            from_attributes=True,
        )
        if instance.enabled is False:
            raise InvalidInputExternalError("This account is disabled")
        provider = ProviderManager(
            model_registry=self.model_registry, requester_id=root
        ).get(id=instance.provider_id)
        from zephyrex.extensions.ai_tuning.EXT_AI_Tuning import EXT_AI_Tuning

        for candidate in EXT_AI_Tuning.providers:
            if candidate.name == provider.name and issubclass(
                candidate, AbstractTuningProvider
            ):
                return candidate, instance
        raise InvalidInputExternalError(
            f"{provider_instance_id} is not a fine-tuning account"
        )

    async def submit(
        self,
        *,
        provider_instance_id: str,
        base_model: str,
        training_data: str,
        validation_data: Optional[str] = None,
        suffix: Optional[str] = None,
        n_epochs: Optional[int] = None,
        batch_size: Optional[int] = None,
        learning_rate_multiplier: Optional[float] = None,
        seed: Optional[int] = None,
    ) -> Any:
        """Check everything, then upload and start the job: the record."""
        model = checked_model(base_model)
        named = checked_suffix(suffix)
        hyperparameters = checked_hyperparameters(
            n_epochs, batch_size, learning_rate_multiplier
        )
        chosen_seed = checked_seed(seed)
        training = checked_dataset(training_data)
        validation = (
            None
            if validation_data is None
            else checked_dataset(validation_data, "validation_data")
        )
        provider, instance = self.account(provider_instance_id)
        uploaded = await provider.upload_dataset(instance, training, "training.jsonl")
        validation_file_id = None
        if validation is not None:
            validation_file_id = (
                await provider.upload_dataset(instance, validation, "validation.jsonl")
            )["file_id"]
        job = await provider.create_job(
            instance,
            model,
            uploaded["file_id"],
            validation_file_id,
            named,
            hyperparameters,
            chosen_seed,
        )
        return self.create(
            **job,
            provider=provider.name,
            provider_instance_id=provider_instance_id,
            suffix=named,
            refreshed_at=datetime.now(UTC),
        )

    def mirror(self, record: Any, job: Dict[str, Any]) -> Any:
        """``record`` updated to the provider's ``job``."""
        return self.update(
            record.id,
            **{key: job.get(key) for key in MIRRORED},
            refreshed_at=datetime.now(UTC),
        )

    async def refresh(self, job_id: str) -> Any:
        record = self.get(id=job_id)
        provider, instance = self.account(record.provider_instance_id)
        return self.mirror(
            record, await provider.get_job(instance, record.provider_job_id)
        )

    async def cancel(self, job_id: str) -> Any:
        record = self.get(id=job_id)
        provider, instance = self.account(record.provider_instance_id)
        return self.mirror(
            record, await provider.cancel_job(instance, record.provider_job_id)
        )

    async def events(
        self, job_id: str, after: Optional[str], limit: int
    ) -> JobEventsPage:
        record = self.get(id=job_id)
        provider, instance = self.account(record.provider_instance_id)
        page = await provider.job_events(
            instance, record.provider_job_id, after, checked_limit(limit)
        )
        return JobEventsPage.model_validate(page)

    async def checkpoints(
        self, job_id: str, after: Optional[str], limit: int
    ) -> JobCheckpointsPage:
        record = self.get(id=job_id)
        provider, instance = self.account(record.provider_instance_id)
        page = await provider.job_checkpoints(
            instance, record.provider_job_id, after, checked_limit(limit)
        )
        return JobCheckpointsPage.model_validate(page)

    async def provider_jobs(
        self, provider_instance_id: str, after: Optional[str], limit: int
    ) -> ProviderJobsPage:
        provider, instance = self.account(provider_instance_id)
        page = await provider.list_jobs(instance, after, checked_limit(limit))
        return ProviderJobsPage.model_validate(page)

    @custom_route(
        method="POST",
        path="/submit",
        input_model=SubmitTuningJob,
        output_model=TuningJobModel,
        authentication_type="jwt",
        openapi_tags=TAGS,
        summary="Upload a dataset to an account and start a fine-tuning job",
        expose_in=(ExposeIn.REST,),
    )
    async def submit_route(self, body: SubmitTuningJob) -> Any:
        with provider_answers():
            return await self.submit(**body.model_dump())

    @custom_route(
        method="POST",
        path="/{job_id}/refresh",
        input_model=JobAction,
        output_model=TuningJobModel,
        authentication_type="jwt",
        openapi_tags=TAGS,
        summary="Read a job's state from its provider and record it",
        expose_in=(ExposeIn.REST,),
    )
    async def refresh_route(self, job_id: str, body: JobAction) -> Any:
        with provider_answers():
            return await self.refresh(job_id)

    @custom_route(
        method="POST",
        path="/{job_id}/cancel",
        input_model=JobAction,
        output_model=TuningJobModel,
        authentication_type="jwt",
        openapi_tags=TAGS,
        summary="Cancel a job at its provider",
        expose_in=(ExposeIn.REST,),
    )
    async def cancel_route(self, job_id: str, body: JobAction) -> Any:
        with provider_answers():
            return await self.cancel(job_id)

    @custom_route(
        method="GET",
        path="/{job_id}/events",
        output_model=JobEventsPage,
        authentication_type="jwt",
        openapi_tags=TAGS,
        summary="A job's events at its provider, newest first",
        expose_in=(ExposeIn.REST,),
    )
    async def events_route(
        self, job_id: str, after: Optional[str] = None, limit: int = 20
    ) -> JobEventsPage:
        with provider_answers():
            return await self.events(job_id, after, limit)

    @custom_route(
        method="GET",
        path="/{job_id}/checkpoints",
        output_model=JobCheckpointsPage,
        authentication_type="jwt",
        openapi_tags=TAGS,
        summary="A job's checkpoints, each usable as a model",
        expose_in=(ExposeIn.REST,),
    )
    async def checkpoints_route(
        self, job_id: str, after: Optional[str] = None, limit: int = 10
    ) -> JobCheckpointsPage:
        with provider_answers():
            return await self.checkpoints(job_id, after, limit)

    @custom_route(
        method="GET",
        path="/account/{provider_instance_id}/jobs",
        output_model=ProviderJobsPage,
        authentication_type="jwt",
        openapi_tags=TAGS,
        summary="Every job on an account, as its provider lists them",
        expose_in=(ExposeIn.REST,),
    )
    async def provider_jobs_route(
        self, provider_instance_id: str, after: Optional[str] = None, limit: int = 20
    ) -> ProviderJobsPage:
        with provider_answers():
            return await self.provider_jobs(provider_instance_id, after, limit)
