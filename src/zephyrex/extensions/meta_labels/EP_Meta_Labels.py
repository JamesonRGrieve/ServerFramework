from fastapi import Depends, Path, Response, status

from zephyrex.endpoints.AbstractEPRouter import (
    AbstractEPRouter,
    ExampleGenerator,
    create_nested_router,
)
from zephyrex.extensions.meta_labels.BLL_Meta_Labels import (
    AgentLabelNetworkModel,
    ChainLabelNetworkModel,
    ConversationLabelNetworkModel,
    LabelManager,
    LabelNetworkModel,
    ProjectLabelNetworkModel,
    PromptLabelNetworkModel,
    ProviderLabelNetworkModel,
    TaskLabelNetworkModel,
)
from zephyrex.logic.BLL_Auth import User, UserManager


def get_label_manager(user: User = Depends(UserManager.auth)):
    """Get an initialized LabelManager instance."""
    return LabelManager(requester_id=user.id)


# Create examples using the ExampleGenerator
label_examples = {
    "get": {
        "label": ExampleGenerator.generate_example_for_model(
            LabelNetworkModel.ResponseSingle.model_fields["label"].annotation
        )
    },
    "create": {
        "label": ExampleGenerator.generate_example_for_model(
            LabelNetworkModel.POST.model_fields["label"].annotation
        )
    },
}

# Create the main label router using AbstractEPRouter with examples
label_router = AbstractEPRouter(
    prefix="/v1/label",
    tags=["Label Management"],
    manager_factory=get_label_manager,
    network_model_cls=LabelNetworkModel,
    resource_name="label",
    example_overrides=label_examples,
)

# Create nested routers for label associations
agent_label_router = create_nested_router(
    parent_prefix="/v1/agent",
    parent_param_name="agent_id",
    child_resource_name="label",
    tags=["Label Management"],
    manager_factory=get_label_manager,
    network_model_cls=AgentLabelNetworkModel,
    manager_property="agents",
)

prompt_label_router = create_nested_router(
    parent_prefix="/v1/prompt",
    parent_param_name="prompt_id",
    child_resource_name="label",
    tags=["Label Management"],
    manager_factory=get_label_manager,
    network_model_cls=PromptLabelNetworkModel,
    manager_property="prompts",
)

chain_label_router = create_nested_router(
    parent_prefix="/v1/chain",
    parent_param_name="chain_id",
    child_resource_name="label",
    tags=["Label Management"],
    manager_factory=get_label_manager,
    network_model_cls=ChainLabelNetworkModel,
    manager_property="chains",
)

provider_label_router = create_nested_router(
    parent_prefix="/v1/provider",
    parent_param_name="provider_id",
    child_resource_name="label",
    tags=["Label Management"],
    manager_factory=get_label_manager,
    network_model_cls=ProviderLabelNetworkModel,
    manager_property="providers",
)

task_label_router = create_nested_router(
    parent_prefix="/v1/task",
    parent_param_name="task_id",
    child_resource_name="label",
    tags=["Label Management"],
    manager_factory=get_label_manager,
    network_model_cls=TaskLabelNetworkModel,
    manager_property="tasks",
)

project_label_router = create_nested_router(
    parent_prefix="/v1/project",
    parent_param_name="project_id",
    child_resource_name="label",
    tags=["Label Management"],
    manager_factory=get_label_manager,
    network_model_cls=ProjectLabelNetworkModel,
    manager_property="projects",
)

conversation_label_router = create_nested_router(
    parent_prefix="/v1/conversation",
    parent_param_name="conversation_id",
    child_resource_name="label",
    tags=["Label Management"],
    manager_factory=get_label_manager,
    network_model_cls=ConversationLabelNetworkModel,
    manager_property="conversations",
)


# Add custom routes for label-prompt associations
@label_router.post(
    "/{label_id}/prompt/{prompt_id}",
    summary="Associate label with prompt",
    description="Associates a label with a prompt.",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def associate_label_with_prompt(
    label_id: str = Path(..., description="Label ID"),
    prompt_id: str = Path(..., description="Prompt ID"),
    manager: LabelManager = Depends(get_label_manager),
):
    """Associate a label with a prompt."""
    manager.associate_with_prompt(label_id=label_id, prompt_id=prompt_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@label_router.delete(
    "/{label_id}/prompt/{prompt_id}",
    summary="Remove label from prompt",
    description="Removes a label from a prompt.",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_label_from_prompt(
    label_id: str = Path(..., description="Label ID"),
    prompt_id: str = Path(..., description="Prompt ID"),
    manager: LabelManager = Depends(get_label_manager),
):
    """Remove a label from a prompt."""
    manager.remove_from_prompt(label_id=label_id, prompt_id=prompt_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Export the router
router = label_router
