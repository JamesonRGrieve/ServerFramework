# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the email tests share: real provider instances, of any scope and
owner, with settings, written the way their owner would write them."""

import uuid
from typing import Any, Dict, Optional

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
)


def email_instance(
    model_registry: Any,
    provider_name: str,
    settings: Optional[Dict[str, str]] = None,
    *,
    requester_id: Optional[str] = None,
    scope: str = "root",
    team_id: Optional[str] = None,
    api_key: Optional[str] = None,
) -> ProviderInstanceModel:
    """A new instance of the email provider ``provider_name``, made (and its
    ``settings`` written) by ``requester_id``, ROOT by default; a user's
    user-scoped instance is theirs."""
    author = requester_id or env("ROOT_ID")
    provider = ProviderManager(
        model_registry=model_registry, requester_id=env("ROOT_ID")
    ).get(name=provider_name)
    fields: Dict[str, Any] = {
        "name": f"{provider_name}_{uuid.uuid4().hex}",
        "provider_id": provider.id,
        "scope": scope,
    }
    if requester_id is not None and scope == "user":
        fields["user_id"] = requester_id
    if team_id is not None:
        fields["team_id"] = team_id
    if api_key is not None:
        fields["api_key"] = api_key
    instance = ProviderInstanceModel.model_validate(
        ProviderInstanceManager(
            model_registry=model_registry, requester_id=author
        ).create(**fields),
        from_attributes=True,
    )
    rows = ProviderInstanceSettingManager(
        model_registry=model_registry, requester_id=author
    )
    for key, value in (settings or {}).items():
        rows.create(provider_instance_id=instance.id, key=key, value=value)
    return instance
