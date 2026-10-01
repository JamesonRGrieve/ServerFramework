"""auth_oauth2_server extension.

The OAuth2 authorization server: registers third-party clients and issues
opaque, revocable access/refresh tokens via the authorization-code flow with
PKCE (S256 required for public clients). See BLL_Auth_OAuth2Server for the
entities and EXT_Auth_OAuth2Server for the HTTP verbs.
"""
