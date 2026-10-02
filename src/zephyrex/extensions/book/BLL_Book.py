# SPDX-License-Identifier: AGPL-3.0-or-later
"""Books and their chapters, owned like any record (by their creator,
optionally shared with a team).

A chapter's text is Markdown; its word count is kept by the manager on
every write. ``GET /v1/book/{book_id}/export?format=markdown|html|epub``
downloads the book with its chapters in order.
"""

from typing import Any, ClassVar, List, Literal, Optional, Type

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import Field

from zephyrex.extensions.book.Export import (
    FORMATS,
    Chapter,
    Manuscript,
    render,
    word_count,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    NumericalSearchModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import TeamModel, UserModel
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.registry import BaseModel

BookStatus = Literal["draft", "review", "editing", "finalized", "published", "archived"]
MAX_TITLE = 300


class BookModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["BookManager"]]
    title: str = Field(..., description="Title")
    author: Optional[str] = Field(None, description="Author shown on the book")
    description: Optional[str] = Field(None, description="Blurb or summary")
    genre: Optional[str] = Field(None, description="Genre")
    language: str = Field("en", description="BCP 47 language tag (en, fr-CA, …)")
    status: BookStatus = Field("draft", description="Where the book is in its life")

    table_comment: ClassVar[str] = "Books: their metadata and status"

    class Create(BaseModel):
        title: str = Field(..., min_length=1, max_length=MAX_TITLE)
        author: Optional[str] = None
        description: Optional[str] = None
        genre: Optional[str] = None
        language: str = "en"
        status: BookStatus = "draft"
        user_id: Optional[str] = None
        team_id: Optional[str] = None

    class Update(BaseModel):
        title: Optional[str] = Field(None, min_length=1, max_length=MAX_TITLE)
        author: Optional[str] = None
        description: Optional[str] = None
        genre: Optional[str] = None
        language: Optional[str] = None
        status: Optional[BookStatus] = None

    class Search(ApplicationModel.Search):
        title: Optional[StringSearchModel] = None
        author: Optional[StringSearchModel] = None
        genre: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None


class BookChapterModel(
    ApplicationModel,
    UpdateMixinModel,
    BookModel.Reference,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["BookChapterManager"]]
    title: str = Field(..., description="Chapter title")
    content: str = Field("", description="Chapter text (Markdown)")
    position: int = Field(..., description="Order in the book, from 1")
    word_count: int = Field(0, description="Words in the content (kept by the server)")

    table_comment: ClassVar[str] = "A book's chapters, in order, as Markdown"

    # word_count is set by the manager from the content; a value a client
    # sends is replaced.
    class Create(BaseModel):
        book_id: str
        title: str = Field(..., min_length=1, max_length=MAX_TITLE)
        content: str = ""
        position: int = Field(..., ge=1)
        word_count: int = 0

    class Update(BaseModel):
        title: Optional[str] = Field(None, min_length=1, max_length=MAX_TITLE)
        content: Optional[str] = None
        position: Optional[int] = Field(None, ge=1)
        word_count: Optional[int] = None

    class Search(ApplicationModel.Search, BookModel.Reference.ID.Search):
        title: Optional[StringSearchModel] = None
        position: Optional[NumericalSearchModel] = None


class BookChapterManager(AbstractBLLManager, RouterMixin):
    _model = BookChapterModel

    def create(self, **kwargs: Any) -> Any:
        if isinstance(kwargs.get("entities"), list):
            kwargs["entities"] = [
                {**entity, "word_count": word_count(entity.get("content"))}
                for entity in kwargs["entities"]
            ]
            return super().create(**kwargs)
        return super().create(
            **{**kwargs, "word_count": word_count(kwargs.get("content"))}
        )

    def update(self, id: str, **kwargs: Any) -> Any:
        kwargs.pop("word_count", None)
        if kwargs.get("content") is not None:
            kwargs["word_count"] = word_count(kwargs["content"])
        return super().update(id, **kwargs)

    def in_order(self, book_id: str) -> List[Any]:
        """The book's chapters the requester can see, in order."""
        chapters = self.list(book_id=book_id) or []
        return sorted(chapters, key=lambda c: (c.position, str(c.created_at)))


class BookManager(AbstractBLLManager, RouterMixin):
    _model = BookModel

    def chapters(self) -> BookChapterManager:
        return BookChapterManager(
            model_registry=self.model_registry, requester_id=self.requester.id
        )

    def manuscript(self, book_id: str) -> Manuscript:
        """The book and its chapters in order, as the requester sees them."""
        book = self.get(id=book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return Manuscript(
            id=str(book.id),
            title=book.title,
            author=book.author or "",
            language=book.language or "en",
            description=book.description or "",
            chapters=[
                Chapter(title=chapter.title, content=chapter.content or "")
                for chapter in self.chapters().in_order(book_id)
            ],
        )

    @custom_route(
        method="GET",
        path="/{book_id}/export",
        authentication_type="jwt",
        openapi_tags=("Book",),
        summary="Download a book with its chapters as Markdown, HTML or EPUB",
        expose_in=(ExposeIn.REST,),
        response_class=Response,
    )
    def export_route(self, book_id: str, format: str = "markdown") -> Response:
        if format not in FORMATS:
            raise HTTPException(
                status_code=400, detail=f"format must be one of {', '.join(FORMATS)}"
            )
        rendered = render(self.manuscript(book_id), format)
        return Response(
            rendered.body,
            media_type=rendered.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{rendered.filename}"'
            },
        )
