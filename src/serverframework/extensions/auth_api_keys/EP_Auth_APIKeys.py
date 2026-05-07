generate_api_key_example = {
    "content": {
        "application/json": {
            "example": {
                "name": "My API Key",
                "raw_key": "v2_J8oCSl5CeamrwQ_m7HmBTVWgBP9lNJgkwKjEcjQYTUI",
            }
        }
    }
}
# These should use /key instead of /api-key according to EP.schema.md
key_router = AbstractEPRouter(
    prefix="/v1/key",
    tags=["API Key Management"],
    manager_factory=get_api_key_manager,
    network_model_cls=APIKeyNetworkModel,
    resource_name="api_key",
    example_overrides=key_examples,
)


# API Key custom routes (should be under /key as per schema)
@key_router.post(
    "/generate",
    summary="Generate API key",
    description="Generates a new API key for a user or team.",
    response_model=Dict[str, str],
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_201_CREATED: {
            "description": "API key generated successfully",
            **generate_api_key_example,
        },
    },
)
async def generate_api_key(
    name: str = Body(..., embed=True, description="Name for the API key"),
    user_id: Optional[str] = Body(
        None, embed=True, description="User ID (for user-specific keys)"
    ),
    team_id: Optional[str] = Body(
        None, embed=True, description="Team ID (for team-specific keys)"
    ),
    role_id: Optional[str] = Body(
        None, embed=True, description="Role ID for permissions"
    ),
    manager=Depends(get_api_key_manager),
):
    """Generate a new API key"""
    raw_key = manager.generate_api_key(
        name=name, user_id=user_id, team_id=team_id, role_id=role_id
    )
    return {"name": name, "raw_key": raw_key}


# Use /api/validate as per schema
api_router = APIRouter(prefix="/v1/api", tags=["API Key Management"])


@api_router.post(
    "/validate",
    summary="Validate API key",
    description="Validates an API key without revealing its details.",
    response_model=Dict[str, bool],
    status_code=status.HTTP_200_OK,
)
async def validate_api_key(
    api_key: str = Body(..., embed=True, description="API key to validate"),
    manager=Depends(get_api_key_manager),
):
    """Validate an API key"""
    valid = manager.validate_api_key(raw_key=api_key)
    return {"valid": valid}
