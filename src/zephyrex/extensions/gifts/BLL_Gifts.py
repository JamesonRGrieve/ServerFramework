# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gift ideas: someone's own list, for the people in their life.

Each gift is the giver's alone (nobody else, the person it is for
included, ever sees it): who it is for, what it is, a link, a note, and how
far along it is (an idea, bought, given). An app may tie a gift to its own
record of the person (``for_ref``, e.g. a genealogy person's id).

Links to the shop carry the operator's Amazon Associates tag
(``AMAZON_ASSOCIATE_TAG``) at their Amazon store (``AMAZON_DOMAIN``): a
search for an idea, and any Amazon product link someone saves. The tag is
server configuration, never sent by a client; without one, links go out
untagged.

Routes: the gifts are the owner's (``/v1/gift``); ``/v1/gifts`` adds
``GET /shop-link?q=`` (a tagged search) and ``POST /link`` (a pasted link,
tagged when it is Amazon's).
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Literal, Optional, Type
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException
from pydantic import BaseModel as ResponseModel
from pydantic import Field, field_validator

from zephyrex.extensions.privacy.Erasure import register_eraser
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.AbstractLogicManager.ownership import each_created
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel

GIFTS_PREFIX = "/v1/gifts"
GiftStatus = Literal["idea", "bought", "given"]
MAX_TEXT = 200
MAX_NOTE = 2_000
MAX_URL = 2_000
DEFAULT_AMAZON_DOMAIN = "www.amazon.com"
# The marketplaces Amazon Associates runs (a tag belongs to one of them).
AMAZON_HOSTS = (
    "amazon.com",
    "amazon.ca",
    "amazon.co.uk",
    "amazon.de",
    "amazon.fr",
    "amazon.it",
    "amazon.es",
    "amazon.com.au",
    "amazon.co.jp",
    "amazon.in",
    "amazon.com.mx",
    "amazon.com.br",
    "amazon.nl",
    "amazon.se",
)


def checked_link(value: Optional[str]) -> Optional[str]:
    """``value`` when it is an http(s) link, refused otherwise (no
    ``javascript:`` or ``data:`` link ever reaches a page)."""
    if value is None or value.strip() == "":
        return None
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("A link starts with http:// or https://")
    return value.strip()


def is_amazon(host: str) -> bool:
    host = host.lower().split(":")[0]
    return any(host == known or host.endswith(f".{known}") for known in AMAZON_HOSTS)


def amazon_domain() -> str:
    domain = str(env("AMAZON_DOMAIN") or DEFAULT_AMAZON_DOMAIN).strip().lower()
    if not is_amazon(domain):
        raise HTTPException(
            status_code=500, detail="AMAZON_DOMAIN is not an Amazon store"
        )
    return domain


    """``url`` carrying the affiliate ``tag`` when it is an Amazon link
    (replacing any tag already there); anything else is left as it is."""
    parts = urlsplit(url)
    if not tag or not is_amazon(parts.netloc):
        return url
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "tag"
    ]
    return urlunsplit(parts._replace(query=urlencode([*query, ("tag", tag)])))


def search_link(query: str, domain: str, tag: Optional[str]) -> str:
    """A search for ``query`` at the Amazon store ``domain``, tagged."""
    return tagged(f"https://{domain}/s?{urlencode({'k': query})}", tag)


def _tag() -> Optional[str]:
    return str(env("AMAZON_ASSOCIATE_TAG") or "").strip() or None


    Manager: ClassVar[Type["GiftManager"]]
    for_name: str = Field(..., description="Who it is for")
    for_ref: Optional[str] = Field(
        None, description="The app's own record of them, e.g. a person id"
    )
    title: str = Field(..., description="What it is")
    url: Optional[str] = Field(
        None, description="Where to buy it (Amazon links carry the operator's tag)"
    )
    note: Optional[str] = Field(None, description="Sizes, colours, the occasion")
    status: str = Field("idea", description="idea, bought or given")

    table_comment: ClassVar[str] = "Gift ideas, each its giver's alone"

    class Create(BaseModel):
        for_name: str = Field(..., min_length=1, max_length=MAX_TEXT)
        for_ref: Optional[str] = Field(None, max_length=MAX_TEXT)
        title: str = Field(..., min_length=1, max_length=MAX_TEXT)
        url: Optional[str] = Field(None, max_length=MAX_URL)
        note: Optional[str] = Field(None, max_length=MAX_NOTE)
        status: GiftStatus = "idea"

        @field_validator("url")
        @classmethod
        def _url(cls, value: Optional[str]) -> Optional[str]:
            return checked_link(value)

    class Update(BaseModel):
        for_name: Optional[str] = Field(None, min_length=1, max_length=MAX_TEXT)
        for_ref: Optional[str] = Field(None, max_length=MAX_TEXT)
        title: Optional[str] = Field(None, min_length=1, max_length=MAX_TEXT)
        url: Optional[str] = Field(None, max_length=MAX_URL)
        note: Optional[str] = Field(None, max_length=MAX_NOTE)
        status: Optional[GiftStatus] = None

        @field_validator("url")
        @classmethod
        def _url(cls, value: Optional[str]) -> Optional[str]:
            return checked_link(value)

    class Search(ApplicationModel.Search):
        for_ref: Optional[StringSearchModel] = None


class ShopLink(ResponseModel):
    url: str
    tagged: bool = Field(
        ..., description="Whether it carries the operator's affiliate tag"
    )


class LinkToTag(ResponseModel):
    url: str = Field(..., max_length=MAX_URL)


def _tag_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    if fields.get("url"):
        fields["url"] = tagged(str(fields["url"]), _tag())
    return fields


class GiftManager(AbstractBLLManager, RouterMixin):
    """``/v1/gift``: the giver's own gift ideas; Amazon links saved tagged."""

    _model = GiftModel

    def create(self, **kwargs: Any) -> Any:
        return super().create(**each_created(kwargs, _tag_fields))

    def update(self, id: str, **kwargs: Any) -> Any:
        return super().update(id, **_tag_fields(dict(kwargs)))


class GiftLinkManager(AbstractBLLManager, RouterMixin):
    """``/v1/gifts``: links to the shop."""

    prefix: ClassVar[Optional[str]] = GIFTS_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["Gifts"]
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    @custom_route(
        method="GET",
        path="/shop-link",
        output_model=ShopLink,
        authentication_type="jwt",
        openapi_tags=("Gifts",),
        summary="A search at the shop for a gift idea",
        expose_in=(ExposeIn.REST,),
    )
    def shop_link_route(self, q: str) -> ShopLink:
        if not q.strip() or len(q) > MAX_TEXT:
            raise HTTPException(
                status_code=422, detail=f"Search for 1 to {MAX_TEXT} characters"
            )
        tag = _tag()
        return ShopLink(
            url=search_link(q.strip(), amazon_domain(), tag), tagged=tag is not None
        )

    @custom_route(
        method="POST",
        path="/link",
        input_model=LinkToTag,
        output_model=ShopLink,
        authentication_type="jwt",
        openapi_tags=("Gifts",),
        summary="A link to buy something, carrying the affiliate tag when it is Amazon's",
        expose_in=(ExposeIn.REST,),
    )
    def link_route(self, body: LinkToTag) -> ShopLink:
        try:
            url = checked_link(body.url)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        if url is None:
            raise HTTPException(status_code=422, detail="Paste a link")
        tag = _tag()
        return ShopLink(
            url=tagged(url, tag),
            tagged=tag is not None and is_amazon(urlsplit(url).netloc),
        )


GiftModel.Manager = GiftManager


def erase_gifts(user_id: str, model_registry: Any) -> None:
    """A deleted account's gift ideas go. Safe to run twice."""
    db = GiftModel.DB(model_registry.DB.manager.Base)
    root = str(env("ROOT_ID"))
    for gift in (
        db.list(
            requester_id=root,
            model_registry=model_registry,
            filters=[db.created_by_user_id == user_id, db.deleted_at.is_(None)],
            return_type="dto",
            override_dto=GiftModel,
        )
        or []
    ):
        db.delete(
            requester_id=root, model_registry=model_registry, filters=[db.id == gift.id]
        )


register_eraser("gifts", erase_gifts)

ALL_MODELS: List[type] = [GiftModel]
