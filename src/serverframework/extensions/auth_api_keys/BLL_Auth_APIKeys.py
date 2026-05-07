import hashlib
import secrets
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from models.ModelBase import BaseMixinModel, UpdateMixinModel
from models.ModelRole import RoleModel, RoleReferenceModel
from models.ModelSearch import StringSearchModel
from models.ModelTeam import TeamModel, TeamReferenceModel
from models.ModelUser import UserModel, UserReferenceModel
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from util.UtilEnv import env

from database.DB_Auth import APIKey, Role, Team, User  # Added APIKey
from logic.BLL_Auth import RoleManager, TeamManager, UserManager
from logic.BLL_Base import AbstractBLLManager


class APIKeyModel(
    BaseMixinModel,
    UpdateMixinModel,
    UserReferenceModel.Optional,
    TeamReferenceModel.Optional,
    RoleReferenceModel.Optional,
):
    name: str = Field(..., description="Name of the API key")
    key_hash: str = Field(..., description="Hashed API key")

    class ReferenceID:
        api_key_id: str = Field(..., description="The ID of the API key")

        class Optional:
            api_key_id: Optional[str] = None

        class Search:
            api_key_id: Optional[StringSearchModel] = None

    class Create(  # Added Create class based on Update and Reference models
        BaseModel,
        UserModel.ReferenceID.Optional,
        TeamModel.ReferenceID.Optional,  # Added Team Reference
        RoleModel.ReferenceID.Optional,
    ):
        name: str = Field(..., description="Name of the API key")
        key_hash: Optional[str] = Field(
            None, description="Hashed API key"
        )  # Made hash optional on create

    class Update(BaseModel):
        name: Optional[str] = Field(None, description="Name of the API key")
        # key_hash should not be updatable directly, removed

    class Search(
        BaseMixinModel.Search,  # Added Base Search Mixin
        UserModel.ReferenceID.Search,
        TeamModel.ReferenceID.Search,
        RoleModel.ReferenceID.Search,
    ):  # Added closing parenthesis and base classes
        name: Optional[StringSearchModel] = None
        key_hash: Optional[StringSearchModel] = None  # Added key_hash search


class APIKeyReferenceModel(APIKeyModel.ReferenceID):
    api_key: Optional[APIKeyModel] = None

    class Optional(APIKeyModel.ReferenceID.Optional):
        api_key: Optional[APIKeyModel] = None


class APIKeyNetworkModel:
    class POST(BaseModel):
        api_key: APIKeyModel.Create  # Changed Update to Create

    class PUT(BaseModel):  # Added PUT model
        api_key: APIKeyModel.Update

    class SEARCH(BaseModel):
        api_key: APIKeyModel.Search  # Added Search model

    class ResponseSingle(BaseModel):
        api_key: APIKeyModel

    class ResponsePlural(BaseModel):  # Added Plural response
        api_keys: list[APIKeyModel]


class APIKeyManager(AbstractBLLManager):
    Model = APIKeyModel
    ReferenceModel = APIKeyReferenceModel
    NetworkModel = APIKeyNetworkModel  # Added Network Model
    DBClass = APIKey

    def __init__(
        self,  # Added self
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,  # Added target_team_id
        db: Optional[Session] = None,
    ):
        super().__init__(  # Corrected super call
            db=db,
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,  # Added target_team_id
        )
        # Corrected indentation
        self._users = None
        self._teams = None
        self._roles = None

    @property
    def users(self):  # Added function definition
        """Get the user manager"""
        # Corrected indentation and logic flow
        if self._users is None:
            self._users = UserManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._users  # Moved return inside function

    @property
    def teams(self):
        """Get the team manager"""
        if self._teams is None:
            self._teams = TeamManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,  # Ensure consistency if needed elsewhere
                target_team_id=self.target_team_id,  # Added target_team_id
                db=self.db,
            )
        return self._teams

    @property
    def roles(self):
        """Get the role manager"""
        if self._roles is None:
            self._roles = RoleManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,  # Added db
            )
        return self._roles

    def createValidation(self, entity: APIKeyModel.Create):  # Added type hint
        """Validate API key creation"""
        if entity.user_id and not User.exists(
            requester_id=self.requester.id, db=self.db, id=entity.user_id
        ):
            raise HTTPException(status_code=404, detail="User not found")
        if entity.team_id and not Team.exists(
            requester_id=self.requester.id, db=self.db, id=entity.team_id
        ):
            raise HTTPException(status_code=404, detail="Team not found")  # Added raise
        # Corrected indentation for role check
        if entity.role_id and not Role.exists(
            requester_id=self.requester.id, db=self.db, id=entity.role_id
        ):
            raise HTTPException(status_code=404, detail="Role not found")
        if entity.user_id and entity.team_id:
            raise HTTPException(
                status_code=400, detail="API key cannot belong to both user and team"
            )

    @staticmethod
    async def verify_system_api_key(  # Added closing parenthesis
        credentials: HTTPAuthorizationCredentials = Security(HTTPBearer()),
    ):  # Moved imports out of signature
        from database.DB_Auth import User
        from logic.BLL_Auth import (
            UserManager,  # Keep local import if specific context needed
        )

        token = credentials.credentials  # Extract the token from "Bearer {token}"
        if not token:  # Simplified check
            raise HTTPException(
                status_code=401,
                detail="Not authenticated",  # More specific detail
            )

        # Check system key first
        system_key = env("AGINFRASTRUCTURE_API_KEY", None)
        if system_key and token == system_key:
            # Consider fetching a minimal system user or creating a placeholder
            # This avoids returning a full User object if not needed
            # For now, return placeholder as in original code
            return User(
                id=env("SYSTEM_ID", "00000000-0000-0000-0000-000000000000"),
                first_name="System",
                last_name="User",
            )

        # Validate against database keys ONLY if UserManager.auth exists and is intended
        # Assuming UserManager.auth is NOT for API key validation based on context
        # If API key validation against DB is needed, implement here using APIKeyManager methods

        # If not system key and no other validation method passed:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this endpoint.",
        )

    def generate_api_key(  # Added self and closing parenthesis
        self,
        name: str,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
        role_id: Optional[str] = None,
    ) -> str:  # Added return type hint
        """Generate a new API key with a secure random value"""  # Moved docstring
        raw_key = f"v2_{secrets.token_urlsafe(32)}"
        key_hash = self._hash_key(raw_key)

        create_data = APIKeyModel.Create(
            name=name,
            key_hash=key_hash,  # Key hash must be provided on creation
            user_id=user_id,
            team_id=team_id,
            role_id=role_id,
        )

        # Use the manager's create method
        created_entity = super().create(entity=create_data)  # Use super().create

        # Assuming create returns the entity model with the ID
        # self.create( # Original call was incorrect
        #     name=name,
        #     key_hash=key_hash,
        #     user_id=user_id, # Added user_id
        #     team_id=team_id,
        #     role_id=role_id, # Added role_id
        # )
        return raw_key

    def _hash_key(self, raw_key: str) -> str:
        """Hash an API key for secure storage"""  # Moved docstring
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def validate_api_key(self, raw_key: str) -> bool:
        """Validate an API key by comparing hashes"""
        key_hash = self._hash_key(raw_key)
        # Use the manager's search method with appropriate filters
        results = self.search(
            search_model=APIKeyModel.Search(key_hash=key_hash)
        )  # Correct search call
        return len(results) > 0
