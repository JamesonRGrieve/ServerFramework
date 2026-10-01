from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Path, Query, status

from zephyrex.endpoints.AbstractEPRouter import (
    AbstractEPRouter,
    MessageModel,
    create_nested_router,
)
from zephyrex.extensions.chains.BLL_Chains import (
    ChainLinkDependencyNetworkModel,
    ChainLinkNetworkModel,
    ChainLinkRunNetworkModel,
    ChainManager,
    ChainNetworkModel,
    ChainRunNetworkModel,
)
from zephyrex.logic.BLL_Auth import User, UserManager


def get_chain_manager(
    user: User = Depends(UserManager.auth),
    target_user_id: Optional[str] = Query(
        None, description="Target user ID for admin operations"
    ),
    target_team_id: Optional[str] = Query(
        None, description="Target team ID for admin operations"
    ),
):
    return ChainManager(
        requester_id=user.id,
        target_user_id=target_user_id or user.id,
        target_team_id=target_team_id,
    )


# Create examples using correct model structure
chain_examples = {
    "get": {
        "chain": {
            "id": "c1h2a3i4-5678-90ab-cdef-123456789012",
            "name": "Data Processing Chain",
            "description": "Chain for processing and analyzing CSV data",
            "favourite": True,
            "created_at": "2023-01-15T12:00:00Z",
            "created_by_user_id": "u1s2e3r4-5678-90ab-cdef-123456789012",
            "user_id": "u1s2e3r4-5678-90ab-cdef-123456789012",
            "team_id": "t1e2a3m4-5678-90ab-cdef-123456789012",
        }
    },
    "create": {
        "chain": {
            "name": "NLP Pipeline",
            "description": "Natural language processing workflow",
            "favourite": False,
            "user_id": "u1s2e3r4-5678-90ab-cdef-123456789012",
            "team_id": "t1e2a3m4-5678-90ab-cdef-123456789012",
        }
    },
}

chain_link_examples = {
    "get": {
        "chain_link": {
            "id": "s1t2e3p4-5678-90ab-cdef-123456789012",
            "chain_id": "c1h2a3i4-5678-90ab-cdef-123456789012",
            "agent_id": "a1g2e3n4-5678-90ab-cdef-123456789012",
            "prompt_type": "standard",
            "prompt": "Process the following data: {data}",
            "target_prompt_id": "p1r2o3m4-5678-90ab-cdef-123456789012",
            "execution_type": "STANDARD",
            "created_at": "2023-01-15T12:05:00Z",
            "created_by_user_id": "u1s2e3r4-5678-90ab-cdef-123456789012",
        }
    },
    "create": {
        "chain_link": {
            "chain_id": "c1h2a3i4-5678-90ab-cdef-123456789012",
            "agent_id": "a1g2e3n4-5678-90ab-cdef-123456789012",
            "prompt_type": "standard",
            "prompt": "Process the following data: {data}",
            "target_prompt_id": "p1r2o3m4-5678-90ab-cdef-123456789012",
            "execution_type": "STANDARD",
        }
    },
}

# Create the chain router
chain_router = AbstractEPRouter(
    prefix="/v1/chain",
    tags=["Chain Management"],
    manager_factory=get_chain_manager,
    network_model_cls=ChainNetworkModel,
    resource_name="chain",
    example_overrides=chain_examples,
)

# Create nested routers for chain steps
chain_link_router = create_nested_router(
    parent_prefix="/v1/chain",
    parent_param_name="chain_id",
    child_resource_name="step",
    tags=["Chain Step Management"],
    manager_factory=get_chain_manager,
    network_model_cls=ChainLinkNetworkModel,
    manager_property="step",
    example_overrides=chain_link_examples,
)

# Create nested router for chain step dependencies
chain_link_dependency_router = create_nested_router(
    parent_prefix="/v1/chain",
    parent_param_name="chain_id",
    child_resource_name="step-dependency",
    tags=["Chain Step Dependency Management"],
    manager_factory=get_chain_manager,
    network_model_cls=ChainLinkDependencyNetworkModel,
    manager_property="step_dependency",
)

# Create nested router for chain runs
chain_run_router = create_nested_router(
    parent_prefix="/v1/chain",
    parent_param_name="chain_id",
    child_resource_name="run",
    tags=["Chain Run Management"],
    manager_factory=get_chain_manager,
    network_model_cls=ChainRunNetworkModel,
    manager_property="run",
)

# Create nested router for chain step runs
chain_link_run_router = create_nested_router(
    parent_prefix="/v1/chain/run",
    parent_param_name="chain_run_id",
    child_resource_name="step-run",
    tags=["Chain Step Run Management"],
    manager_factory=get_chain_manager,
    network_model_cls=ChainLinkRunNetworkModel,
    manager_property="step_run",
)


@chain_router.post(
    "/{id}/execute",
    summary="Execute chain",
    description="Initiates asynchronous execution of a chain.",
    response_model=MessageModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def execute_chain(
    id: str = Path(..., description="Chain ID"),
    meta_data: Dict[str, Any] = Body({}, description="Optional execution parameters"),
    manager: ChainManager = Depends(get_chain_manager),
):
    """Start asynchronous execution of a chain."""
    import asyncio

    # Use the proper static method from ChainRunManager
    run = manager.run.start_chain_run(
        requester_id=manager.requester.id,
        chain_id=id,
        meta_data=meta_data,
        db=manager.db,
    )
    # Placeholder for async execution - in real implementation this would call a method to process the run
    asyncio.create_task(manager.process_chain_run(run.id))
    return MessageModel(message=f"Chain execution started. Run ID: {run.id}")


@chain_router.post(
    "/{id}/reorder-steps",
    summary="Reorder chain steps",
    description="Reorders the steps in a chain based on the provided sequence.",
    response_model=MessageModel,
    status_code=status.HTTP_200_OK,
)
async def reorder_steps(
    id: str = Path(..., description="Chain ID"),
    step_ids: List[str] = Body(..., description="Ordered list of step IDs"),
    manager: ChainManager = Depends(get_chain_manager),
):
    """Reorder steps in a chain."""
    # This would be a custom method that handles step reordering
    manager.reorder_steps(chain_id=id, step_ids=step_ids)
    return MessageModel(message=f"Steps reordered for chain ID: {id}")


@chain_router.get(
    "/{id}/args",
    summary="Get chain arguments",
    description="Retrieves the available arguments that can be used with this chain.",
    status_code=status.HTTP_200_OK,
)
async def get_chain_args(
    id: str = Path(..., description="Chain ID"),
    manager: ChainManager = Depends(get_chain_manager),
):
    """Get available arguments for a chain."""
    args = ChainManager.get_chain_args(
        requester_id=manager.requester.id, chain_id=id, db=manager.db
    )
    return {"arguments": args}


# Export the routers
router = APIRouter()
router.include_router(chain_router)
router.include_router(chain_link_router)
router.include_router(chain_link_dependency_router)
router.include_router(chain_run_router)
router.include_router(chain_link_run_router)
