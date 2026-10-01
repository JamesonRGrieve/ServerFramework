from datetime import datetime
from enum import Enum as PyEnum, IntEnum
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DescriptionMixinModel,
    NameMixinModel,
    ParentMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import TeamModel, UserModel
from zephyrex.logic.BLL_Extensions import AbilityModel


class ExecutionType(PyEnum):
    STANDARD = "standard"
    CONDITIONAL = "conditional"
    ITERATION = "iteration"
    PARALLEL_ITERATION = "parallel_iteration"


class AggregationStrategy(PyEnum):
    FIRST_SUCCESS = "first_success"
    MAJORITY_VOTE = "majority_vote"
    ALL = "all"
    CUSTOM = "custom"


class IterationType(PyEnum):
    FOR_EACH = "for_each"
    WHILE = "while"
    COUNT = "count"


class ChainRunStatus(IntEnum):
    PENDING = 0
    RUNNING = 1
    COMPLETED = 2
    FAILED = 3


class ChainLinkRunStatus(IntEnum):
    PENDING = 0
    READY = 1
    RUNNING = 2
    COMPLETED = 3
    FAILED = 4
    SKIPPED = 5
    AGGREGATING = 6


class ChainModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel,
    DescriptionMixinModel,
    UserModel.Reference.ID.Optional,
    TeamModel.Reference.ID.Optional,
):
    favourite: bool = Field(
        False, description="Whether the user has marked this chain as a favourite"
    )

    # Database metadata
    table_comment: ClassVar[str] = "A Chain represents a collection of ChainSteps."

    class Create(
        BaseModel,
        NameMixinModel,
        DescriptionMixinModel,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        favourite: bool = Field(
            False, description="Whether the user has marked this chain as a favourite"
        )

    class Update(
        BaseModel,
        NameMixinModel.Optional,
        DescriptionMixinModel.Optional,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        favourite: Optional[bool] = Field(
            None, description="Whether the user has marked this chain as a favourite"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        NameMixinModel.Search,
        DescriptionMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        favourite: Optional[bool] = None


class ChainNetworkModel:
    class POST(BaseModel):
        chain: ChainModel.Create

    class PUT(BaseModel):
        chain: ChainModel.Update

    class SEARCH(BaseModel):
        chain: ChainModel.Search

    class ResponseSingle(BaseModel):
        chain: ChainModel

    class ResponsePlural(BaseModel):
        chains: List[ChainModel]


class ChainManager(AbstractBLLManager):
    _model = ChainModel
    NetworkModel = ChainNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._links = None
        self._runs = None

    @property
    def DB(self):
        """Get the SQLAlchemy model class for this manager."""
        return self.Model.DB

    @property
    def links(self):
        if self._links is None:
            self._links = ChainLinkManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._links

    @property
    def runs(self):
        if self._runs is None:
            self._runs = ChainRunManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._runs


class ChainLinkModel(
    ApplicationModel,
    UpdateMixinModel,
    ParentMixinModel,
    ChainModel.Reference.ID.Optional,
    AbilityModel.Reference.ID.Optional,
):
    agent_id: str = Field(..., description="ID of the agent executing this step")
    link_of_chain_id: str = Field(
        ..., description="ID of the chain this link belongs to"
    )
    content: Optional[str] = Field(
        None, description="Additional text content injected with this step"
    )

    # Execution control fields
    execution_type: ExecutionType = Field(
        ExecutionType.STANDARD,
        description="Determines how the step executes: standard, conditional, iteration, parallel_iteration",
    )

    # Conditional execution
    condition_expression: Optional[str] = Field(
        None,
        description="Expression to evaluate for conditional steps. If true, the 'then' branch is taken, otherwise the 'else' branch",
    )

    # Iteration configuration
    iteration_type: Optional[IterationType] = Field(
        None,
        description="Type of iteration: for_each (iterate over collection), while (repeat while condition is true), count (repeat fixed number of times)",
    )
    iteration_expression: Optional[str] = Field(
        None,
        description="Expression providing the collection for for_each loops, condition for while loops, or count for count loops",
    )
    iteration_variable: Optional[str] = Field(
        None,
        description="Variable name that will contain the current item during iteration",
    )
    max_iterations: Optional[int] = Field(
        None, description="Maximum number of iterations allowed for safety"
    )

    # Parallel execution configuration
    parallel_execution: bool = Field(
        False,
        description="When true, iterations are executed concurrently rather than sequentially",
    )
    aggregation_strategy: Optional[AggregationStrategy] = Field(
        None,
        description="Strategy for combining results from parallel iterations: first_success, majority_vote, all, or custom",
    )
    aggregation_expression: Optional[str] = Field(
        None,
        description="Custom expression for aggregating results when using custom aggregation strategy",
    )
    max_parallel_instances: Optional[int] = Field(
        None, description="Maximum number of instances that can execute concurrently"
    )

    # Database metadata
    table_comment: ClassVar[str] = (
        "A ChainLink represents a step in a Chain, supporting various execution patterns "
        "including standard, conditional, iteration, and parallel execution."
    )

    class Create(
        BaseModel,
        ParentMixinModel.Optional,
        ChainModel.Reference.ID.Optional,
        AbilityModel.Reference.ID.Optional,
    ):
        agent_id: str = Field(..., description="ID of the agent executing this step")
        link_of_chain_id: str = Field(
            ..., description="ID of the chain this link belongs to"
        )
        content: Optional[str] = Field(
            None, description="Additional text content injected with this step"
        )
        execution_type: ExecutionType = Field(
            ExecutionType.STANDARD, description="How the step executes"
        )
        condition_expression: Optional[str] = Field(
            None, description="Expression for conditional steps"
        )
        iteration_type: Optional[IterationType] = Field(
            None, description="Type of iteration"
        )
        iteration_expression: Optional[str] = Field(
            None, description="Iteration expression"
        )
        iteration_variable: Optional[str] = Field(
            None, description="Variable name for iteration"
        )
        max_iterations: Optional[int] = Field(
            None, description="Maximum iterations allowed"
        )
        parallel_execution: bool = Field(False, description="Concurrent execution flag")
        aggregation_strategy: Optional[AggregationStrategy] = Field(
            None, description="Result aggregation strategy"
        )
        aggregation_expression: Optional[str] = Field(
            None, description="Custom aggregation expression"
        )
        max_parallel_instances: Optional[int] = Field(
            None, description="Maximum parallel instances"
        )

        @model_validator(mode="after")
        def validate_execution_type_fields(self):
            """Validate execution type field requirements."""
            if self.execution_type == ExecutionType.CONDITIONAL:
                if not self.condition_expression:
                    raise ValueError(
                        "condition_expression is required for conditional execution"
                    )
            elif self.execution_type in [
                ExecutionType.ITERATION,
                ExecutionType.PARALLEL_ITERATION,
            ]:
                if not all(
                    [
                        self.iteration_type,
                        self.iteration_expression,
                        self.iteration_variable,
                    ]
                ):
                    raise ValueError(
                        "iteration_type, iteration_expression, and iteration_variable are required for iteration execution"
                    )
                if (
                    self.execution_type == ExecutionType.PARALLEL_ITERATION
                    and not self.parallel_execution
                ):
                    raise ValueError(
                        "parallel_execution must be True for parallel_iteration execution type"
                    )

            # Validate aggregation strategy
            if self.aggregation_strategy == AggregationStrategy.CUSTOM:
                if not self.aggregation_expression:
                    raise ValueError(
                        "aggregation_expression is required for custom aggregation strategy"
                    )

            return self

    class Update(
        BaseModel,
        ParentMixinModel.Optional,
        ChainModel.Reference.ID.Optional,
        AbilityModel.Reference.ID.Optional,
    ):
        agent_id: Optional[str] = Field(
            None, description="ID of the agent executing this step"
        )
        link_of_chain_id: Optional[str] = Field(
            None, description="ID of the chain this link belongs to"
        )
        content: Optional[str] = Field(None, description="Additional text content")
        execution_type: Optional[ExecutionType] = Field(
            None, description="Execution type"
        )
        condition_expression: Optional[str] = Field(
            None, description="Conditional expression"
        )
        iteration_type: Optional[IterationType] = Field(
            None, description="Iteration type"
        )
        iteration_expression: Optional[str] = Field(
            None, description="Iteration expression"
        )
        iteration_variable: Optional[str] = Field(
            None, description="Iteration variable"
        )
        max_iterations: Optional[int] = Field(None, description="Maximum iterations")
        parallel_execution: Optional[bool] = Field(
            None, description="Parallel execution flag"
        )
        aggregation_strategy: Optional[AggregationStrategy] = Field(
            None, description="Aggregation strategy"
        )
        aggregation_expression: Optional[str] = Field(
            None, description="Aggregation expression"
        )
        max_parallel_instances: Optional[int] = Field(
            None, description="Maximum parallel instances"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        ParentMixinModel.Search,
        ChainModel.Reference.ID.Search,
        AbilityModel.Reference.ID.Search,
    ):
        agent_id: Optional[StringSearchModel] = None
        link_of_chain_id: Optional[StringSearchModel] = None
        content: Optional[StringSearchModel] = None
        execution_type: Optional[ExecutionType] = None
        parallel_execution: Optional[bool] = None
        aggregation_strategy: Optional[AggregationStrategy] = None


class ChainLinkNetworkModel:
    class POST(BaseModel):
        chain_link: ChainLinkModel.Create

    class PUT(BaseModel):
        chain_link: ChainLinkModel.Update

    class SEARCH(BaseModel):
        chain_link: ChainLinkModel.Search

    class ResponseSingle(BaseModel):
        chain_link: ChainLinkModel

    class ResponsePlural(BaseModel):
        chain_links: List[ChainLinkModel]


class ChainLinkManager(AbstractBLLManager):
    _model = ChainLinkModel
    NetworkModel = ChainLinkNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._dependencies = None
        self._runs = None

    @property
    def DB(self):
        """Get the SQLAlchemy model class for this manager."""
        return self.Model.DB

    @property
    def dependencies(self):
        if self._dependencies is None:
            self._dependencies = ChainLinkDependencyManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._dependencies

    @property
    def runs(self):
        if self._runs is None:
            self._runs = ChainLinkRunManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._runs


class ChainLinkDependencyModel(ApplicationModel):
    prerequisite_chain_link_id: str = Field(
        ..., description="ID of the prerequisite chain link"
    )
    dependent_chain_link_id: str = Field(
        ..., description="ID of the dependent chain link"
    )
    condition_value: Optional[bool] = Field(
        None,
        description="For conditional dependencies: NULL means always follow this dependency, TRUE means follow only if condition is true, FALSE means follow only if condition is false",
    )

    # Database metadata
    table_comment: ClassVar[str] = (
        "Defines dependencies between chain steps, supporting conditional paths. "
        "NULL condition_value means always follow this dependency, TRUE means follow only if condition is true, FALSE means follow only if condition is false."
    )

    class Create(BaseModel):
        prerequisite_chain_link_id: str = Field(
            ..., description="ID of the prerequisite chain link"
        )
        dependent_chain_link_id: str = Field(
            ..., description="ID of the dependent chain link"
        )
        condition_value: Optional[bool] = Field(
            None, description="Condition value for dependency"
        )

    class Update(BaseModel):
        condition_value: Optional[bool] = Field(
            None, description="Condition value for dependency"
        )

    class Search(ApplicationModel.Search):
        prerequisite_chain_link_id: Optional[StringSearchModel] = None
        dependent_chain_link_id: Optional[StringSearchModel] = None
        condition_value: Optional[bool] = None


class ChainLinkDependencyNetworkModel:
    class POST(BaseModel):
        chain_link_dependency: ChainLinkDependencyModel.Create

    class PUT(BaseModel):
        chain_link_dependency: ChainLinkDependencyModel.Update

    class SEARCH(BaseModel):
        chain_link_dependency: ChainLinkDependencyModel.Search

    class ResponseSingle(BaseModel):
        chain_link_dependency: ChainLinkDependencyModel

    class ResponsePlural(BaseModel):
        chain_link_dependencies: List[ChainLinkDependencyModel]


class ChainLinkDependencyManager(AbstractBLLManager):
    _model = ChainLinkDependencyModel
    NetworkModel = ChainLinkDependencyNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )

    @property
    def DB(self):
        """Get the SQLAlchemy model class for this manager."""
        return self.Model.DB


class ChainRunModel(
    ApplicationModel,
    UpdateMixinModel,
    ChainModel.Reference.ID,
    UserModel.Reference.ID.Optional,
):
    status: ChainRunStatus = Field(
        ChainRunStatus.PENDING, description="Current status of the chain execution"
    )
    meta_data: Optional[Dict[str, Any]] = Field(
        None, description="Additional execution context and parameters"
    )

    # Database metadata
    table_comment: ClassVar[str] = (
        "Represents a specific execution instance of a Chain."
    )

    class Create(BaseModel, ChainModel.Reference.ID, UserModel.Reference.ID.Optional):
        status: ChainRunStatus = Field(
            ChainRunStatus.PENDING, description="Initial status of the chain execution"
        )
        meta_data: Optional[Dict[str, Any]] = Field(
            None, description="Execution context and parameters"
        )

    class Update(BaseModel):
        status: Optional[ChainRunStatus] = Field(
            None, description="Chain execution status"
        )
        meta_data: Optional[Dict[str, Any]] = Field(
            None, description="Execution metadata"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        ChainModel.Reference.ID.Search,
        UserModel.Reference.ID.Search,
    ):
        status: Optional[ChainRunStatus] = None


class ChainRunNetworkModel:
    class POST(BaseModel):
        chain_run: ChainRunModel.Create

    class PUT(BaseModel):
        chain_run: ChainRunModel.Update

    class SEARCH(BaseModel):
        chain_run: ChainRunModel.Search

    class ResponseSingle(BaseModel):
        chain_run: ChainRunModel

    class ResponsePlural(BaseModel):
        chain_runs: List[ChainRunModel]


class ChainRunManager(AbstractBLLManager):
    _model = ChainRunModel
    NetworkModel = ChainRunNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._link_runs = None

    @property
    def DB(self):
        """Get the SQLAlchemy model class for this manager."""
        return self.Model.DB

    @property
    def link_runs(self):
        if self._link_runs is None:
            self._link_runs = ChainLinkRunManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._link_runs


class ChainLinkRunModel(
    ApplicationModel,
    UpdateMixinModel,
    ParentMixinModel,
    ChainRunModel.Reference.ID,
    ChainLinkModel.Reference.ID,
):
    # Iteration tracking
    iteration_index: Optional[int] = Field(
        None, description="For iteration steps, the current iteration number (0-based)"
    )
    iteration_value: Optional[Dict[str, Any]] = Field(
        None, description="For for_each iterations, contains the current item value"
    )
    iteration_complete: Optional[bool] = Field(
        None,
        description="For iteration steps, indicates whether all iterations are complete",
    )

    # Parallel execution tracking
    is_aggregation_step: bool = Field(
        False,
        description="Identifies the special step that aggregates results from parallel iterations",
    )
    aggregated_result: Optional[Dict[str, Any]] = Field(
        None,
        description="For aggregation steps, contains the combined result from all parallel iterations",
    )

    # Conditional execution tracking
    condition_result: Optional[bool] = Field(
        None,
        description="For conditional steps, stores the result of the condition evaluation",
    )

    # General execution state
    status: ChainLinkRunStatus = Field(
        ChainLinkRunStatus.PENDING, description="Current status of this step execution"
    )
    start_time: Optional[datetime] = Field(
        None, description="When execution of this step began"
    )
    end_time: Optional[datetime] = Field(
        None,
        description="When execution of this step completed (successfully or with failure)",
    )
    result: Optional[Dict[str, Any]] = Field(
        None, description="Output data produced by this step execution"
    )
    error: Optional[str] = Field(
        None, description="Error information if the step failed"
    )

    # Database metadata
    table_comment: ClassVar[str] = (
        "Records the execution of a specific ChainLink within a ChainRun, "
        "tracking status, results, and execution-specific data."
    )

    class Create(
        BaseModel,
        ParentMixinModel.Optional,
        ChainRunModel.Reference.ID,
        ChainLinkModel.Reference.ID,
    ):
        iteration_index: Optional[int] = Field(None, description="Iteration index")
        iteration_value: Optional[Dict[str, Any]] = Field(
            None, description="Iteration value"
        )
        is_aggregation_step: bool = Field(False, description="Is aggregation step")
        condition_result: Optional[bool] = Field(None, description="Condition result")
        status: ChainLinkRunStatus = Field(
            ChainLinkRunStatus.PENDING, description="Initial execution status"
        )

    class Update(BaseModel):
        iteration_index: Optional[int] = Field(None, description="Iteration index")
        iteration_value: Optional[Dict[str, Any]] = Field(
            None, description="Iteration value"
        )
        iteration_complete: Optional[bool] = Field(
            None, description="Iteration complete"
        )
        is_aggregation_step: Optional[bool] = Field(
            None, description="Is aggregation step"
        )
        aggregated_result: Optional[Dict[str, Any]] = Field(
            None, description="Aggregated result"
        )
        condition_result: Optional[bool] = Field(None, description="Condition result")
        status: Optional[ChainLinkRunStatus] = Field(
            None, description="Execution status"
        )
        start_time: Optional[datetime] = Field(None, description="Start time")
        end_time: Optional[datetime] = Field(None, description="End time")
        result: Optional[Dict[str, Any]] = Field(None, description="Execution result")
        error: Optional[str] = Field(None, description="Error information")

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        ParentMixinModel.Search,
        ChainRunModel.Reference.ID.Search,
        ChainLinkModel.Reference.ID.Search,
    ):
        iteration_index: Optional[int] = None
        iteration_complete: Optional[bool] = None
        is_aggregation_step: Optional[bool] = None
        condition_result: Optional[bool] = None
        status: Optional[ChainLinkRunStatus] = None


class ChainLinkRunNetworkModel:
    class POST(BaseModel):
        chain_link_run: ChainLinkRunModel.Create

    class PUT(BaseModel):
        chain_link_run: ChainLinkRunModel.Update

    class SEARCH(BaseModel):
        chain_link_run: ChainLinkRunModel.Search

    class ResponseSingle(BaseModel):
        chain_link_run: ChainLinkRunModel

    class ResponsePlural(BaseModel):
        chain_link_runs: List[ChainLinkRunModel]


class ChainLinkRunManager(AbstractBLLManager):
    _model = ChainLinkRunModel
    NetworkModel = ChainLinkRunNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )

    @property
    def DB(self):
        """Get the SQLAlchemy model class for this manager."""
        return self.Model.DB


# Extension hooks for integrating chains with other models
try:
    from zephyrex.extensions.ai_agents.BLL_AI_Agents import ActivityModel

    # Extend Activity model with chain link reference
    class ActivityChainExtension(ChainLinkModel.Reference.ID.Optional):
        pass

    # Add the extension to ActivityModel
    ActivityModel.__bases__ = ActivityModel.__bases__ + (ActivityChainExtension,)

except ImportError:
    # AI Agents extension not available
    pass
