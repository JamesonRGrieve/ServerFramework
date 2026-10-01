from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel

from zephyrex.extensions.ai_tasks.BLL_AI_Tasks import TaskManager, TaskNetworkModel
from zephyrex.logic.BLL_Auth import UserManager, UserModel


class MessageModel(BaseModel):
    message: str


def get_task_manager(
    request: Request,
    user: UserModel = Depends(UserManager.auth),
    target_team_id: Optional[str] = Query(
        None, description="Target team ID for admin operations"
    ),
) -> TaskManager:
    """Build a request-scoped :class:`TaskManager` bound to the app's model registry."""
    return TaskManager(
        model_registry=getattr(request.app.state, "model_registry", None),
        requester_id=user.id,
        target_team_id=target_team_id,
    )


router = APIRouter(
    prefix="/v1/task",
    tags=["Task Management"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Not authenticated"},
        status.HTTP_403_FORBIDDEN: {"description": "Permission denied"},
        status.HTTP_404_NOT_FOUND: {"description": "Task not found"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Server error"},
    },
)


@router.get(
    "",
    summary="List tasks",
    description="Lists tasks visible to the requester.",
    response_model=TaskNetworkModel.ResponsePlural,
    status_code=status.HTTP_200_OK,
)
async def list_tasks(manager: TaskManager = Depends(get_task_manager)):
    tasks = manager.list()
    return TaskNetworkModel.ResponsePlural(tasks=tasks)


@router.post(
    "",
    summary="Create task",
    description="Creates a new task.",
    response_model=TaskNetworkModel.ResponseSingle,
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    task: TaskNetworkModel.POST = Body(..., description="Task to create"),
    manager: TaskManager = Depends(get_task_manager),
):
    created = manager.create(**task.task.model_dump(exclude_unset=True))
    return TaskNetworkModel.ResponseSingle(task=created)


@router.get(
    "/{id}",
    summary="Get task",
    description="Retrieves a single task by ID.",
    response_model=TaskNetworkModel.ResponseSingle,
    status_code=status.HTTP_200_OK,
)
async def get_task(
    id: str = Path(..., description="Task ID"),
    manager: TaskManager = Depends(get_task_manager),
):
    task = manager.get(id=id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task with ID {id} not found")
    return TaskNetworkModel.ResponseSingle(task=task)


@router.put(
    "/{id}",
    summary="Update task",
    description="Updates fields on an existing task.",
    response_model=TaskNetworkModel.ResponseSingle,
    status_code=status.HTTP_200_OK,
)
async def update_task(
    id: str = Path(..., description="Task ID"),
    task: TaskNetworkModel.PUT = Body(..., description="Task fields to update"),
    manager: TaskManager = Depends(get_task_manager),
):
    updated = manager.update(id=id, **task.task.model_dump(exclude_unset=True))
    return TaskNetworkModel.ResponseSingle(task=updated)


@router.post(
    "/{id}/complete",
    summary="Mark task as completed",
    description="Marks a task as completed and sets the completion timestamp.",
    response_model=TaskNetworkModel.ResponseSingle,
    status_code=status.HTTP_200_OK,
)
async def complete_task(
    id: str = Path(..., description="Task ID to mark as completed"),
    manager: TaskManager = Depends(get_task_manager),
):
    from datetime import datetime, timezone

    updated = manager.update(
        id=id, status="completed", completed_at=datetime.now(timezone.utc)
    )
    return TaskNetworkModel.ResponseSingle(task=updated)


@router.delete(
    "/{id}",
    summary="Delete task",
    description="Deletes a task by ID.",
    response_model=MessageModel,
    status_code=status.HTTP_200_OK,
)
async def delete_task(
    id: str = Path(..., description="Task ID"),
    manager: TaskManager = Depends(get_task_manager),
):
    manager.delete(id=id)
    return MessageModel(message="Task deleted successfully")
