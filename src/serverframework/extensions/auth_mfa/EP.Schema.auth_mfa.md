# MFA Extension API Schema

## Multi-Factor Authentication Domain

### MFA Method Router [JWT]

- Create an MFA Method
    - POST /v1/user/mfa
- Get an MFA Method
    - GET /v1/user/mfa/{id}
- List MFA Methods
    - GET /v1/user/mfa
- Update an MFA Method
    - PUT /v1/user/mfa/{id}
    - PUT /v1/user/mfa
        - Bulk processing of updates.
- Delete an MFA Method
    - DELETE /v1/user/mfa/{id}
    - DELETE /v1/user/mfa
        - Bulk processing of deletes.
- Search MFA Methods
    - POST /v1/user/mfa/search

### MFA Recovery Code Router [JWT]

# Recovery codes are created at method creation time and cannot be recreated or changed.
- List Recovery Codes
    - GET /v1/user/mfa/{id}/recovery

