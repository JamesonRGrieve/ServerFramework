"""Legacy SQLAlchemy direct-ORM stubbed out during normalization.

The original file maintained labels and a separate join table per labelable
entity (``AgentLabel``, ``PromptLabel``, ``ChainLabel``, ``ProviderLabel``,
``TaskLabel``, ``ProjectLabel``, ``ConversationLabel``). The labelable types
referenced -- ai_agents, prompts, chains, tasks, projects, conversations --
do not exist as extensions in this tree, and ``serverframework.database.DB_Projects``
/ ``DB_Providers`` are also absent.

TODO(audit): rewrite labels using a single polymorphic ``LabelLink`` row
shape (``label_id``, ``target_type``, ``target_id``) against the current
``ApplicationModel`` + ``ModelMeta`` pattern. Drop join tables. Reconcile
overlap with ``metadata/`` (key/value attachments).
"""

from typing import Optional

from pydantic import BaseModel


class Label(BaseModel):
    """Stub for the legacy ``Label`` model. Not persisted."""

    name: str
    description: Optional[str] = None
    color: Optional[str] = None


# Legacy join-table classes are intentionally omitted; rewrite as a
# polymorphic ``LabelLink`` per the module docstring.
