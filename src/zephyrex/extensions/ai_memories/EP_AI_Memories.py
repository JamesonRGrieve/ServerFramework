from enum import Enum
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, status
from pydantic import BaseModel

from zephyrex.endpoints.AbstractEndpointRouter import MessageModel
from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentManager
from zephyrex.extensions.ai_memories.BLL_AI_Memories import (
    MemoryManager,
    MemoryModel,
    MemoryNetworkModel,
)
from zephyrex.logic.BLL_Auth import User, UserManager


class MemorySourceType(str, Enum):
    TEXT = "text"
    FILE = "file"
    URL = "url"
    YOUTUBE = "youtube"


class MemoryImportItem(BaseModel):
    type: MemorySourceType
    content: Dict[str, Any]


class MemoryImport(BaseModel):
    items: List[MemoryImportItem]


def get_memory_manager(user: User = Depends(UserManager.auth)):
    """Get an initialized MemoryManager instance."""
    return MemoryManager(requester_id=user.id)


def get_agent_memory_manager(
    agent_name: str, collection_id: str = "0", user: User = Depends(UserManager.auth)
):
    """Get a memory manager for a specific agent and collection."""
    agent = AgentManager(requester_id=user.id).get(name=agent_name)
    return MemoryManager(
        requester_id=user.id, agent_name=agent_name, collection_number=collection_id
    )


# Create router with standard responses
router = APIRouter(
    prefix="/v1/memory",
    tags=["Memory Management"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Not authenticated"},
        status.HTTP_403_FORBIDDEN: {"description": "Permission denied"},
        status.HTTP_404_NOT_FOUND: {"description": "Resource not found"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Server error"},
    },
)

# Example responses for documentation
memory_query_example = {
    "content": {
        "application/json": {
            "example": {
                "memories": [
                    {
                        "id": "m1e2m3o4-5678-90ab-cdef-123456789012",
                        "external_source_name": "user input",
                        "description": "Information about machine learning",
                        "text": "Machine learning is a branch of artificial intelligence...",
                        "relevance_score": 0.89,
                        "timestamp": "2025-03-15T14:32:45Z",
                    }
                ]
            }
        }
    }
}

memory_export_example = {
    "content": {
        "application/json": {
            "example": {
                "memories": [
                    {
                        "collection_id": "0",
                        "memories": [
                            {
                                "external_source_name": "user input",
                                "description": "Information about machine learning",
                                "text": "Machine learning is a branch of artificial intelligence...",
                                "timestamp": "2025-03-15T14:32:45Z",
                            }
                        ],
                    }
                ]
            }
        }
    }
}

memory_import_example = {
    "content": {
        "application/json": {
            "example": {
                "items": [
                    {
                        "type": "text",
                        "content": {
                            "user_input": "Information about machine learning",
                            "text": "Machine learning is a branch of artificial intelligence...",
                        },
                    },
                    {
                        "type": "url",
                        "content": {
                            "url": "https://example.com/machine-learning-article"
                        },
                    },
                ]
            }
        }
    }
}

external_sources_example = {
    "content": {
        "application/json": {
            "example": {
                "external_sources": [
                    "user input",
                    "https://example.com/machine-learning-article",
                    "file document.pdf",
                ]
            }
        }
    }
}


@router.get(
    "/agent/{agent_name}/collection/{collection_id}",
    summary="List memories",
    description="Lists memories for an agent from a specific collection.",
    response_model=Dict[str, List[MemoryModel]],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "Memories retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "memories": [
                            {
                                "id": "m1e2m3o4-5678-90ab-cdef-123456789012",
                                "agent_id": "a1g2e3n4-5678-90ab-cdef-123456789012",
                                "conversation_id": "c1o2n3v4-5678-90ab-cdef-123456789012",
                                "text": "Machine learning is a branch of artificial intelligence...",
                                "external_source": "user input",
                                "description": "Information about machine learning",
                            }
                        ]
                    }
                }
            },
        },
        status.HTTP_404_NOT_FOUND: {"description": "Agent not found"},
    },
)
async def list_memories(
    agent_name: str = Path(..., description="Agent name"),
    collection_id: str = Path(..., description="Collection ID"),
    manager: MemoryManager = Depends(get_memory_manager),
):
    """List memories for an agent from a specific collection."""
    # Create agent memory manager
    memory_manager = get_agent_memory_manager(
        agent_name=agent_name, collection_id=collection_id, user=manager.requester
    )

    # Get memories from the collection
    memories = memory_manager.list(
        filters=[
            MemoryModel.agent_id == memory_manager.agent_id,
            MemoryModel.conversation_id
            == (None if collection_id == "0" else collection_id),
        ]
    )

    return {"memories": memories}


@router.post(
    "/agent/{agent_name}/collection/{collection_id}/query",
    summary="Query memories",
    description="Queries memories based on relevance to the input text.",
    response_model=Dict[str, List[Dict[str, Any]]],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "Memory query results retrieved successfully",
            **memory_query_example,
        },
        status.HTTP_404_NOT_FOUND: {"description": "Agent not found"},
    },
)
async def query_memories(
    agent_name: str = Path(..., description="Agent name"),
    collection_id: str = Path(..., description="Collection ID"),
    query_text: str = Body(
        ..., embed=True, description="Text to query against memories"
    ),
    limit: int = Query(5, description="Maximum number of memories to return"),
    min_relevance_score: float = Query(
        0.0, description="Minimum relevance score threshold"
    ),
    manager: MemoryManager = Depends(get_memory_manager),
):
    """Query memories based on relevance to input text."""
    # Create agent memory manager
    memory_manager = get_agent_memory_manager(
        agent_name=agent_name, collection_id=collection_id, user=manager.requester
    )

    # Query memories
    memories = await memory_manager.get_memories_data(
        user_input=query_text, limit=limit, min_relevance_score=min_relevance_score
    )

    return {"memories": memories}


@router.post(
    "/agent/{agent_name}/collection/{collection_id}",
    summary="Create memory",
    description="Adds a new memory to the specified collection.",
    response_model=MemoryNetworkModel.ResponseSingle,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_201_CREATED: {
            "description": "Memory created successfully",
        },
        status.HTTP_404_NOT_FOUND: {"description": "Agent not found"},
    },
)
async def create_memory(
    agent_name: str = Path(..., description="Agent name"),
    collection_id: str = Path(..., description="Collection ID"),
    memory: MemoryNetworkModel.POST = Body(..., description="Memory to create"),
    manager: MemoryManager = Depends(get_memory_manager),
):
    """Create a new memory."""
    # Create agent memory manager
    memory_manager = get_agent_memory_manager(
        agent_name=agent_name, collection_id=collection_id, user=manager.requester
    )

    # Extract memory data
    body_attr_name = list(vars(memory).keys())[0]
    memory_data = getattr(memory, body_attr_name).model_dump(exclude_unset=True)

    # Set fixed values
    memory_data["agent_id"] = memory_manager.agent_id
    memory_data["conversation_id"] = None if collection_id == "0" else collection_id

    # Write to memory
    # This approach bypasses the BaseMixin.create to call the specialized memory writing method
    success = await memory_manager.write_text_to_memory(
        user_input=memory_data.get("description", ""),
        text=memory_data["text"],
        external_source=memory_data.get("external_source", "user input"),
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to create memory")

    # Get the created memory (not ideal, but we don't have the ID)
    # In a real implementation, write_text_to_memory would return the created memory
    created_memory = memory_manager.list(
        filters=[
            MemoryModel.agent_id == memory_manager.agent_id,
            MemoryModel.conversation_id
            == (None if collection_id == "0" else collection_id),
            MemoryModel.text == memory_data["text"],
        ],
        limit=1,
    )[0]

    return MemoryNetworkModel.ResponseSingle(memory=created_memory)


@router.post(
    "/agent/{agent_name}/import",
    summary="Import memories",
    description="Imports memories from various sources (text, file, URL, YouTube).",
    response_model=MessageModel,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "Memory import started",
            "content": {
                "application/json": {
                    "example": {
                        "message": "Importing 5 memories for agent Research Assistant"
                    }
                }
            },
        },
        status.HTTP_404_NOT_FOUND: {"description": "Agent not found"},
    },
)
async def import_memories(
    agent_name: str = Path(..., description="Agent name"),
    import_data: MemoryImport = Body(
        ..., description="Memories to import", example=memory_import_example
    ),
    manager: MemoryManager = Depends(get_memory_manager),
    authorization: str = Header(..., description="Authorization token"),
):
    """Import memories from various sources."""
    # Get agent
    agent_manager = AgentManager(requester_id=manager.requester.id)
    agent = agent_manager.get(name=agent_name)

    # Start import process for each item
    success_count = 0
    total_count = len(import_data.items)

    for item in import_data.items:
        collection_id = item.content.get("collection_number", "0")
        memory_manager = get_agent_memory_manager(
            agent_name=agent_name, collection_id=collection_id, user=manager.requester
        )

        try:
            if item.type == MemorySourceType.TEXT:
                await memory_manager.write_text_to_memory(
                    user_input=item.content.get("user_input", ""),
                    text=item.content["text"],
                    external_source=item.content.get("external_source", "user input"),
                )
                success_count += 1

            elif item.type == MemorySourceType.URL:
                # Would need to call the URL processing logic
                # This is a placeholder for what would be implemented
                # await memory_manager.learn_from_url(url=item.content["url"])
                pass

            elif item.type == MemorySourceType.FILE:
                # Would need to call the file processing logic
                # await memory_manager.learn_from_file(...)
                pass

            elif item.type == MemorySourceType.YOUTUBE:
                # Would need to call the YouTube processing logic
                # await memory_manager.write_youtube_captions_to_memory(video_id=item.content["video_id"])
                pass

        except Exception as e:
            # Log error but continue with other items
            continue

    return MessageModel(
        message=f"Successfully imported {success_count} of {total_count} memories for agent {agent_name}"
    )


@router.get(
    "/agent/{agent_name}/export",
    summary="Export memories",
    description="Exports all memories for an agent across all collections.",
    response_model=Dict[str, List[Dict[str, Any]]],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "Memories exported successfully",
            **memory_export_example,
        },
        status.HTTP_404_NOT_FOUND: {"description": "Agent not found"},
    },
)
async def export_memories(
    agent_name: str = Path(..., description="Agent name"),
    manager: MemoryManager = Depends(get_memory_manager),
):
    """Export all memories for an agent."""
    # Create agent memory manager
    memory_manager = get_agent_memory_manager(
        agent_name=agent_name, collection_id="0", user=manager.requester
    )  # Default collection, but we'll export all

    # Export collections
    collections = await memory_manager.export_collections_to_json()

    return {"memories": collections}


@router.get(
    "/agent/{agent_name}/collection/{collection_id}/sources",
    summary="List external sources",
    description="Lists unique external sources in the specified collection.",
    response_model=Dict[str, List[str]],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "External sources retrieved successfully",
            **external_sources_example,
        },
        status.HTTP_404_NOT_FOUND: {"description": "Agent not found"},
    },
)
async def list_external_sources(
    agent_name: str = Path(..., description="Agent name"),
    collection_id: str = Path(..., description="Collection ID"),
    manager: MemoryManager = Depends(get_memory_manager),
):
    """List unique external sources in a collection."""
    # Create agent memory manager
    memory_manager = get_agent_memory_manager(
        agent_name=agent_name, collection_id=collection_id, user=manager.requester
    )

    # Get external sources
    sources = await memory_manager.get_external_data_sources()

    return {"external_sources": sources}


@router.delete(
    "/agent/{agent_name}/collection/{collection_id}/source/{source}",
    summary="Delete memories by source",
    description="Deletes all memories from a specific external source.",
    response_model=MessageModel,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "Memories deleted successfully",
            "content": {
                "application/json": {
                    "example": {
                        "message": "Memories from source 'file document.pdf' deleted"
                    }
                }
            },
        },
        status.HTTP_404_NOT_FOUND: {"description": "Agent or source not found"},
    },
)
async def delete_memories_by_source(
    agent_name: str = Path(..., description="Agent name"),
    collection_id: str = Path(..., description="Collection ID"),
    source: str = Path(..., description="External source name"),
    manager: MemoryManager = Depends(get_memory_manager),
    authorization: str = Header(..., description="Authorization token"),
):
    """Delete all memories from a specific external source."""
    # Verify admin access
    # if not is_admin(email=manager.requester.email, api_key=authorization):
    #     raise HTTPException(status_code=403, detail="Admin access required")

    # Create agent memory manager
    memory_manager = get_agent_memory_manager(
        agent_name=agent_name, collection_id=collection_id, user=manager.requester
    )

    # Delete memories
    result = await memory_manager.delete_memories_from_external_source(
        external_source=source
    )

    if not result:
        raise HTTPException(
            status_code=404, detail=f"No memories found from source '{source}'"
        )

    return MessageModel(message=f"Memories from source '{source}' deleted")


@router.delete(
    "/agent/{agent_name}/collection/{collection_id}",
    summary="Delete collection",
    description="Deletes all memories in the specified collection.",
    response_model=MessageModel,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "Collection deleted successfully",
            "content": {
                "application/json": {
                    "example": {"message": "Collection deleted successfully"}
                }
            },
        },
        status.HTTP_404_NOT_FOUND: {"description": "Agent not found"},
    },
)
async def delete_collection(
    agent_name: str = Path(..., description="Agent name"),
    collection_id: str = Path(..., description="Collection ID"),
    manager: MemoryManager = Depends(get_memory_manager),
    authorization: str = Header(..., description="Authorization token"),
):
    """Delete all memories in a collection."""
    # Verify admin access
    # if not is_admin(email=manager.requester.email, api_key=authorization):
    #     raise HTTPException(status_code=403, detail="Admin access required")

    # Create agent memory manager
    memory_manager = get_agent_memory_manager(
        agent_name=agent_name, collection_id=collection_id, user=manager.requester
    )

    # Wipe memory collection
    result = await memory_manager.wipe_memory(
        collection_id if collection_id != "0" else None
    )

    if not result:
        raise HTTPException(status_code=500, detail="Failed to delete collection")

    return MessageModel(message="Collection deleted successfully")


@router.delete(
    "/agent/{agent_name}/collection/{collection_id}/{memory_id}",
    summary="Delete memory",
    description="Deletes a specific memory by ID.",
    response_model=MessageModel,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "Memory deleted successfully",
            "content": {
                "application/json": {
                    "example": {"message": "Memory deleted successfully"}
                }
            },
        },
        status.HTTP_404_NOT_FOUND: {"description": "Memory not found"},
    },
)
async def delete_memory(
    agent_name: str = Path(..., description="Agent name"),
    collection_id: str = Path(..., description="Collection ID"),
    memory_id: str = Path(..., description="Memory ID"),
    manager: MemoryManager = Depends(get_memory_manager),
):
    """Delete a specific memory."""
    # Create agent memory manager
    memory_manager = get_agent_memory_manager(
        agent_name=agent_name, collection_id=collection_id, user=manager.requester
    )

    # Delete memory
    result = await memory_manager.delete_memory(key=memory_id)

    if not result:
        raise HTTPException(status_code=404, detail="Memory not found")

    return MessageModel(message="Memory deleted successfully")
