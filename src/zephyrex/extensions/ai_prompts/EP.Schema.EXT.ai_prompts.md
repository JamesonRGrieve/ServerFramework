# API Endpoint Schema - AI Prompts Extension

## Prompt Management [JWT]

- Create Prompt
  - POST /v1/prompt
- Get Prompt
  - GET /v1/prompt/{id}
- List Prompts
  - GET /v1/prompt
- Search Prompts
  - POST /v1/prompt/search
- Update Prompt
  - PUT /v1/prompt/{id} (Single)
  - PUT /v1/prompt (Bulk)
- Delete Prompt
  - DELETE /v1/prompt/{id} (Single)
  - DELETE /v1/prompt (Bulk)
- Associate Prompt with Label
  - POST /v1/prompt/{prompt_id}/label/{label_id}
- Remove Label from Prompt
  - DELETE /v1/prompt/{prompt_id}/label/{label_id}
- Get Prompt Arguments (Legacy Endpoint)
  - GET /v1/prompt/{id}/arguments

## Prompt Argument Management [JWT]

### Standalone Routes

- Create Prompt Argument
  - POST /v1/prompt-argument
- Get Prompt Argument
  - GET /v1/prompt-argument/{id}
- List Prompt Arguments
  - GET /v1/prompt-argument
- Search Prompt Arguments
  - POST /v1/prompt-argument/search
- Update Prompt Argument
  - PUT /v1/prompt-argument/{id} (Single)
  - PUT /v1/prompt-argument (Bulk)
- Delete Prompt Argument
  - DELETE /v1/prompt-argument/{id} (Single)
  - DELETE /v1/prompt-argument (Bulk)

### Nested Routes (within Prompt)

- Create Prompt Argument (for a specific Prompt)
  - POST /v1/prompt/{prompt_id}/argument
- Get Prompt Argument (for a specific Prompt)
  - GET /v1/prompt/{prompt_id}/argument/{id}
- List Prompt Arguments (for a specific Prompt)
  - GET /v1/prompt/{prompt_id}/argument
- Update Prompt Argument (for a specific Prompt)
  - PUT /v1/prompt/{prompt_id}/argument/{id} (Single)
  - PUT /v1/prompt/{prompt_id}/argument (Bulk)
- Delete Prompt Argument (for a specific Prompt)
  - DELETE /v1/prompt/{prompt_id}/argument/{id} (Single)
  - DELETE /v1/prompt/{prompt_id}/argument (Bulk)
- Search Prompt Arguments (for a specific Prompt)
  - POST /v1/prompt/{prompt_id}/argument/search
