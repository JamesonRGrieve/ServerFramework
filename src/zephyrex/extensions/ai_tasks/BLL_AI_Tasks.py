"""
Business logic layer for AI Tasks.

Defines the persisted ``TaskModel`` -- a Pydantic model that doubles as the
SQLAlchemy entity via ``DatabaseMixin`` -- and its ``TaskManager``. There is
no separate ``DB_AI_Tasks.py`` module: the current framework generates the
database table directly from this Pydantic model, the same pattern used by
the sibling ``ai_chains`` and ``ai_agents`` extensions (see
``DB_AI_Tasks.py`` for the note on why that file is intentionally empty).
"""

from datetime import datetime
from typing import ClassVar, List, Optional

from pydantic import BaseModel, Field

from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    NumericalSearchModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import TeamModel, UserModel


class TaskModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.ID.Optional,
    TeamModel.Reference.ID.Optional,
):
    """A unit of AI-driven work to schedule, execute, and track."""

    name: str = Field(..., description="The name of the task")
    task_type: str = Field(
        "one_time",
        description="The kind of task: one_time, recurring, conditional, workflow, or ai_agent",
    )
    instructions: str = Field(
        "", description="Instructions describing the work to perform"
    )
    status: str = Field("pending", description="Current task status")
    ai_agent_id: Optional[str] = Field(
        None, description="ID of the agent assigned to execute this task"
    )
    schedule_json: Optional[str] = Field(
        None, description="JSON-encoded schedule configuration for this task"
    )
    dependencies_json: Optional[str] = Field(
        None, description="JSON-encoded list of dependency task IDs"
    )
    task_metadata_json: Optional[str] = Field(
        None, description="JSON-encoded free-form task metadata"
    )
    due_date: Optional[datetime] = Field(None, description="When this task is due")
    completed_at: Optional[datetime] = Field(
        None, description="When this task completed"
    )
    priority: int = Field(2, description="Priority level (1-5)")

    table_comment: ClassVar[str] = (
        "A Task represents a unit of AI-driven work to schedule, execute, and track."
    )

    class Create(
        BaseModel,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        name: str
        task_type: Optional[str] = "one_time"
        instructions: Optional[str] = ""
        ai_agent_id: Optional[str] = None
        priority: Optional[int] = 2
        due_date: Optional[datetime] = None

    class Update(
        BaseModel,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        name: Optional[str] = None
        status: Optional[str] = None
        instructions: Optional[str] = None
        priority: Optional[int] = None
        due_date: Optional[datetime] = None
        completed_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        name: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None
        task_type: Optional[StringSearchModel] = None
        priority: Optional[NumericalSearchModel] = None
        due_date: Optional[DateSearchModel] = None


class TaskNetworkModel:
    class POST(BaseModel):
        task: TaskModel.Create

    class PUT(BaseModel):
        task: TaskModel.Update

    class SEARCH(BaseModel):
        task: TaskModel.Search

    class ResponseSingle(BaseModel):
        task: TaskModel

    class ResponsePlural(BaseModel):
        tasks: List[TaskModel]


class TaskManager(AbstractBLLManager):
    """Business-logic manager for :class:`TaskModel`."""

    Model = TaskModel
    NetworkModel = TaskNetworkModel
