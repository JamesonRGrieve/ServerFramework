"""Abstract provider template for object/blob storage (Item 43).

Reference targets: AWS S3, Google Cloud Storage, Azure Blob Storage,
MinIO, Backblaze B2, Cloudflare R2.

This module ships the contract only — concrete implementations live in
separate extensions (a follow-up item). Concrete subclasses declare
their typed configuration via the `Settings` inner Pydantic class
(Item 37) and implement each ability marked `@abstractmethod`.
"""

from abc import abstractmethod
from typing import Any, ClassVar, List, Optional

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticProvider


class AbstractObjectStorageProvider(AbstractStaticProvider):
    """Abstract object/blob storage provider.

    Concrete implementations declare their settings via the typed
    `Settings` inner Pydantic class (Item 37) and override each ability
    method declared as `@abstractmethod`. The framework discovers the
    abilities for the rotation system through the standard
    `AbstractStaticProvider` surface.
    """

    category: ClassVar[str] = "object_storage"

    # ------------------------------------------------------------------
    # Single-shot object operations
    # ------------------------------------------------------------------
    @classmethod
    @abstractmethod
    async def upload(
        cls,
        provider_instance: Any,
        key: str,
        data: bytes,
        content_type: Optional[str] = None,
    ) -> str:
        """Upload `data` under `key`.

        Returns the canonical URL or key the bucket associates with the
        stored object. Concrete providers may translate `content_type`
        into the wire-format `Content-Type` header.
        """
        ...

    @classmethod
    @abstractmethod
    async def download(cls, provider_instance: Any, key: str) -> bytes:
        """Fetch the object stored under `key` as raw bytes."""
        ...

    @classmethod
    @abstractmethod
    async def list(
        cls, provider_instance: Any, prefix: Optional[str] = None
    ) -> List[str]:
        """List keys in the bucket. When `prefix` is given, restrict the
        listing to keys starting with that prefix.
        """
        ...

    @classmethod
    @abstractmethod
    async def delete(cls, provider_instance: Any, key: str) -> None:
        """Delete the object stored under `key`. Idempotent —
        deleting an already-absent key is not an error.
        """
        ...

    @classmethod
    @abstractmethod
    async def presigned_url(
        cls,
        provider_instance: Any,
        key: str,
        expires_in_seconds: int = 3600,
    ) -> str:
        """Return a time-limited URL for `key` that callers can hand to
        end-users for direct upload/download without proxying through
        the application.
        """
        ...

    # ------------------------------------------------------------------
    # Multipart upload — three-step protocol for large objects.
    # ------------------------------------------------------------------
    @classmethod
    @abstractmethod
    async def multipart_upload_init(
        cls, provider_instance: Any, key: str
    ) -> str:
        """Begin a multipart upload for `key`. Returns an `upload_id`
        the caller threads into subsequent `multipart_upload_part` and
        `multipart_upload_complete` calls.
        """
        ...

    @classmethod
    @abstractmethod
    async def multipart_upload_part(
        cls,
        provider_instance: Any,
        upload_id: str,
        part_number: int,
        data: bytes,
    ) -> str:
        """Upload one chunk of a multipart upload. Returns the part's
        `etag` (or provider equivalent) which the caller passes into
        `multipart_upload_complete`.
        """
        ...

    @classmethod
    @abstractmethod
    async def multipart_upload_complete(
        cls,
        provider_instance: Any,
        upload_id: str,
        parts: List[dict],
    ) -> str:
        """Finalize a multipart upload. `parts` is the ordered list of
        `{"part_number": int, "etag": str}` records returned by
        `multipart_upload_part`. Returns the canonical URL or key.
        """
        ...
