"""Abstract provider template for cache (Item 43).

Reference targets: Redis, Memcached, KeyDB, Dragonfly, in-process LRU.

This module ships the contract only — concrete implementations live in
separate extensions (a follow-up item). Concrete subclasses declare
their typed configuration via the `Settings` inner Pydantic class
(Item 37) and implement each ability marked `@abstractmethod`.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Optional

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticProvider


class AbstractCacheProvider(AbstractStaticProvider):
    """Abstract cache provider.

    Concrete implementations declare their settings via the typed
    `Settings` inner Pydantic class (Item 37) and override each ability
    method declared as `@abstractmethod`. The framework discovers the
    abilities for the rotation system through the standard
    `AbstractStaticProvider` surface.
    """

    category: ClassVar[str] = "cache"

    @classmethod
    @abstractmethod
    async def get(cls, provider_instance: Any, key: str) -> Optional[bytes]:
        """Fetch the value stored at `key`, or `None` when absent or
        expired. Concrete providers MUST distinguish "missing key" from
        "stored empty value" by returning `None` for the former.
        """
        ...

    @classmethod
    @abstractmethod
    async def set(
        cls,
        provider_instance: Any,
        key: str,
        value: bytes,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Store `value` under `key`. When `ttl_seconds` is given, the
        entry expires after that many seconds; otherwise it persists
        until explicitly deleted (or evicted by the backend's policy).
        """
        ...

    @classmethod
    @abstractmethod
    async def delete(cls, provider_instance: Any, key: str) -> bool:
        """Remove `key`. Returns True when an entry was actually
        deleted, False when the key was absent.
        """
        ...

    @classmethod
    @abstractmethod
    async def incr(
        cls, provider_instance: Any, key: str, amount: int = 1
    ) -> int:
        """Atomically increment the integer stored at `key` by
        `amount` (which may be negative). Creates the key at `amount`
        when absent. Returns the post-increment value.
        """
        ...

    @classmethod
    @abstractmethod
    async def setnx(
        cls,
        provider_instance: Any,
        key: str,
        value: bytes,
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        """Atomic "set if not exists". Returns True when the value was
        stored, False when the key already existed. The basic primitive
        for distributed locks and one-shot deduplication.
        """
        ...

    @classmethod
    @abstractmethod
    async def mget(
        cls, provider_instance: Any, keys: List[str]
    ) -> Dict[str, bytes]:
        """Multi-get. Returns a mapping of key → value for the keys
        that were present; absent or expired keys are simply omitted
        from the returned dict.
        """
        ...
