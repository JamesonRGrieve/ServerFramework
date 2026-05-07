# from sqlalchemy import Boolean, Column, DateTime, String, Text
# from sqlalchemy.orm import relationship
# # from database.DB_Providers import Provider
# from database.Base import Base
# from database.DB_Auth import TeamRefMixin.Optional, UserRefMixin.Optional
# from database.DB_Providers import Provider
# from database.Mixins import BaseMixin, ParentMixin, UpdateMixin, create_foreign_key
# Provider.client_id = Column(String, nullable=True)
# Provider.auth_url = Column(String, nullable=True)
# Provider.token_url = Column(String, nullable=True)
# Provider.userinfo_url = Column(String, nullable=True)
# Provider.scope_delimiter = Column(String, default=" ", nullable=True)
# class OAuthExternalScope(Base, BaseMixin, UpdateMixin):
#     """Available scopes for OAuth extensions."""
#     provider = relationship(Provider.__name__, backref="available_scopes")
#     scope_name = Column(String, nullable=False)
#     is_required = Column(Boolean, default=False)
# class UserOAuth(Base, BaseMixin, UpdateMixin, UserRefMixin.Optional):
#     """OAuth accounts linked to users."""
#     __tablename__ = "user_oauths"
#     provider_id = create_foreign_key(Provider)
#     provider = relationship(Provider.__name__)
#     account_email = Column(String, nullable=True)
#     access_token = Column(String, nullable=False)
#     token_expires_at = Column(DateTime, nullable=True)
#     is_primary = Column(Boolean, default=False)
#     scopes = Column(String, nullable=True)  # Space-delimited list of scopes
#     profile_data = Column(Text, nullable=True)  # JSON with profile data
# class OAuth2Client(Base, BaseMixin, UpdateMixin, UserRefMixin.Optional, TeamRefMixin.Optional):
#     """OAuth2 clients for when our app acts as an OAuth provider."""
#     __tablename__ = "oauth_clients"
#     client_id = Column(String, nullable=False, unique=True)
#     client_secret = Column(String, nullable=False)
#     client_name = Column(String, nullable=False)
#     redirect_uris = Column(Text, nullable=False)  # JSON array of URIs
#     allowed_scopes = Column(Text, nullable=False)  # Space-delimited list of scopes
# class OAuth2AuthCode(Base, BaseMixin, UpdateMixin, UserRefMixin.Optional):
#     """OAuth2 authorization codes for our own OAuth server."""
#     client_id = create_foreign_key(OAuth2Client)
#     client = relationship(OAuth2Client.__name__)
#     code = Column(String, nullable=False, unique=True)
#     redirect_uri = Column(String, nullable=False)
#     scopes = Column(String, nullable=False)
#     expires_at = Column(DateTime, nullable=False)
#     is_used = Column(Boolean, default=False)
# class OAuth2Token(Base, BaseMixin, UpdateMixin, UserRefMixin.Optional, ParentMixin):
#     client_id = create_foreign_key(OAuth2Client)
#     client = relationship(OAuth2Client.__name__)
#     token = Column(String, nullable=False, unique=True)
#     scopes = Column(String, nullable=False)
#     expires_at = Column(DateTime, nullable=False)
#     is_revoked = Column(Boolean, default=False)
