"""Legacy DB-layer shim for meta_labels.

The original per-entity join tables (AgentLabel, PromptLabel, ChainLabel,
TaskLabel, etc.) have been replaced by the polymorphic
:class:`~zephyrex.extensions.meta_labels.BLL_Meta_Labels.LabelLinkModel`.
This module re-exports the canonical models for backward compatibility.
"""

from zephyrex.extensions.meta_labels.BLL_Meta_Labels import (  # noqa: F401
    LabelLinkManager,
    LabelLinkModel,
    LabelManager,
    LabelModel,
)
