from typing import List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# NORMALIZED-DEAD-IMPORT: from extensions.chains.BLL_Chains import ChainManager, ChainModel, ChainReferenceModel
# NORMALIZED-DEAD-IMPORT: from extensions.conversations.BLL_Conversations import (
# NORMALIZED-DEAD-CONTINUATION:     ConversationManager,
# NORMALIZED-DEAD-CONTINUATION:     ConversationModel,
# NORMALIZED-DEAD-CONTINUATION:     ConversationReferenceModel,
# NORMALIZED-DEAD-CONTINUATION: )

# Corrected DB Imports
from serverframework.extensions.meta_labels.DB_Labels import (
    AgentLabel,
    ChainLabel,
    ConversationLabel,
    Label,
    ProjectLabel,
    PromptLabel,
    ProviderLabel,
    TaskLabel,
)
# NORMALIZED-DEAD-IMPORT: from extensions.prompts.BLL_Prompts import (
# NORMALIZED-DEAD-CONTINUATION:     PromptManager,
# NORMALIZED-DEAD-CONTINUATION:     PromptModel,
# NORMALIZED-DEAD-CONTINUATION:     PromptReferenceModel,
# NORMALIZED-DEAD-CONTINUATION: )
# NORMALIZED-DEAD-IMPORT: from extensions.tasks.BLL_Tasks import TaskManager, TaskModel, TaskReferenceModel

# Import AbstractLogicManager and Mixins
from serverframework.logic.AbstractLogicManager import StringSearchModel  # Added ParentMixinModel
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    BaseMixinModel,
    NameMixinModel,
)

# Import referenced BLL Models and Managers correctly
from serverframework.logic.BLL_Agents import AgentManager, AgentModel, AgentReferenceModel
from serverframework.logic.BLL_Projects import ProjectManager, ProjectModel, ProjectReferenceModel
from serverframework.logic.BLL_Providers import ProviderManager, ProviderModel, ProviderReferenceModel


# --- Label --- #
class LabelModel(BaseMixinModel, NameMixinModel):
    color: Optional[str] = Field(None, description="Optional color for the label")

    class ReferenceID:
        label_id: str = Field(..., description="The ID of the label")

        class Optional:
            label_id: Optional[str] = None

        class Search:
            label_id: Optional[StringSearchModel] = None

    class Create(BaseModel, NameMixinModel):  # Updated Create Model
        description: Optional[str] = None
        color: Optional[str] = None

    class Update(BaseModel, NameMixinModel.Optional):  # Updated Update Model
        description: Optional[str] = None
        color: Optional[str] = None

    class Search(BaseMixinModel.Search, NameMixinModel.Search):
        color: Optional[StringSearchModel] = None


class LabelReferenceModel(LabelModel.ReferenceID):
    class Optional(LabelModel.ReferenceID.Optional):
        label: Optional[LabelModel] = None


class LabelNetworkModel:
    class POST(BaseModel):
        label: LabelModel.Create  # Corrected POST body

    class PUT(BaseModel):
        label: LabelModel.Update

    class SEARCH(BaseModel):  # Added SEARCH class
        label: LabelModel.Search

    class ResponseSingle(BaseModel):
        label: LabelModel

    class ResponsePlural(BaseModel):  # Added Plural response
        labels: List[LabelModel]


class LabelManager(AbstractBLLManager):
    Model = LabelModel  # Added Model
    ReferenceModel = LabelReferenceModel  # Added ReferenceModel
    NetworkModel = LabelNetworkModel
    DBClass = Label

    def __init__(
        self,  # Added self
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,  # Added target_team_id
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,  # Added target_team_id
            db=db,
        )
        # Initialize related managers to None
        self._agents = None
        self._prompts = None
        self._chains = None
        self._providers = None
        self._tasks = None
        self._projects = None
        self._conversations = None

    @property
    def agents(self):
        if self._agents is None:
            # from extensions.labels.BLL_Labels import AgentLabelManager # Avoid circular import
            self._agents = AgentLabelManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._agents

    @property
    def prompts(self):
        if self._prompts is None:
            # from extensions.labels.BLL_Labels import PromptLabelManager
            self._prompts = PromptLabelManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._prompts  # Added return

    @property
    def chains(self):
        if self._chains is None:
            # from extensions.labels.BLL_Labels import ChainLabelManager
            self._chains = ChainLabelManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._chains

    @property
    def providers(self):
        if self._providers is None:
            # from extensions.labels.BLL_Labels import ProviderLabelManager
            self._providers = ProviderLabelManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,  # Added target_user_id
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._providers

    @property
    def tasks(self):
        if self._tasks is None:
            # from extensions.labels.BLL_Labels import TaskLabelManager
            self._tasks = TaskLabelManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,  # Added db
            )
        return self._tasks

    @property
    def projects(self):
        if self._projects is None:
            # from extensions.labels.BLL_Labels import ProjectLabelManager
            self._projects = ProjectLabelManager(
                requester_id=self.requester.id,  # Added requester_id
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._projects  # Added return

    @property
    def conversations(self):
        if self._conversations is None:
            # from extensions.labels.BLL_Labels import ConversationLabelManager
            self._conversations = ConversationLabelManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,  # Added target_team_id
                db=self.db,
            )
        return self._conversations


# --- AgentLabel --- #
class AgentLabelModel(BaseMixinModel, AgentReferenceModel, LabelReferenceModel):
    class ReferenceID:
        agent_label_id: str = Field(
            ..., description="The ID of the agent label association"
        )

        class Optional:
            agent_label_id: Optional[str] = None

        class Search:
            agent_label_id: Optional[StringSearchModel] = None

    class Create(
        BaseModel, AgentModel.ReferenceID, LabelModel.ReferenceID
    ):  # Added Create Model
        pass

    class Update(
        BaseModel, AgentModel.ReferenceID.Optional, LabelModel.ReferenceID.Optional
    ):
        pass  # Added pass

    class Search(
        BaseMixinModel.Search,
        AgentModel.ReferenceID.Search,
        LabelModel.ReferenceID.Search,
    ):
        pass


class AgentLabelReferenceModel(AgentLabelModel.ReferenceID):
    class Optional(AgentLabelModel.ReferenceID.Optional):
        agent_label: Optional[AgentLabelModel] = None


class AgentLabelNetworkModel:
    class POST(BaseModel):
        agent_label: AgentLabelModel.Create  # Corrected POST body

    class PUT(BaseModel):
        agent_label: AgentLabelModel.Update

    class SEARCH(BaseModel):
        agent_label: AgentLabelModel.Search

    class ResponseSingle(BaseModel):
        agent_label: AgentLabelModel

    class ResponsePlural(BaseModel):
        agent_labels: List[AgentLabelModel]  # Added Plural response


class AgentLabelManager(AbstractBLLManager):
    Model = AgentLabelModel
    ReferenceModel = AgentLabelReferenceModel
    NetworkModel = AgentLabelNetworkModel
    DBClass = AgentLabel

    def __init__(
        self,  # Added self
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,  # Added target_team_id
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,  # Added target_team_id
            db=db,
        )
        self._agents = None
        self._labels = None

    @property  # Made agents a property
    def agents(self):
        if self._agents is None:
            # from logic.BLL_Agents import AgentManager # Use direct import
            self._agents = AgentManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,  # Added target_team_id
                db=self.db,  # Added db
            )
        return self._agents

    @property  # Made labels a property
    def labels(self):
        if self._labels is None:
            self._labels = LabelManager(  # Corrected instantiation
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._labels

    def createValidation(self, entity):
        if not self.agents.get(id=entity.agent_id):
            raise ValueError(f"Agent with ID {entity.agent_id} not found")
        if not self.labels.get(id=entity.label_id):
            raise ValueError(
                f"Label with ID {entity.label_id} not found"
            )  # Added closing paren


# --- PromptLabel --- #
class PromptLabelModel(BaseMixinModel, PromptReferenceModel, LabelReferenceModel):
    class ReferenceID:
        prompt_label_id: str = Field(  # Added field name
            ..., description="The ID of the prompt label association"
        )

        class Optional:  # Added Optional class
            prompt_label_id: Optional[str] = None

        class Search:
            prompt_label_id: Optional[StringSearchModel] = None

    class Create(BaseModel, PromptModel.ReferenceID, LabelModel.ReferenceID):
        pass

    class Update(
        BaseModel,
        PromptModel.ReferenceID.Optional,
        LabelModel.ReferenceID.Optional,  # Added BaseModel
    ):
        pass

    class Search(
        BaseMixinModel.Search,
        PromptModel.ReferenceID.Search,
        LabelModel.ReferenceID.Search,
    ):
        pass


class PromptLabelReferenceModel(PromptLabelModel.ReferenceID):
    # prompt_label: Optional[PromptLabelModel] = None # This should be in Optional
    class Optional(PromptLabelModel.ReferenceID.Optional):
        prompt_label: Optional[PromptLabelModel] = None


class PromptLabelNetworkModel:
    class POST(BaseModel):
        prompt_label: PromptLabelModel.Create

    class PUT(BaseModel):  # Added PUT class
        prompt_label: PromptLabelModel.Update

    class SEARCH(BaseModel):
        prompt_label: PromptLabelModel.Search

    class ResponseSingle(BaseModel):
        prompt_label: PromptLabelModel  # Added prompt_label field

    class ResponsePlural(BaseModel):
        prompt_labels: List[PromptLabelModel]


class PromptLabelManager(AbstractBLLManager):
    Model = PromptLabelModel
    ReferenceModel = PromptLabelReferenceModel
    NetworkModel = PromptLabelNetworkModel
    DBClass = PromptLabel

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
            db=db,  # Added db
        )
        self._prompts = None
        self._labels = None

    @property
    def prompts(self):
        if self._prompts is None:
            # from extensions.prompts.BLL_Prompts import PromptManager # Use direct import
            self._prompts = PromptManager(
                requester_id=self.requester.id,  # Added requester_id
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._prompts  # Added return

    @property
    def labels(self):
        if self._labels is None:
            self._labels = LabelManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,  # Added target_user_id
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._labels

    def createValidation(self, entity):
        if not self.prompts.get(id=entity.prompt_id):
            raise ValueError(f"Prompt with ID {entity.prompt_id} not found")
        if not self.labels.get(id=entity.label_id):  # Added condition
            raise ValueError(f"Label with ID {entity.label_id} not found")


# --- ChainLabel --- #
class ChainLabelModel(BaseMixinModel, ChainReferenceModel, LabelReferenceModel):
    class ReferenceID:
        chain_label_id: str = Field(
            ..., description="The ID of the chain label association"
        )

        class Optional:  # Added Optional class
            chain_label_id: Optional[str] = None

        class Search:
            chain_label_id: Optional[StringSearchModel] = None

    class Create(
        BaseModel, ChainModel.ReferenceID, LabelModel.ReferenceID
    ):  # Added Create Model
        pass

    class Update(
        BaseModel,
        ChainModel.ReferenceID.Optional,
        LabelModel.ReferenceID.Optional,  # Added BaseModel
    ):
        pass

    class Search(
        BaseMixinModel.Search,
        ChainModel.ReferenceID.Search,  # Added ChainModel Ref Search
        LabelModel.ReferenceID.Search,
    ):
        pass  # Added pass


class ChainLabelReferenceModel(ChainLabelModel.ReferenceID):
    # chain_label: Optional[ChainLabelModel] = None # Should be in Optional
    class Optional(ChainLabelModel.ReferenceID.Optional):
        chain_label: Optional[ChainLabelModel] = None


class ChainLabelNetworkModel:
    class POST(BaseModel):  # Added POST class
        chain_label: ChainLabelModel.Create

    class PUT(BaseModel):
        chain_label: ChainLabelModel.Update  # Corrected PUT body

    class SEARCH(BaseModel):  # Added SEARCH class
        chain_label: ChainLabelModel.Search

    class ResponseSingle(BaseModel):
        chain_label: ChainLabelModel

    class ResponsePlural(BaseModel):
        chain_labels: List[ChainLabelModel]


class ChainLabelManager(AbstractBLLManager):
    Model = ChainLabelModel
    ReferenceModel = ChainLabelReferenceModel
    NetworkModel = ChainLabelNetworkModel
    DBClass = ChainLabel

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
            db=db,  # Added db
        )
        self._chains = None
        self._labels = None

    @property
    def chains(self):  # Corrected property
        if self._chains is None:
            # from extensions.chains.BLL_Chains import ChainManager
            self._chains = ChainManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._chains

    @property
    def labels(self):
        if self._labels is None:
            self._labels = LabelManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._labels  # Added return

    def createValidation(self, entity):
        if not self.chains.get(id=entity.chain_id):
            raise ValueError(f"Chain with ID {entity.chain_id} not found")
        if not self.labels.get(id=entity.label_id):
            raise ValueError(f"Label with ID {entity.label_id} not found")


# --- ProviderLabel --- #
class ProviderLabelModel(BaseMixinModel, ProviderReferenceModel, LabelReferenceModel):
    class ReferenceID:
        provider_label_id: str = Field(  # Added field name
            ...,
            description="The ID of the provider label association",  # Corrected description
        )

        class Optional:
            provider_label_id: Optional[str] = None

        class Search:
            provider_label_id: Optional[StringSearchModel] = None  # Added Search class

    class Create(BaseModel, ProviderModel.ReferenceID, LabelModel.ReferenceID):
        pass

    class Update(  # Added Update Class
        BaseModel, ProviderModel.ReferenceID.Optional, LabelModel.ReferenceID.Optional
    ):
        pass  # Added pass

    class Search(
        BaseMixinModel.Search,
        ProviderModel.ReferenceID.Search,  # Added ProviderModel Ref Search
        LabelModel.ReferenceID.Search,
    ):
        pass


class ProviderLabelReferenceModel(ProviderLabelModel.ReferenceID):
    provider_label: Optional[ProviderLabelModel] = None  # Added provider_label field

    class Optional(ProviderLabelModel.ReferenceID.Optional):
        provider_label: Optional[ProviderLabelModel] = None


class ProviderLabelNetworkModel:
    class POST(BaseModel):
        provider_label: ProviderLabelModel.Create

    class PUT(BaseModel):
        provider_label: ProviderLabelModel.Update  # Corrected PUT body

    class SEARCH(BaseModel):  # Added SEARCH class
        provider_label: ProviderLabelModel.Search

    class ResponseSingle(BaseModel):
        provider_label: ProviderLabelModel  # Added provider_label field

    class ResponsePlural(BaseModel):
        provider_labels: List[ProviderLabelModel]


class ProviderLabelManager(AbstractBLLManager):
    Model = ProviderLabelModel  # Added Model
    ReferenceModel = ProviderLabelReferenceModel
    NetworkModel = ProviderLabelNetworkModel
    DBClass = ProviderLabel

    def __init__(  # Corrected init definition
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,  # Added target_user_id
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,  # Added target_user_id
            target_team_id=target_team_id,
            db=db,
        )
        self._providers = None  # Initialized providers
        self._labels = None

    @property
    def providers(self):
        if self._providers is None:
            # from logic.BLL_Providers import ProviderManager
            self._providers = ProviderManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._providers

    @property
    def labels(self):
        if self._labels is None:
            self._labels = LabelManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._labels  # Added return

    def createValidation(self, entity):
        if not self.providers.get(
            id=entity.provider_id
        ):  # Changed from extensions to providers
            raise ValueError(f"Provider with ID {entity.provider_id} not found")
        if not self.labels.get(id=entity.label_id):
            raise ValueError(
                f"Label with ID {entity.label_id} not found"
            )  # Added closing paren


# --- TaskLabel --- #
class TaskLabelModel(BaseMixinModel, TaskReferenceModel, LabelReferenceModel):
    class ReferenceID:
        task_label_id: str = Field(
            ..., description="The ID of the task label association"
        )

        class Optional:
            task_label_id: Optional[str] = None

        class Search:  # Added Search class
            task_label_id: Optional[StringSearchModel] = None

    class Create(BaseModel, TaskModel.ReferenceID, LabelModel.ReferenceID):
        pass

    class Update(
        BaseModel, TaskModel.ReferenceID.Optional, LabelModel.ReferenceID.Optional
    ):
        pass

    class Search(
        BaseMixinModel.Search,
        TaskModel.ReferenceID.Search,
        LabelModel.ReferenceID.Search,  # Added LabelModel Ref Search
    ):
        pass


class TaskLabelReferenceModel(TaskLabelModel.ReferenceID):
    # task_label: Optional[TaskLabelModel] = None # Should be in Optional
    class Optional(TaskLabelModel.ReferenceID.Optional):
        task_label: Optional[TaskLabelModel] = None  # Added definition


class TaskLabelNetworkModel:
    class POST(BaseModel):
        task_label: TaskLabelModel.Create  # Added POST class

    class PUT(BaseModel):
        task_label: TaskLabelModel.Update

    class SEARCH(BaseModel):
        task_label: TaskLabelModel.Search

    class ResponseSingle(BaseModel):  # Added ResponseSingle class
        task_label: TaskLabelModel

    class ResponsePlural(BaseModel):
        task_labels: List[TaskLabelModel]  # Added task_labels field


class TaskLabelManager(AbstractBLLManager):
    Model = TaskLabelModel
    ReferenceModel = TaskLabelReferenceModel  # Added ReferenceModel
    NetworkModel = TaskLabelNetworkModel
    DBClass = TaskLabel

    def __init__(  # Corrected init definition
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,  # Added db parameter
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,
            db=db,  # Added db to super call
        )
        self._tasks = None  # Initialized tasks
        self._labels = None

    @property
    def tasks(self):  # Corrected property
        if self._tasks is None:
            # from extensions.tasks.BLL_Tasks import TaskManager
            self._tasks = TaskManager(
                requester_id=self.requester.id,  # Added requester_id
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._tasks

    @property
    def labels(self):  # Corrected property
        if self._labels is None:
            self._labels = LabelManager(
                requester_id=self.requester.id,  # Added requester_id
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,  # Added db
            )
        return self._labels

    def createValidation(self, entity):
        if not self.tasks.get(id=entity.task_id):  # Added validation
            raise ValueError(f"Task with ID {entity.task_id} not found")
        if not self.labels.get(id=entity.label_id):
            raise ValueError(f"Label with ID {entity.label_id} not found")


# --- ProjectLabel --- #
class ProjectLabelModel(BaseMixinModel, ProjectReferenceModel, LabelReferenceModel):
    class ReferenceID:
        project_label_id: str = Field(  # Added field name
            ..., description="The ID of the project label association"
        )

        class Optional:
            project_label_id: Optional[str] = None

        class Search:
            project_label_id: Optional[StringSearchModel] = None

    class Create(BaseModel, ProjectModel.ReferenceID, LabelModel.ReferenceID):
        pass

    class Update(
        BaseModel, ProjectModel.ReferenceID.Optional, LabelModel.ReferenceID.Optional
    ):
        pass

    class Search(
        BaseMixinModel.Search,
        ProjectModel.ReferenceID.Search,
        LabelModel.ReferenceID.Search,  # Added LabelModel Ref Search
    ):
        pass


class ProjectLabelReferenceModel(ProjectLabelModel.ReferenceID):
    # project_label: Optional[ProjectLabelModel] = None # Should be in Optional
    class Optional(ProjectLabelModel.ReferenceID.Optional):
        project_label: Optional[ProjectLabelModel] = None


class ProjectLabelNetworkModel:
    class POST(BaseModel):
        project_label: ProjectLabelModel.Create

    class PUT(BaseModel):
        project_label: ProjectLabelModel.Update

    class SEARCH(BaseModel):
        project_label: ProjectLabelModel.Search  # Added SEARCH class

    class ResponseSingle(BaseModel):
        project_label: ProjectLabelModel

    class ResponsePlural(BaseModel):
        project_labels: List[ProjectLabelModel]


class ProjectLabelManager(AbstractBLLManager):
    Model = ProjectLabelModel
    ReferenceModel = ProjectLabelReferenceModel
    NetworkModel = ProjectLabelNetworkModel
    DBClass = ProjectLabel

    def __init__(
        self,  # Added self
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(  # Corrected super call
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,  # Added target_team_id
            db=db,
        )
        self._projects = None
        self._labels = None

    @property  # Made projects a property
    def projects(self):
        if self._projects is None:
            # from logic.BLL_Projects import ProjectManager # Use direct import
            self._projects = ProjectManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,  # Added target_user_id
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._projects

    @property
    def labels(self):
        if self._labels is None:
            self._labels = LabelManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._labels

    def createValidation(self, entity):
        if not self.projects.get(id=entity.project_id):
            raise ValueError(f"Project with ID {entity.project_id} not found")
        if not self.labels.get(id=entity.label_id):
            raise ValueError(
                f"Label with ID {entity.label_id} not found"
            )  # Added closing paren


# --- ConversationLabel --- #
class ConversationLabelModel(
    BaseMixinModel, ConversationReferenceModel, LabelReferenceModel
):  # Corrected class definition
    class ReferenceID:
        conversation_label_id: str = Field(
            ..., description="The ID of the conversation label association"
        )

        class Optional:  # Added Optional class
            conversation_label_id: Optional[str] = None

        class Search:
            conversation_label_id: Optional[StringSearchModel] = (
                None  # Added Search class
            )

    class Create(BaseModel, ConversationModel.ReferenceID, LabelModel.ReferenceID):
        pass

    class Update(  # Added Update Class
        BaseModel,
        ConversationModel.ReferenceID.Optional,
        LabelModel.ReferenceID.Optional,  # Added LabelModel Ref Optional
    ):
        pass

    class Search(
        BaseMixinModel.Search,  # Added BaseMixin Search
        ConversationModel.ReferenceID.Search,
        LabelModel.ReferenceID.Search,
    ):
        pass


class ConversationLabelReferenceModel(ConversationLabelModel.ReferenceID):
    conversation_label: Optional[ConversationLabelModel] = (
        None  # Added conversation_label field
    )

    class Optional(ConversationLabelModel.ReferenceID.Optional):
        conversation_label: Optional[ConversationLabelModel] = None


class ConversationLabelNetworkModel:
    class POST(BaseModel):
        conversation_label: ConversationLabelModel.Create

    class PUT(BaseModel):
        conversation_label: ConversationLabelModel.Update

    class SEARCH(BaseModel):
        conversation_label: ConversationLabelModel.Search

    class ResponseSingle(BaseModel):
        conversation_label: ConversationLabelModel

    class ResponsePlural(BaseModel):
        conversation_labels: List[ConversationLabelModel]


class ConversationLabelManager(AbstractBLLManager):
    Model = ConversationLabelModel
    ReferenceModel = ConversationLabelReferenceModel  # Added ReferenceModel
    NetworkModel = ConversationLabelNetworkModel
    DBClass = ConversationLabel

    def __init__(
        self,
        requester_id: str,  # Added requester_id
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,  # Added target_team_id
            db=db,
        )
        self._conversations = None
        self._labels = None

    @property
    def conversations(self):
        if self._conversations is None:
            # from logic.BLL_Conversations import ConversationManager # Use direct import
            self._conversations = ConversationManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,  # Added target_user_id
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._conversations

    @property
    def labels(self):
        if self._labels is None:
            self._labels = LabelManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._labels

    def createValidation(self, entity):
        if not self.conversations.get(id=entity.conversation_id):
            raise ValueError(f"Conversation with ID {entity.conversation_id} not found")
        if not self.labels.get(id=entity.label_id):
            raise ValueError(
                f"Label with ID {entity.label_id} not found"
            )  # Added closing paren
