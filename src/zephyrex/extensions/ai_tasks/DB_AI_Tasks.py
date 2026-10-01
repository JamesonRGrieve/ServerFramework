"""
Database layer for AI Tasks.

There is no separate SQLAlchemy model in this module. ``TaskModel`` in
``BLL_AI_Tasks.py`` is defined via the framework's ``ApplicationModel``
(Pydantic model + ``DatabaseMixin``); the SQLAlchemy table is generated
automatically from that model on first access via ``TaskModel.DB(base)``.
This mirrors the pattern used by the sibling ``ai_agents`` and ``ai_chains``
extensions, which likewise carry no standalone ``DB_*.py`` module.
"""
