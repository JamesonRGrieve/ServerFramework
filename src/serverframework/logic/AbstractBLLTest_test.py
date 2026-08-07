"""Item 87 — independent verification for the abstract field/include test methods.

The CRUD-suite-binding tests live on each concrete manager via
``AbstractBLLTest``; the matrix is exercised end-to-end there. This file
provides direct unit-style coverage of the helpers
(``_candidate_relationship_names``, ``_captured_includes_evidence``,
``_captured_load_only_evidence``) so a regression in the abstract layer
itself surfaces here without needing to spin up the full test ladder.

No mocks of business logic — the tests build small SQLAlchemy models and
exercise the real helpers against the real ``Load`` / ``load_only``
constructs. Per AGENTS.md the helpers are pure-utility functions on
``AbstractBLLTest`` so this is unit-testing of the framework's
introspection logic, not of any BLL manager.
"""

from __future__ import annotations

from typing import List

import pytest
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import declarative_base, joinedload, load_only, relationship, selectinload

from serverframework.logic.AbstractBLLTest import AbstractBLLTest


Base = declarative_base()


class _Author(Base):  # type: ignore[misc, valid-type]
    __tablename__ = "_acl_test_author"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    books = relationship("_Book", back_populates="author")


class _Book(Base):  # type: ignore[misc, valid-type]
    __tablename__ = "_acl_test_book"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    author_id = Column(Integer, ForeignKey("_acl_test_author.id"))
    author = relationship("_Author", back_populates="books")


class _Manager:
    """Tiny stand-in for ``AbstractBLLManager`` exposing the bits the
    Item 87 helpers introspect — ``DB`` (the SQLAlchemy model) only."""

    def __init__(self, db) -> None:
        self.DB = db


# ---------------------------------------------------------------------------
# _candidate_relationship_names
# ---------------------------------------------------------------------------


def test_candidate_relationship_names_returns_sorted_relations():
    mgr = _Manager(_Author)
    rels = AbstractBLLTest._candidate_relationship_names(mgr)
    assert rels == ["books"]


def test_candidate_relationship_names_handles_no_relations():
    class NoRel(Base):
        __tablename__ = "_acl_test_no_rel"
        id = Column(Integer, primary_key=True)

    mgr = _Manager(NoRel)
    assert AbstractBLLTest._candidate_relationship_names(mgr) == []


def test_candidate_relationship_names_safe_on_missing_mapper():
    class Bare:
        pass

    mgr = _Manager(Bare)
    assert AbstractBLLTest._candidate_relationship_names(mgr) == []


# ---------------------------------------------------------------------------
# _captured_includes_evidence
# ---------------------------------------------------------------------------


def test_captured_includes_evidence_detects_joinedload():
    holder = {"options": [joinedload(_Author.books)], "fields": None}
    assert AbstractBLLTest._captured_includes_evidence(holder) is True


def test_captured_includes_evidence_detects_selectinload():
    holder = {"options": [selectinload(_Author.books)], "fields": None}
    assert AbstractBLLTest._captured_includes_evidence(holder) is True


def test_captured_includes_evidence_empty_options_means_n_plus_one_leak():
    holder = {"options": [], "fields": None}
    assert AbstractBLLTest._captured_includes_evidence(holder) is False


def test_captured_includes_evidence_options_none():
    holder = {"options": None, "fields": None}
    assert AbstractBLLTest._captured_includes_evidence(holder) is False


# ---------------------------------------------------------------------------
# _captured_load_only_evidence — load_only + fields-kwarg paths
# ---------------------------------------------------------------------------


def test_captured_load_only_evidence_detects_load_only_option():
    holder = {"options": [load_only(_Author.name)], "fields": None}
    assert (
        AbstractBLLTest._captured_load_only_evidence(holder, ["name"]) is True
    )


def test_captured_load_only_evidence_detects_fields_kwarg_path():
    holder = {"options": [], "fields": ["name", "id"]}
    assert (
        AbstractBLLTest._captured_load_only_evidence(holder, ["name"]) is True
    )


def test_captured_load_only_evidence_no_evidence_when_neither_present():
    holder = {"options": [], "fields": None}
    assert (
        AbstractBLLTest._captured_load_only_evidence(holder, ["name"]) is False
    )


def test_captured_load_only_evidence_options_with_unrelated_load():
    holder = {"options": [joinedload(_Author.books)], "fields": None}
    # joinedload IS a Load-family option so this evaluates to True per
    # the helper's contract — which is correct: the evidence we capture
    # is "BLL pushed a Load option to the DB layer", not "BLL pushed a
    # column-projection Load option specifically". The fields-kwarg
    # path catches the column-projection case for managers that route
    # field selection through the kwarg.
    assert (
        AbstractBLLTest._captured_load_only_evidence(holder, ["name"]) is True
    )


# ---------------------------------------------------------------------------
# _is_load_option — direct verification
# ---------------------------------------------------------------------------


def test_is_load_option_detects_load_only():
    assert AbstractBLLTest._is_load_option(load_only(_Author.name)) is True


def test_is_load_option_detects_joinedload():
    assert AbstractBLLTest._is_load_option(joinedload(_Author.books)) is True


def test_is_load_option_detects_selectinload():
    assert AbstractBLLTest._is_load_option(selectinload(_Author.books)) is True


def test_is_load_option_rejects_plain_object():
    assert AbstractBLLTest._is_load_option(object()) is False
    assert AbstractBLLTest._is_load_option("load_only") is False
    assert AbstractBLLTest._is_load_option(None) is False


# ---------------------------------------------------------------------------
# _capture_db_options helper — verifies the snapshot mechanism
# ---------------------------------------------------------------------------


class _StubDB:
    def __init__(self) -> None:
        self.calls: List[dict] = []

    def get(self, *args, **kwargs):
        self.calls.append({"method": "get", **kwargs})
        return None

    def list(self, *args, **kwargs):
        self.calls.append({"method": "list", **kwargs})
        return []


class _StubManager:
    def __init__(self) -> None:
        self.DB = _StubDB()


def test_capture_db_options_records_options_kwarg():
    mgr = _StubManager()
    holder, restore = AbstractBLLTest._capture_db_options(mgr, "get")
    try:
        opt = load_only(_Author.name)
        mgr.DB.get(options=[opt])
    finally:
        restore()
    assert holder["options"] == [opt]
    assert holder["fields"] is None


def test_capture_db_options_records_fields_kwarg():
    mgr = _StubManager()
    holder, restore = AbstractBLLTest._capture_db_options(mgr, "list")
    try:
        mgr.DB.list(fields=["name"])
    finally:
        restore()
    assert holder["fields"] == ["name"]


def test_capture_db_options_restore_unwraps_stub():
    mgr = _StubManager()
    holder, restore = AbstractBLLTest._capture_db_options(mgr, "get")
    # During capture, the DB.get attribute is the inner _capture function
    # (a plain function); after restore it's a bound method again.
    assert callable(mgr.DB.get)
    restore()
    # After restore, the call goes through to the real stub which appends
    # to mgr.DB.calls — verifies the capture wrapper is gone.
    mgr.DB.get(id="post-restore")
    assert mgr.DB.calls == [{"method": "get", "id": "post-restore"}]


def test_capture_db_options_returns_empty_list_for_list_method():
    """``_capture`` returns ``[]`` for ``list`` so downstream BLL
    code that expects a list-typed result keeps working."""
    mgr = _StubManager()
    holder, restore = AbstractBLLTest._capture_db_options(mgr, "list")
    try:
        result = mgr.DB.list()
    finally:
        restore()
    assert result == []


def test_capture_db_options_returns_none_for_get_method():
    mgr = _StubManager()
    holder, restore = AbstractBLLTest._capture_db_options(mgr, "get")
    try:
        result = mgr.DB.get(id="x")
    finally:
        restore()
    assert result is None
