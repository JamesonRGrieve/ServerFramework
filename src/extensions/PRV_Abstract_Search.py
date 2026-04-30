"""Abstract provider template for search index (Item 43).

Reference targets: Elasticsearch, OpenSearch, Algolia, Meilisearch,
Typesense, Solr, AWS CloudSearch.

This module ships the contract only — concrete implementations live in
separate extensions (a follow-up item). Concrete subclasses declare
their typed configuration via the `Settings` inner Pydantic class
(Item 37) and implement each ability marked `@abstractmethod`.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Dict, List

from extensions.AbstractExtensionProvider import AbstractStaticProvider


class AbstractSearchProvider(AbstractStaticProvider):
    """Abstract search index provider.

    Concrete implementations declare their settings via the typed
    `Settings` inner Pydantic class (Item 37) and override each ability
    method declared as `@abstractmethod`. The framework discovers the
    abilities for the rotation system through the standard
    `AbstractStaticProvider` surface.
    """

    category: ClassVar[str] = "search"

    @classmethod
    @abstractmethod
    async def index(
        cls,
        provider_instance: Any,
        index_name: str,
        doc_id: str,
        doc: Dict[str, Any],
    ) -> None:
        """Index (create-or-replace) a single document under `doc_id`
        in `index_name`. Subsequent `query()` calls SHOULD return this
        document subject to the provider's refresh-interval semantics.
        """
        ...

    @classmethod
    @abstractmethod
    async def query(
        cls,
        provider_instance: Any,
        index_name: str,
        query: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Run `query` against `index_name`. The shape of `query` is
        provider-dispatched — Elasticsearch DSL for ES, Algolia search
        params for Algolia, etc. Returns a list of result hits, each at
        least `{"id": ..., "score": ..., "source": ...}`.
        """
        ...

    @classmethod
    @abstractmethod
    async def delete(
        cls,
        provider_instance: Any,
        index_name: str,
        doc_id: str,
    ) -> bool:
        """Delete document `doc_id` from `index_name`. Returns True
        when a document was actually removed, False when the document
        was already absent.
        """
        ...

    @classmethod
    @abstractmethod
    async def bulk_index(
        cls,
        provider_instance: Any,
        index_name: str,
        docs: List[Dict[str, Any]],
    ) -> int:
        """Index many documents in one upstream round trip. Each entry
        in `docs` MUST include an `id` key (the rest is the document
        body). Returns the count of documents successfully indexed —
        failures are logged but do not abort the batch.
        """
        ...

    @classmethod
    @abstractmethod
    async def create_schema(
        cls,
        provider_instance: Any,
        index_name: str,
        schema: Dict[str, Any],
    ) -> None:
        """Create or update the mapping/schema for `index_name`. The
        shape of `schema` is provider-dispatched (Elasticsearch
        mappings, Meilisearch settings, Algolia attributesForFaceting,
        etc.). Idempotent — calling with an existing matching schema
        is a no-op.
        """
        ...
