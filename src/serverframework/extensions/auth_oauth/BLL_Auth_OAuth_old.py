# from datetime import datetime, timedelta
# from typing import Any, List, Optional
# import requests
# from fastapi import HTTPException
# from pydantic import BaseModel, Field
# from database.DB_Auth import Notification, UserTeam
# from logic.AbstractLogicManager import (
#     AbstractBLLManager,
#     BaseMixinModel,
#     NameMixinModel,
#     StringSearchModel,
#     UpdateMixinModel,
# )
# from extensions.amazon import amazon_sso
# from extensions.github import github_sso
# from extensions.google import google_sso
# from extensions.tesla import tesla_sso
# from extensions.walmart import walmart_sso
# class OAuthExternalScopeModel(BaseMixinModel, UpdateMixinModel):
#     provider_id: str = Field(..., description="ID of the provider")
#     is_required: bool = Field(False, description="Whether this scope is required")
#     provider: Optional["OAuthProviderModel"] = None
#     class ReferenceID:
#         oauth_external_scope_id: str = Field(
#             ..., description="ID of the OAuth external scope"
#         class Optional:
#         class Search:
#             oauth_external_scope_id: Optional[StringSearchModel] = None
#     class Create(BaseModel, NameMixinModel):
#         provider_id: str = Field(..., description="ID of the provider")
#         description: Optional[str] = Field(None, description="Description of the scope")
#         is_required: bool = Field(False, description="Whether this scope is required")
#         provider_id: Optional[str] = None
#         scope_name: Optional[str] = None
#         is_required: Optional[bool] = None
#     class Search(BaseMixinModel.Search, UpdateMixinModel.Search):
#         provider_id: Optional[StringSearchModel] = None
#         scope_name: Optional[StringSearchModel] = None
#         is_required: Optional[bool] = None
#     oauth_external_scope: Optional[OAuthExternalScopeModel] = None
# class OAuthExternalScopeNetworkModel:
#     class POST(BaseModel):
#         oauth_external_scope: OAuthExternalScopeModel.Create
#     class PUT(BaseModel):
#     class SEARCH(BaseModel):
#         oauth_external_scope: OAuthExternalScopeModel.Search
#     class ResponseSingle(BaseModel):
#         oauth_external_scope: OAuthExternalScopeModel
# class OAuthExternalScopeManager(AbstractBLLManager):
#     Model = OAuthExternalScopeModel
#     DBClass = OAuthExternalScope
#     def __init__(
#         self,
#         target_user_id: Optional[str] = None,
#         target_team_id: Optional[str] = None,
#     ):
#         super().__init__(
#             target_user_id=target_user_id,
#             target_team_id=target_team_id,
#         )
#         self._oauth_provider_manager = None
#         if self._oauth_provider_manager is None:
#             self._oauth_provider_manager = OAuthProviderManager(
#                 requester_id=self.requester.id,
#                 target_user_id=self.target_user_id,
#                 target_team_id=self.target_team_id,
#             )
#         return self._oauth_provider_manager
# class OAuthProviderModel(BaseMixinModel, NameMixinModel):
#     client_id: Optional[str] = Field(None, description="OAuth client ID")
#     client_secret: Optional[str] = Field(None, description="OAuth client secret")
#     auth_url: Optional[str] = Field(
#         None, description="Authorization URL for OAuth flow"
#     )
#     token_url: Optional[str] = Field(None, description="Token URL for OAuth flow")
#     userinfo_url: Optional[str] = Field(
#         None, description="User info URL for OAuth flow"
#     )
#     scope_delimiter: Optional[str] = Field(
#         " ", description="Delimiter used for OAuth scopes"
#     available_scopes: Optional[List[OAuthExternalScopeModel]] = None
#     class ReferenceID:
#         provider_id: str = Field(..., description="ID of the OAuth provider")
#         class Optional:
#             provider_id: Optional[str] = None
#         class Search:
#             provider_id: Optional[StringSearchModel] = None
#     class Create(BaseModel, NameMixinModel):
#         client_id: Optional[str] = Field(None, description="OAuth client ID")
#         client_secret: Optional[str] = Field(None, description="OAuth client secret")
#         )
#         token_url: Optional[str] = Field(None, description="Token URL for OAuth flow")
#         userinfo_url: Optional[str] = Field(
#             None, description="User info URL for OAuth flow"
#         )
#         scope_delimiter: Optional[str] = Field(
#             " ", description="Delimiter used for OAuth scopes"
#         )
#     class Update(BaseModel, NameMixinModel.Optional):
#         client_id: Optional[str] = Field(None, description="OAuth client ID")
#         client_secret: Optional[str] = Field(None, description="OAuth client secret")
#         auth_url: Optional[str] = Field(
#             None, description="Authorization URL for OAuth flow"
#         token_url: Optional[str] = Field(None, description="Token URL for OAuth flow")
#             None, description="User info URL for OAuth flow"
#         )
#             None, description="Delimiter used for OAuth scopes"
#         )
#         client_id: Optional[StringSearchModel] = Field(
#             None, description="Search by client ID"
#         auth_url: Optional[StringSearchModel] = Field(
#             None, description="Search by auth URL"
#         )
#         token_url: Optional[StringSearchModel] = Field(
#             None, description="Search by token URL"
#         )
#         userinfo_url: Optional[StringSearchModel] = Field(
#             None, description="Search by userinfo URL"
#         )
# class OAuthProviderReferenceModel(OAuthProviderModel.ReferenceID.Optional):
#     provider: Optional[OAuthProviderModel] = None
# class OAuthProviderNetworkModel:
#     class POST(BaseModel):
#     class PUT(BaseModel):
#         oauth_provider: OAuthProviderModel.Update
#     class SEARCH(BaseModel):
#         oauth_provider: OAuthProviderModel.Search
#     class ResponseSingle(BaseModel):
#         oauth_provider: OAuthProviderModel
#     class ResponsePlural(BaseModel):
#         oauth_providers: List[OAuthProviderModel]
# class OAuthProviderManager(AbstractBLLManager):
#     Model = OAuthProviderModel
#     ReferenceModel = OAuthProviderReferenceModel
#     NetworkModel = OAuthProviderNetworkModel
#     DBClass = Provider
#         self,
#         requester_id: str,
#         target_user_id: Optional[str] = None,
#         target_team_id: Optional[str] = None,
#         db: Optional[Session] = None,
#     ):
#         super().__init__(
#             requester_id=requester_id,
#             target_user_id=target_user_id,
#             target_team_id=target_team_id,
#             db=db,
#         )
#         self._oauth_external_scopes = None
#         if self._oauth_external_scopes is None:
#             self._oauth_external_scopes = OAuthExternalScopeManager(
#                 target_team_id=self.target_team_id,
#                 db=self.db,
#             )
# class UserOAuthModel(BaseMixinModel, UpdateMixinModel):
#     user_id: str = Field(..., description="ID of the user")
#     provider_user_id: str = Field(..., description="User ID from the provider")
#     account_name: str = Field(..., description="User's account name from the provider")
#         None, description="User's email from the provider"
#     )
#     refresh_token: Optional[str] = Field(None, description="OAuth refresh token")
#     token_expires_at: Optional[datetime] = Field(
#     is_primary: bool = Field(
#         False, description="Whether this is the user's primary OAuth connection"
#     )
#     scopes: Optional[str] = Field(None, description="OAuth scopes granted")
#     profile_data: Optional[str] = Field(
#     )
#     provider: Optional[OAuthProviderModel] = None
#     class ReferenceID:
#         user_oauth_id: str = Field(..., description="ID of the user OAuth connection")
#         class Optional:
#             user_oauth_id: Optional[str] = None
#         class Search:
#             user_oauth_id: Optional[StringSearchModel] = None
#     class Create(BaseModel, OAuthProviderModel.ReferenceID):
#         user_id: str = Field(..., description="ID of the user")
#         provider_user_id: str = Field(..., description="User ID from the provider")
#         account_name: str = Field(
#             ..., description="User's account name from the provider"
#         )
#             None, description="User's email from the provider"
#         )
#         access_token: str = Field(..., description="OAuth access token")
#         refresh_token: Optional[str] = Field(None, description="OAuth refresh token")
#         token_expires_at: Optional[datetime] = Field(
#             None, description="Expiration time of the access token"
#         )
#         is_primary: bool = Field(
#             False, description="Whether this is the user's primary OAuth connection"
#         )
#             None, description="Additional profile data from the provider"
#         )
#     class Update(BaseModel, OAuthProviderModel.ReferenceID.Optional):
#         user_id: Optional[str] = None
#         provider_user_id: Optional[str] = None
#         account_name: Optional[str] = None
#         account_email: Optional[str] = None
#         access_token: Optional[str] = None
#         refresh_token: Optional[str] = None
#         token_expires_at: Optional[datetime] = None
#         is_primary: Optional[bool] = None
#         scopes: Optional[str] = None
#         profile_data: Optional[str] = None
#     class Search(
#         BaseMixinModel.Search,
#         UpdateMixinModel.Search,
#         OAuthProviderModel.ReferenceID.Optional,
#     ):
#         user_id: Optional[StringSearchModel] = None
#         provider_user_id: Optional[StringSearchModel] = None
#         account_email: Optional[StringSearchModel] = None
# class UserOAuthReferenceModel(UserOAuthModel.ReferenceID.Optional):
#     user_oauth: Optional[UserOAuthModel] = None
#     class POST(BaseModel):
#         user_oauth: UserOAuthModel.Create
#         user_oauth: UserOAuthModel.Update
#     class SEARCH(BaseModel):
#     class ResponseSingle(BaseModel):
#         user_oauth: UserOAuthModel
#     class ResponsePlural(BaseModel):
#         user_oauths: List[UserOAuthModel]
# class OAuth2ClientModel(BaseMixinModel, UpdateMixinModel, NameMixinModel):
#     user_id: Optional[str] = Field(
#         None, description="ID of the user who owns this client"
#     )
#     team_id: Optional[str] = Field(
#         None, description="ID of the team this client belongs to"
#     )
#     client_id: str = Field(..., description="OAuth 2.0 client ID")
#     client_secret: str = Field(..., description="OAuth 2.0 client secret")
#     redirect_uris: str = Field(..., description="JSON array of redirect URIs")
#     allowed_scopes: str = Field(
#         ..., description="Space-delimited list of allowed scopes"
#     )
#     is_confidential: bool = Field(
#         True, description="Whether this is a confidential client"
#     )
#     is_enabled: bool = Field(True, description="Whether this client is enabled")
#         oauth2_client_id: str = Field(..., description="ID of the OAuth 2.0 client")
#         class Optional:
#             oauth2_client_id: Optional[str] = None
#         class Search:
#             oauth2_client_id: Optional[StringSearchModel] = None
#     class Create(BaseModel, NameMixinModel):
#         user_id: Optional[str] = Field(
#             None, description="ID of the user who owns this client"
#         )
#         team_id: Optional[str] = Field(
#             None, description="ID of the team this client belongs to"
#         client_id: str = Field(..., description="OAuth 2.0 client ID")
#         client_secret: str = Field(..., description="OAuth 2.0 client secret")
#         redirect_uris: str = Field(..., description="JSON array of redirect URIs")
#         allowed_scopes: str = Field(
#             ..., description="Space-delimited list of allowed scopes"
#         )
#         is_confidential: bool = Field(
#             True, description="Whether this is a confidential client"
#         )
#         is_enabled: bool = Field(True, description="Whether this client is enabled")
#         team_id: Optional[str] = None
#         client_id: Optional[str] = None
#         allowed_scopes: Optional[str] = None
#         is_confidential: Optional[bool] = None
#         is_enabled: Optional[bool] = None
#         user_id: Optional[StringSearchModel] = None
#         team_id: Optional[StringSearchModel] = None
#         is_confidential: Optional[bool] = None
#         is_enabled: Optional[bool] = None
#     oauth2_client: Optional[OAuth2ClientModel] = None
# class OAuth2ClientNetworkModel:
#         oauth2_client: OAuth2ClientModel.Create
#     class PUT(BaseModel):
#         oauth2_client: OAuth2ClientModel.Search
#     class ResponseSingle(BaseModel):
#         oauth2_client: OAuth2ClientModel
#     class ResponsePlural(BaseModel):
#         oauth2_clients: List[OAuth2ClientModel]
# class OAuth2ClientManager(AbstractBLLManager):
#     Model = OAuth2ClientModel
#     ReferenceModel = OAuth2ClientReferenceModel
#     NetworkModel = OAuth2ClientNetworkModel
#     DBClass = OAuth2Client
#     def __init__(
#         self,
#         requester_id: str,
#         target_user_id: Optional[str] = None,
#         target_team_id: Optional[str] = None,
#         db: Optional[Session] = None,
#     ):
#             requester_id=requester_id,
#             target_user_id=target_user_id,
#             db=db,
#         )
#     user_id: str = Field(..., description="ID of the user")
#     client_id: str = Field(..., description="ID of the OAuth 2.0 client")
#     redirect_uri: str = Field(..., description="Redirect URI")
#     scopes: str = Field(..., description="Authorized scopes")
#     expires_at: datetime = Field(..., description="Expiration time")
#     is_used: bool = Field(False, description="Whether this code has been used")
#     client: Optional[OAuth2ClientModel] = None
#     class ReferenceID:
#         oauth2_auth_code_id: str = Field(
#             ..., description="ID of the OAuth 2.0 auth code"
#         )
#         class Optional:
#             oauth2_auth_code_id: Optional[str] = None
#         class Search:
#             oauth2_auth_code_id: Optional[StringSearchModel] = None
#     class Create(BaseModel, OAuth2ClientModel.ReferenceID):
#         user_id: str = Field(..., description="ID of the user")
#         code: str = Field(..., description="Authorization code")
#         redirect_uri: str = Field(..., description="Redirect URI")
#         expires_at: datetime = Field(..., description="Expiration time")
#         is_used: bool = Field(False, description="Whether this code has been used")
#     class Update(BaseModel, OAuth2ClientModel.ReferenceID.Optional):
#         user_id: Optional[str] = None
#         code: Optional[str] = None
#         redirect_uri: Optional[str] = None
#         scopes: Optional[str] = None
#         expires_at: Optional[datetime] = None
#         is_used: Optional[bool] = None
#         user_id: Optional[StringSearchModel] = None
#         client_id: Optional[StringSearchModel] = None
#         code: Optional[StringSearchModel] = None
#         is_used: Optional[bool] = None
# class OAuth2AuthCodeReferenceModel(OAuth2AuthCodeModel.ReferenceID.Optional):
#     oauth2_auth_code: Optional[OAuth2AuthCodeModel] = None
#         oauth2_auth_code: OAuth2AuthCodeModel.Create
#     class PUT(BaseModel):
#         oauth2_auth_code: OAuth2AuthCodeModel.Search
#     class ResponseSingle(BaseModel):
#         oauth2_auth_code: OAuth2AuthCodeModel
#         oauth2_auth_codes: List[OAuth2AuthCodeModel]
# class OAuth2AuthCodeManager(AbstractBLLManager):
#     Model = OAuth2AuthCodeModel
#     ReferenceModel = OAuth2AuthCodeReferenceModel
#     NetworkModel = OAuth2AuthCodeNetworkModel
#     DBClass = OAuth2AuthCode

#     def __init__(
#         self,
#         requester_id: str,
#         target_user_id: Optional[str] = None,
#         target_team_id: Optional[str] = None,
#         db: Optional[Session] = None,
#     ):
#         super().__init__(
#             requester_id=requester_id,
#             target_user_id=target_user_id,
#             target_team_id=target_team_id,
#             db=db,
#         )
#         self._oauth2_clients = None

#     @property
#     def oauth2_clients(self):
#         if self._oauth2_clients is None:
#             self._oauth2_clients = OAuth2ClientManager(
#                 requester_id=self.requester.id,
#                 target_user_id=self.target_user_id,
#                 target_team_id=self.target_team_id,
#                 db=self.db,
#             )
#         return self._oauth2_clients
# class OAuth2TokenModel(BaseMixinModel, UpdateMixinModel, ParentMixinModel):
#     user_id: str = Field(..., description="ID of the user")
#     token: str = Field(..., description="Token value")
#     scopes: str = Field(..., description="Authorized scopes")
#     expires_at: datetime = Field(..., description="Expiration time")
#     is_revoked: bool = Field(False, description="Whether this token has been revoked")
#     client: Optional[OAuth2ClientModel] = None
#     class ReferenceID:
#         oauth2_token_id: str = Field(..., description="ID of the OAuth 2.0 token")
#         class Optional:
#         class Search:
#     class Create(BaseModel, OAuth2ClientModel.ReferenceID, ParentMixinModel.Optional):
#         user_id: str = Field(..., description="ID of the user")
#         token_type: str = Field(..., description="Type of token (access or refresh)")
#         token: str = Field(..., description="Token value")
#         expires_at: datetime = Field(..., description="Expiration time")
#         is_revoked: bool = Field(
#         )
#     class Update(
#         OAuth2ClientModel.ReferenceID.Optional,
#         ParentMixinModel.Optional,
#     ):
#         user_id: Optional[str] = None
#         token_type: Optional[str] = None
#         token: Optional[str] = None
#         scopes: Optional[str] = None
#         is_revoked: Optional[bool] = None
#     class Search(
#         BaseMixinModel.Search, UpdateMixinModel.Search, ParentMixinModel.Search
#     ):
#         user_id: Optional[StringSearchModel] = None
#         client_id: Optional[StringSearchModel] = None
#         token_type: Optional[StringSearchModel] = None
#         is_revoked: Optional[bool] = None
# class OAuth2TokenReferenceModel(OAuth2TokenModel.ReferenceID.Optional):
#     oauth2_token: Optional[OAuth2TokenModel] = None
# class OAuth2TokenNetworkModel:
#     class POST(BaseModel):
#         oauth2_token: OAuth2TokenModel.Update
#     class SEARCH(BaseModel):
#         oauth2_token: OAuth2TokenModel
#     class ResponsePlural(BaseModel):
#         oauth2_tokens: List[OAuth2TokenModel]
#     Model = OAuth2TokenModel
#     ReferenceModel = OAuth2TokenReferenceModel
#     DBClass = OAuth2Token
#     def __init__(
#         requester_id: str,
#         target_user_id: Optional[str] = None,
#         db: Optional[Session] = None,
#     ):
#             target_user_id=target_user_id,
#             target_team_id=target_team_id,
#             db=db,
#         )
#         self._oauth2_clients = None
#     def oauth2_clients(self):
#         if self._oauth2_clients is None:
#             self._oauth2_clients = OAuth2ClientManager(
#                 requester_id=self.requester.id,
#                 target_user_id=self.target_user_id,
#                 target_team_id=self.target_team_id,
#                 db=self.db,
#             )
#         return self._oauth2_clients
# class UserOAuthManager(AbstractBLLManager):
#     Model = UserOAuthModel
#     ReferenceModel = UserOAuthReferenceModel
#     NetworkModel = UserOAuthNetworkModel
#     DBClass = UserOAuth
#         self,
#         requester_id: str,
#         target_user_id: Optional[str] = None,
#         target_team_id: Optional[str] = None,
#         db: Optional[Session] = None,
#     ):
#         super().__init__(
#             requester_id=requester_id,
#             target_user_id=target_user_id,
#             target_team_id=target_team_id,
#         self.app_uri = env("APP_URI")
#         self._providers = None
#     @property
#     def providers(self):
#         if self._providers is None:
#             self._providers = OAuthProviderManager(
#                 requester_id=self.requester.id,
#                 target_user_id=self.target_user_id,
#                 db=self.db,
#         return self._providers
#     def connect_oauth(
#         provider_name: str,
#         code: str,
#         invitation_id: Optional[str] = None,
#     ) -> Dict[str, Any]:
#         redirect_uri = redirect_uri or self.app_uri
#         user_data = self._get_oauth_user_data(provider_name, code, redirect_uri)
#         if not user_data:
#             raise HTTPException(
#                 status_code=400, detail=f"Failed to get user data from {provider_name}"
#             )
#         email, account_name, access_token, refresh_token, expires_in = (
#             self._extract_oauth_user_info(provider_name, user_data)
#         )
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"Could not retrieve email from {provider_name} account",
#             )
#         provider = self._get_or_create_provider(provider_name)
#         if self.requester.id != env("SYSTEM_ID"):
#             return self._handle_authenticated_user_oauth(
#                 provider,
#                 provider_name,
#                 account_name,
#                 email,
#                 refresh_token,
#                 expires_in,
#             )
#         else:
#             return self._handle_unauthenticated_user_oauth(
#                 provider,
#                 provider_name,
#                 account_name,
#                 refresh_token,
#                 expires_in,
#             )
#     def _get_or_create_provider(self, provider_name: str):
#         providers = self.extensions.search(name=provider_name)
#             return providers[0]
#         return self.extensions.create(name=provider_name)
#         self,
#         provider,
#         account_name: str,
#         email: str,
#         refresh_token: Optional[str],
#         expires_in: Optional[int],
#         existing_oauth = self.search(provider_id=provider.id, account_name=account_name)
#         for oauth in existing_oauth:
#             if oauth.user_id != user_id:
#                 raise HTTPException(
#                     status_code=409,
#                 )
#         user_oauth = self.search(user_id=user_id, provider_id=provider.id)
#         if user_oauth:
#             oauth_data = {
#                 "access_token": access_token,
#                 "refresh_token": refresh_token,
#             }
#             if expires_in:
#                 oauth_data["token_expires_at"] = datetime.now() + timedelta(
#                     seconds=expires_in
#                 )
#             self.update(id=user_oauth[0].id, **oauth_data)
#             return {
#                 "message": f"{provider_name} account updated successfully",
#                 "is_new_user": False,
#             }
#         else:
#             oauth_data = {
#                 "user_id": user_id,
#                 "provider_id": provider.id,
#                 "provider_user_id": str(account_name),
#                 "account_name": account_name,
#                 "account_email": email,
#                 "access_token": access_token,
#             if expires_in:
#                 oauth_data["token_expires_at"] = datetime.now() + timedelta(
#                     seconds=expires_in
#                 )
#             self.create(**oauth_data)
#                 user_id=user_id,
#                 title="OAuth Account Connected",
#                 content=f"Your {provider_name} account has been connected successfully.",
#             )
#             return {
#                 "message": f"{provider_name} account connected successfully",
#                 "email": self.requester.email,
#                 "is_new_user": False,
#             }
#     def _handle_unauthenticated_user_oauth(
#         self,
#         provider,
#         provider_name: str,
#         account_name: str,
#         email: str,
#         refresh_token: Optional[str],
#         expires_in: Optional[int],
#         user_data: Any,
#         invitation_id: Optional[str],
#     ) -> Dict[str, Any]:
#         normalized_email = email.lower().strip()
#         existing_users = User.list(
#             requester_id=getenv("SYSTEM_ID"), db=self.db, email=normalized_email
#         )
#         if existing_users:
#             return self._handle_existing_user_login(
#                 existing_user,
#                 provider,
#                 provider_name,
#                 account_name,
#                 email,
#                 access_token,
#                 refresh_token,
#                 expires_in,
#         else:
#                 provider_name, user_data
#             )
#             return self._create_new_user_with_oauth(
#                 email,
#                 last_name,
#                 provider,
#                 provider_name,
#                 access_token,
#                 refresh_token,
#                 expires_in,
#                 invitation_id,
#             )
#         self,
#         provider,
#         provider_name: str,
#         account_name: str,
#         email: str,
#         access_token: str,
#         refresh_token: Optional[str],
#         expires_in: Optional[int],
#     ) -> Dict[str, Any]:
#         existing_oauth = self.search(user_id=existing_user.id, provider_id=provider.id)
#         if not existing_oauth:
#             oauth_data = {
#                 "user_id": existing_user.id,
#                 "provider_id": provider.id,
#                 "provider_user_id": str(account_name),
#                 "account_name": account_name,
#                 "account_email": email,
#                 "access_token": access_token,
#                 "refresh_token": refresh_token,
#             }
#             if expires_in:
#                 oauth_data["token_expires_at"] = datetime.now() + timedelta(
#                     seconds=expires_in
#             self.create(**oauth_data)
#             self._create_user_notification(
#                 title="OAuth Account Connected",
#                 content=f"Your {provider_name} account has been connected successfully.",
#         else:
#                 "access_token": access_token,
#                 "refresh_token": refresh_token,
#             }
#             if expires_in:
#                 oauth_data["token_expires_at"] = datetime.now() + timedelta(
#                     seconds=expires_in
#                 )
#             self.update(id=existing_oauth[0].id, **oauth_data)
#         from logic.BLL_Auth import UserManager
#         user_manager = UserManager(requester_id=getenv("SYSTEM_ID"), db=self.db)
#         token = user_manager.generate_jwt_token(
#         )
#             "message": f"Logged in with {provider_name} account",
#             "token": token,
#             "email": existing_user.email,
#             "is_new_user": False,
#         }
#     def _create_new_user_with_oauth(
#         email: str,
#         last_name: str,
#         provider,
#         provider_name: str,
#         account_name: str,
#         access_token: str,
#         expires_in: Optional[int],
#         invitation_id: Optional[str],
#     ) -> Dict[str, Any]:
#         from logic.BLL_Auth import UserManager
#         new_user = user_manager.create(
#             first_name=first_name,
#             last_name=last_name,
#             display_name=f"{first_name} {last_name}".strip() or email,
#         )
#         totp_secret = pyotp.random_base32()
#         from extensions.mfa.BLL_MFA import UserMFAMethodManager
#         mfa_manager = UserMFAMethodManager(
#             requester_id=getenv("SYSTEM_ID"), target_user_id=new_user.id, db=self.db
#         )
#         mfa_manager.create(
#             method_type=MFAMethodType.TOTP.value,
#             totp_secret=totp_secret,
#             is_primary=True,
#             verification=True,
#         )
#             "user_id": new_user.id,
#             "provider_id": provider.id,
#             "provider_user_id": str(account_name),
#             "account_name": account_name,
#             "access_token": access_token,
#             "is_primary": True,
#         }
#         if expires_in:
#             oauth_data["token_expires_at"] = datetime.now() + timedelta(
#                 seconds=expires_in
#         self.create(**oauth_data)
#         team_id = None
#         if invitation_id:
#             team_id = self._process_invitation_for_new_user(
#                 invitation_id, new_user.id, email
#         if not team_id:
#             from logic.BLL_Auth import TeamManager
#             team_manager = TeamManager(
#                 requester_id=getenv("SYSTEM_ID"), target_user_id=new_user.id, db=self.db
#             )
#             team_name = f"{first_name}'s Team" if first_name else "My Team"
#             new_team = team_manager.create(name=team_name)
#             team_id = new_team.id
#             from logic.BLL_Auth import UserTeamManager
#             user_team_manager = UserTeamManager(
#                 requester_id=getenv("SYSTEM_ID"), db=self.db
#             )
#             user_team_manager.create(
#                 team_id=team_id,
#                 role_name="admin",
#             )
#             user_id=new_user.id,
#             title="Welcome to the platform",
#             content=f"Welcome {new_user.display_name or email}! Your account has been created successfully with {provider_name}.",
#         )
#         from logic.BLL_Auth import UserManager
#         user_manager = UserManager(requester_id=getenv("SYSTEM_ID"), db=self.db)
#         token = user_manager.generate_jwt_token(
#             user_id=str(new_user.id), email=new_user.email
#         )
#         return {
#             "message": f"Account created with {provider_name}",
#             "token": token,
#             "email": new_user.email,
#             "is_new_user": True,
#         }
#     def _process_invitation_for_new_user(
#     ) -> Optional[str]:
#         from database.DB_Auth import Invitation, InvitationInvitee
#         from logic.BLL_Auth import InvitationManager
#         invitation_manager = InvitationManager(
#             requester_id=getenv("SYSTEM_ID"), db=self.db
#         )
#         try:
#             invitation = invitation_manager.get(id=invitation_id)
#         except HTTPException:
#             return None
#         invitees = InvitationInvitee.list(
#             requester_id=getenv("SYSTEM_ID"),
#             invitation_id=invitation.id,
#             email=email,
#         )
#         if not invitees:
#             return None
#         invitation_manager.accept_invitation(
#             invitation_id=invitation.id, invitee_email=email, user_id=user_id
#         )
#         team = Team.get(
#             requester_id=getenv("SYSTEM_ID"), db=self.db, id=invitation.team_id
#         )
#         if team:
#                 user_id=user_id,
#                 title="Team Joined",
#                 content=f"You have joined team '{team.name}' via invitation.",
#             )
#             notification = Notification.create(
#                 requester_id=getenv("SYSTEM_ID"),
#                 db=self.db,
#                 title="New Team Member",
#                 content=f"A new user has joined the team via invitation.",
#                 team_id=invitation.team_id,
#             team_admins = UserTeam.list(
#                 requester_id=getenv("SYSTEM_ID"),
#                 db=self.db,
#                 team_id=invitation.team_id,
#             )
#                 if admin.user_id != user_id:
#                     UserNotification.create(
#                         requester_id=getenv("SYSTEM_ID"),
#                         db=self.db,
#                         user_id=admin.user_id,
#                         notification_id=notification.id,
#                         read=False,
#                     )
#         return invitation.team_id
#     def _create_user_notification(self, user_id: str, title: str, content: str) -> None:
#             requester_id=getenv("SYSTEM_ID"),
#             db=self.db,
#             title=title,
#             content=content,
#         )
#             requester_id=getenv("SYSTEM_ID"),
#             user_id=user_id,
#             notification_id=notification.id,
#             read=False,
#         )
#         self, provider: str, code: str, redirect_uri: str
#     ) -> Optional[Dict[str, Any]]:
#         try:
#             if provider == "google":
#                 return google_sso(code=code, redirect_uri=redirect_uri)
#             elif provider == "microsoft":
#             elif provider == "github":
#                 return github_sso(code=code, redirect_uri=redirect_uri)
#             elif provider == "amazon":
#                 return amazon_sso(code=code, redirect_uri=redirect_uri)
#             elif provider == "walmart":
#                 return walmart_sso(code=code, redirect_uri=redirect_uri)
#             elif provider == "tesla":
#                 return tesla_sso(code=code, redirect_uri=redirect_uri)
#             else:
#                 raise HTTPException(
#                     status_code=400, detail=f"Unsupported OAuth provider: {provider}"
#                 )
#         except Exception as e:
#             raise HTTPException(
#             )
#     def _extract_oauth_user_info(self, provider: str, data: Any) -> tuple:
#         email = None
#         account_name = None
#         access_token = None
#         refresh_token = None
#         expires_in = None
#             access_token = data.access_token
#             expires_in = getattr(data, "expires_in", None)
#             if provider == "microsoft":
#                 email = (
#                     user_info.get("mail")
#                     or user_info.get("email")
#                 )
#                 account_name = email
#             elif provider == "google":
#                 email = user_info.get("email")
#                 account_name = email
#                 email = user_info.get("email")
#                 account_name = user_info.get("login")
#                 if not email and access_token:
#                     try:
#                         response = requests.get(
#                             "https://api.github.com/user/emails",
#                             headers={"Authorization": f"token {access_token}"},
#                             timeout=10,
#                         )
#                         response.raise_for_status()
#                         primary_email = next(
#                             (e for e in emails if e.get("primary")), None
#                         )
#                         if primary_email:
#                     except Exception as e:
#                             status_code=400,
#                             detail=f"Error fetching GitHub email: {str(e)}",
#                         )
#             elif provider == "amazon":
#                 email = user_info.get("email")
#             elif provider == "walmart":
#                 email = user_info.get("email")
#             elif provider == "tesla":
#                 email = user_info.get("email")
#                 account_name = email
#         except Exception as e:
#             raise HTTPException(
#                 status_code=400, detail=f"Error extracting OAuth user info: {str(e)}"
#     def _extract_name_from_oauth(self, provider: str, data: Any) -> tuple:
#         last_name = ""
#         try:
#             user_info = data.user_info
#                 first_name = user_info.get("givenName", "")
#                 last_name = user_info.get("surname", "")
#             elif provider == "google":
#                 first_name = user_info.get("given_name", "")
#                 last_name = user_info.get("family_name", "")
#                 full_name = user_info.get("name", "")
#                 if full_name:
#                     parts = full_name.split(" ", 1)
#                     first_name = parts[0]
#                     last_name = parts[1] if len(parts) > 1 else ""
#                 first_name = user_info.get("given_name", "")
#             elif provider == "walmart":
#                 first_name = user_info.get("firstName", "")
#                 last_name = user_info.get("lastName", "")
#             elif provider == "tesla":
#                 if full_name:
#                     parts = full_name.split(" ", 1)
#                     first_name = parts[0]
#                     last_name = parts[1] if len(parts) > 1 else ""
#         except Exception:
#             pass
#     def disconnect_oauth(self, provider_name: str) -> Dict[str, str]:
#         providers = self.extensions.search(name=provider_name.lower())
#         if not providers:
#             raise HTTPException(
#                 status_code=404, detail=f"Provider '{provider_name}' not found"
#         provider = providers[0]
#         oauth_connections = self.search(
#             user_id=self.target_user_id, provider_id=provider.id
#         if not oauth_connections:
#             raise HTTPException(
#                 status_code=404, detail=f"No connection found to {provider_name}"
#             )
#         user_mfa_method_manager = UserMFAMethodManager(
#             requester_id=self.requester.id,
#             target_user_id=self.target_user_id,
#             db=self.db,
#         )
#         mfa_methods = user_mfa_method_manager.list(is_enabled=True)
#         if len(mfa_methods) == 0 and len(all_oauth_connections) <= 1:
#             raise HTTPException(
#                 detail="Cannot disconnect the only authentication method",
#             )
#         self.delete(id=oauth_connections[0].id)
#             user_id=self.target_user_id,
#             title="OAuth Account Disconnected",
#             content=f"Your {provider_name} account has been disconnected.",
#         return {"message": f"Disconnected from {provider_name}"}
#     def refresh_oauth_token(self, provider_name: str) -> Dict[str, str]:
#         providers = self.extensions.search(name=provider_name.lower())
#         if not providers:
#             raise HTTPException(
#                 status_code=404, detail=f"Provider '{provider_name}' not found"
#         provider = providers[0]
#         oauth_connections = self.search(
#             user_id=self.target_user_id, provider_id=provider.id
#         )
#         if not oauth_connections:
#             raise HTTPException(
#                 status_code=404, detail=f"No connection found to {provider_name}"
#         oauth = oauth_connections[0]
#         if not oauth.refresh_token:
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"No refresh token available for {provider_name}",
#             )
#             oauth.token_expires_at
#             and oauth.token_expires_at > datetime.now() + timedelta(minutes=5)
#         ):
#             return {
#                 "message": "Token still valid",
#                 "access_token": oauth.access_token,
#                 "expires_at": oauth.token_expires_at.isoformat(),
#             }
#         try:
#             if provider_name == "google":
#                 google = GoogleProvider(refresh_token=oauth.refresh_token)
#                 new_tokens = google.get_new_token()
#             elif provider_name == "microsoft":
#                 from extensions.microsoft import MicrosoftProvider
#                 microsoft = MicrosoftProvider(refresh_token=oauth.refresh_token)
#                 new_tokens = microsoft.get_new_token()
#             else:
#                 raise HTTPException(
#                     detail=f"Token refresh not implemented for {provider_name}",
#                 )
#             update_data = {"access_token": new_tokens["access_token"]}
#             if "refresh_token" in new_tokens:
#                 update_data["refresh_token"] = new_tokens["refresh_token"]
#             if "expires_in" in new_tokens:
#                 update_data["token_expires_at"] = datetime.now() + timedelta(
#                 )
#             updated_oauth = self.update(id=oauth.id, **update_data)
#             return {
#                 "message": "Token refreshed successfully",
#                 "access_token": updated_oauth.access_token,
#                 "expires_at": (
#                     updated_oauth.token_expires_at.isoformat()
#                     if updated_oauth.token_expires_at
#                     else None
#                 ),
#             }
#         except Exception as e:
#             raise HTTPException(
#                 status_code=400, detail=f"Failed to refresh token: {str(e)}"
#             )
