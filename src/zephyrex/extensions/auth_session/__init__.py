"""auth_session extension.

Owns the persisted ``SessionModel`` and ``SessionManager`` that back JWT
revocation, refresh rotation, device-pairing pending-state, and session
listing/admin. The IP-keyed brute-force tracker stays in core
(``lib/InboundSecurity``); JWT issuance/verification stays in core
(``logic/BLL_Auth``). This extension is the durable record of who has
an active token and the orchestration that revokes/expires/lists them.

Without ``auth_session`` loaded, the framework still issues and verifies
JWTs — but tokens are stateless: there is no row to revoke, so a token
remains valid until ``exp``. Deployments that need server-side revocation
or sign-out-all-devices must enable this extension.
"""
