"""auth_lockout extension.

Owns the persisted-failed-login table and the per-user attempt count gate.
The IP-keyed `LockoutTracker` primitive stays in core (`lib/InboundSecurity`)
because it is the generic auth-flow defense; this extension is the durable
record of who failed when, and the per-user count threshold consumed by
`UserManager.login`.
"""
