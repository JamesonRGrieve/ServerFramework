# API Endpoint Schema - AI Agents Extension

## Agent Management [JWT]

- Create Agent
  - POST /v1/agent
- Get Agent
  - GET /v1/agent/{id}
- List Agents
  - GET /v1/agent
- Search Agents
  - POST /v1/agent/search
- Update Agent
  - PUT /v1/agent/{id} (Single)
  - PUT /v1/agent (Bulk)
- Delete Agent
  - DELETE /v1/agent/{id} (Single)
  - DELETE /v1/agent (Bulk)
- Prompt Agent
  - POST /v1/agent/{id}/prompt
- Transcribe Audio via Agent
  - POST /v1/agent/{id}/transcribe
- List Agent Abilities
  - GET /v1/agent/{id}/ability

## Agent Prompt Management (Context) [JWT] (Nested under Agent)

- Create Agent Context Prompt
  - POST /v1/agent/{agent_id}/context/prompt
- Get Agent Context Prompt
  - GET /v1/agent/{agent_id}/context/prompt/{id}
- List Agent Context Prompts
  - GET /v1/agent/{agent_id}/context/prompt
- Search Agent Context Prompts
  - POST /v1/agent/{agent_id}/context/prompt/search
- Update Agent Context Prompt
  - PUT /v1/agent/{agent_id}/context/prompt/{id} (Single)
  - PUT /v1/agent/{agent_id}/context/prompt (Bulk)
- Delete Agent Context Prompt
  - DELETE /v1/agent/{agent_id}/context/prompt/{id} (Single)
  - DELETE /v1/agent/{agent_id}/context/prompt (Bulk)

## Provider Instance Agent Management [JWT] (Nested under Provider Instance)

- Create Provider Instance Agent Link
  - POST /v1/provider-instance/{provider_instance_id}/agent
- Get Provider Instance Agent Link
  - GET /v1/provider-instance/{provider_instance_id}/agent/{id}
- List Provider Instance Agent Links
  - GET /v1/provider-instance/{provider_instance_id}/agent
- Search Provider Instance Agent Links
  - POST /v1/provider-instance/{provider_instance_id}/agent/search
- Update Provider Instance Agent Link
  - PUT /v1/provider-instance/{provider_instance_id}/agent/{id} (Single)
  - PUT /v1/provider-instance/{provider_instance_id}/agent (Bulk)
- Delete Provider Instance Agent Link
  - DELETE /v1/provider-instance/{provider_instance_id}/agent/{id} (Single)
  - DELETE /v1/provider-instance/{provider_instance_id}/agent (Bulk)

## Provider Instance Agent Ability Management [JWT] (Nested under Provider Instance Agent)

- Create Provider Instance Agent Ability Override
  - POST /v1/provider-instance/{provider_instance_id}/agent/{agent_id}/ability
- Get Provider Instance Agent Ability Override
  - GET /v1/provider-instance/{provider_instance_id}/agent/{agent_id}/ability/{id}
- List Provider Instance Agent Ability Overrides
  - GET /v1/provider-instance/{provider_instance_id}/agent/{agent_id}/ability
- Search Provider Instance Agent Ability Overrides
  - POST /v1/provider-instance/{provider_instance_id}/agent/{agent_id}/ability/search
- Update Provider Instance Agent Ability Override
  - PUT /v1/provider-instance/{provider_instance_id}/agent/{agent_id}/ability/{id} (Single)
  - PUT /v1/provider-instance/{provider_instance_id}/agent/{agent_id}/ability (Bulk)
- Delete Provider Instance Agent Ability Override
  - DELETE /v1/provider-instance/{provider_instance_id}/agent/{agent_id}/ability/{id} (Single)
  - DELETE /v1/provider-instance/{provider_instance_id}/agent/{agent_id}/ability (Bulk)

## Project Management [JWT]

- Create Project
  - POST /v1/project
- Get Project
  - GET /v1/project/{id}
- List Projects
  - GET /v1/project
- Search Projects
  - POST /v1/project/search
- Update Project
  - PUT /v1/project/{id} (Single)
  - PUT /v1/project (Bulk)
- Delete Project
  - DELETE /v1/project/{id} (Single)
  - DELETE /v1/project (Bulk)

## Project Prompt Management (Context) [JWT] (Nested under Project)

- Create Project Context Prompt
  - POST /v1/project/{project_id}/context/prompt
- Get Project Context Prompt
  - GET /v1/project/{project_id}/context/prompt/{id}
- List Project Context Prompts
  - GET /v1/project/{project_id}/context/prompt
- Search Project Context Prompts
  - POST /v1/project/{project_id}/context/prompt/search
- Update Project Context Prompt
  - PUT /v1/project/{project_id}/context/prompt/{id} (Single)
  - PUT /v1/project/{project_id}/context/prompt (Bulk)
- Delete Project Context Prompt
  - DELETE /v1/project/{project_id}/context/prompt/{id} (Single)
  - DELETE /v1/project/{project_id}/context/prompt (Bulk)

## Project Context Provider Management [JWT] (Nested under Project)

- Create Project Context Provider
  - POST /v1/project/{project_id}/context/provider
- Get Project Context Provider
  - GET /v1/project/{project_id}/context/provider/{id}
- List Project Context Providers
  - GET /v1/project/{project_id}/context/provider
- Search Project Context Providers
  - POST /v1/project/{project_id}/context/provider/search
- Update Project Context Provider
  - PUT /v1/project/{project_id}/context/provider/{id} (Single)
  - PUT /v1/project/{project_id}/context/provider (Bulk)
- Delete Project Context Provider
  - DELETE /v1/project/{project_id}/context/provider/{id} (Single)
  - DELETE /v1/project/{project_id}/context/provider (Bulk)

## Activity Type Management [Root API Key]

- Create an Activity Type
  - POST /v1/activity/type
- Get an Activity Type
  - GET /v1/activity/type/{id}
- List Activity Types
  - GET /v1/activity/type
- Update an Activity Type
  - PUT /v1/activity/type/{id} (Single)
  - PUT /v1/activity/type (Bulk)
- Delete an Activity Type
  - DELETE /v1/activity/type/{id} (Single)
  - DELETE /v1/activity/type (Bulk)
- Search Activity Types
  - POST /v1/activity/type/search

## Activity Management [JWT] (Requires ai_conversations)

- Create an Activity (Nested under Message)
  - POST /v1/message/{message_id}/activity
- List Activities (Nested under Message)
  - GET /v1/message/{message_id}/activity
- Get an Activity (Standalone)
  - GET /v1/activity/{id}
- Update an Activity (Standalone)
  - PUT /v1/activity/{id} (Single)
  - PUT /v1/activity (Bulk)
- Delete an Activity (Standalone)
  - DELETE /v1/activity/{id} (Single)
  - DELETE /v1/activity (Bulk)
- Search Activities (Standalone)
  - POST /v1/activity/search
- Get Activity Hierarchy (Standalone)
  - GET /v1/activity/hierarchy/{message_id} 