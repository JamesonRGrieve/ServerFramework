# API Endpoint Schema

## Conversations Domain

### Conversation Router [JWT]

- Create a Conversation
    - POST /v1/conversation
    - POST /v1/project_conversation, with `project_id` and `conversation_id` in the body, files a conversation in a project (if optional `ai_agents` extension dependency installed)
- Get a Conversation
    - GET /v1/conversation/{id}
- List Conversations
    - GET /v1/conversation
    - GET /v1/project_conversation?project_id={project_id}, a project's conversation links (if optional `ai_agents` extension dependency installed)
- Search Conversations
    - POST /v1/conversation/search
- Update a Conversation
    - PUT /v1/conversation/{id}
    - PUT /v1/conversation
        - Bulk processing of updates.
    - !PATCH /v1/conversation
        - Automatically renames a conversation based on its content
- Delete a Conversation
    - DELETE /v1/conversation/{id}
    - DELETE /v1/conversation
        - Bulk processing of updates

### Message Router [JWT]

- List Messages
    - GET /v1/conversation/{conversation_id}/message
- Get a Specific Message
    - GET /v1/message/{id}
- Create a Message
    - POST /v1/conversation/{conversation_id}/message
- Update a Message
    - PUT /v1/message/{id}
    - PUT /v1/message
        - Bulk processing of updates.
    - !PATCH /v1/conversation/{conversation_id}/message
        - Forks a message by creating a new message with the same parent with the revised content, then submitting a new completion therefrom.
- Delete a Message
    - DELETE /v1/message/{id}
    - DELETE /v1/message
        - Bulk processing of updates
- Search Messages
    - POST /v1/message/search

### Artifact Router [JWT]

- Create an Artifact
    - POST /v1/artifact
    - POST /v1/project/{project_id}/artifact
    - POST /v1/conversation/{conversation_id}/artifact
- Get an Artifact
    - GET /v1/artifact/{id}
- List Artifacts
    - GET /v1/artifact
    - GET /v1/project/{project_id}/artifact
    - GET /v1/conversation/{conversation_id}/artifact
- Update an Artifact
    - PUT /v1/artifact/{id}
    - PUT /v1/artifact
        - Bulk processing of updates.
- Delete an Artifact
    - DELETE /v1/artifact/{id}
    - DELETE /v1/artifact
        - Bulk processing of deletions.
- Search Artifacts
    - POST /v1/artifact/search

### Feedback Router [JWT]

- Create Feedback
    - POST /v1/conversation/{conversation_id}/message/{message_id}/feedback
- Get Feedback
    - GET /v1/feedback/{id}
- List Feedback
    - GET /v1/conversation/{conversation_id}/message/{message_id}/feedback
- Update Feedback
    - PUT /v1/feedback/{id}
    - PUT /v1/feedback
        - Bulk processing of updates.
- Delete Feedback
    - DELETE /v1/feedback/{id}
    - DELETE /v1/feedback
        - Bulk processing of deletions.
- Search Feedback
    - POST /v1/feedback/search
