# SPDX-License-Identifier: AGPL-3.0-or-later
"""LDAP service accounts: the identities legacy applications bind as to
read the directory.

A service account is configured by the server (ROOT or SYSTEM; over the
API that is the system key, as for every system entity). Its secret is
write-only: it is taken on create or update, stored only as a bcrypt
hash, and never returned. A service account binds as
``cn=<name>,ou=services,<base DN>`` and reads the whole directory; it is
not an entry in the tree itself.
"""

import re
from typing import Any, ClassVar, Dict, List, Optional

import bcrypt
from fastapi import HTTPException
from pydantic import Field

from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    NameMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.AbstractLogicManager.ownership import server_side
from zephyrex.logic.BLL_Auth._shared import _BCRYPT_ROUNDS, _DUMMY_BCRYPT_HASH
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.registry import BaseModel

SERVICE_ACCOUNT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MIN_SECRET_LENGTH = 16
# bcrypt hashes at most this many bytes; a longer secret would be silently
# truncated, so it is refused instead.
MAX_SECRET_BYTES = 72


def _valid_name(name: Optional[str]) -> str:
    if not name or not SERVICE_ACCOUNT_NAME.match(name):
        raise HTTPException(
            status_code=422,
            detail="A service account name is 1-64 letters, digits, '.', '_' or '-'",
        )
    return name


def _secret_hash(secret: Optional[str]) -> str:
    if not isinstance(secret, str) or len(secret) < MIN_SECRET_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"A service account secret is at least {MIN_SECRET_LENGTH} characters",
        )
    raw = secret.encode()
    if len(raw) > MAX_SECRET_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"A service account secret is at most {MAX_SECRET_BYTES} bytes",
        )
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode()


def secret_matches(secret: str, secret_hash: Optional[str]) -> bool:
    """Whether ``secret`` is the one hashed as ``secret_hash``. Without a
    hash the same bcrypt work is done, so an unknown account takes as long
    to refuse as a wrong secret."""
    raw = secret.encode()
    if not secret_hash or len(raw) > MAX_SECRET_BYTES:
        bcrypt.checkpw(raw[:MAX_SECRET_BYTES], _DUMMY_BCRYPT_HASH)
        return False
    return bcrypt.checkpw(raw, secret_hash.encode())


class LdapServiceAccountModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel,
    metaclass=ModelMeta,
):
    """An identity a legacy application binds to the LDAP directory as."""

    description: Optional[str] = Field(None, description="What uses this account")
    enabled: bool = Field(True, description="Whether the account may bind")
    # Write-only: excluded from every serialization; only the bind reads it.
    secret_hash: Optional[str] = Field(
        None, exclude=True, description="bcrypt hash of the bind secret"
    )

    table_comment: ClassVar[str] = (
        "Service accounts that bind to this server's LDAP directory"
    )
    is_system_entity: ClassVar[bool] = True

    class Create(BaseModel, NameMixinModel):
        description: Optional[str] = None
        enabled: bool = True
        secret: Optional[str] = Field(
            None, exclude=True, description="The bind secret (write-only)"
        )
        # Server-computed from ``secret``; whatever a caller sends is replaced.
        secret_hash: Optional[str] = None

    class Update(BaseModel):
        description: Optional[str] = None
        enabled: Optional[bool] = None
        secret: Optional[str] = Field(
            None, exclude=True, description="A new bind secret (write-only)"
        )
        secret_hash: Optional[str] = None

    class Search(
        ApplicationModel.Search, UpdateMixinModel.Search, NameMixinModel.Search
    ):
        description: Optional[StringSearchModel] = None
        enabled: Optional[bool] = None


class LdapServiceAccountManager(AbstractBLLManager, RouterMixin):
    _model = LdapServiceAccountModel

    prefix: ClassVar[Optional[str]] = "/v1/ldap/service-account"
    tags: ClassVar[Optional[List[str]]] = ["LDAP Directory"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def _require_server_side(self) -> None:
        if not server_side(self.requester.id):
            raise HTTPException(
                status_code=403,
                detail="LDAP service accounts are configured by the server only",
            )

    def _named(self, name: str) -> List[Any]:
        Account = LdapServiceAccountModel.DB(self.model_registry.DB.manager.Base)
        found: List[Any] = Account.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[Account.name == name, Account.deleted_at.is_(None)],
        )
        return found

    def _prepared(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        name = _valid_name(fields.get("name"))
        if self._named(name):
            raise HTTPException(
                status_code=409, detail=f"A service account named {name!r} exists"
            )
        fields["secret_hash"] = _secret_hash(fields.pop("secret", None))
        return fields

    def create(self, **kwargs: Any) -> Any:
        self._require_server_side()
        if isinstance(kwargs.get("entities"), list):
            names = [entity.get("name") for entity in kwargs["entities"]]
            if len(set(names)) != len(names):
                raise HTTPException(
                    status_code=409, detail="Service account names repeat"
                )
            kwargs["entities"] = [self._prepared(dict(e)) for e in kwargs["entities"]]
            return super().create(**kwargs)
        return super().create(**self._prepared(dict(kwargs)))

    def update(self, id: str, **kwargs: Any) -> Any:
        self._require_server_side()
        kwargs.pop("secret_hash", None)
        if "secret" in kwargs:
            kwargs["secret_hash"] = _secret_hash(kwargs.pop("secret"))
        return super().update(id, **kwargs)

    def delete(self, id: str) -> None:
        self._require_server_side()
        super().delete(id)

    # Reads are the operator's too: an account's name and description say
    # which application binds as it, which no ordinary user may learn
    # (any user could list the SYSTEM-written ones before).
    def get(self, *args: Any, **kwargs: Any) -> Any:
        self._require_server_side()
        return super().get(*args, **kwargs)

    def list(self, *args: Any, **kwargs: Any) -> Any:
        self._require_server_side()
        return super().list(*args, **kwargs)

    def search(self, *args: Any, **kwargs: Any) -> Any:
        self._require_server_side()
        return super().search(*args, **kwargs)
