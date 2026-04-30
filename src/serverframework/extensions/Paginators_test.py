"""End-to-end tests for the paginator hierarchy."""

from __future__ import annotations

import pytest

from serverframework.extensions.ExternalErrors import InvalidPaginationError
from serverframework.extensions.Paginators import (
    CursorPaginator,
    LinkHeaderPaginator,
    OffsetPaginator,
    PageTokenPaginator,
    Pagination,
    decode_token,
    encode_token,
    query_hash,
)


class TestPaginationModel:
    def test_defaults(self):
        p = Pagination()
        assert p.offset is None
        assert p.has_more is False
        assert p.supports_random_access is False

    def test_explicit(self):
        p = Pagination(offset=0, limit=20, has_more=True, supports_random_access=True)
        assert p.has_more is True
        assert p.supports_random_access is True


class TestTokenEnvelope:
    def test_round_trip(self):
        h = query_hash({"a": 1, "b": "x"})
        tok = encode_token(provider_cursor="curs_123", page_size=20, q_hash=h)
        decoded = decode_token(tok, expected_query_hash=h)
        assert decoded["provider_cursor"] == "curs_123"
        assert decoded["page_size"] == 20
        assert decoded["query_hash"] == h

    def test_query_hash_mismatch_raises(self):
        h = query_hash({"a": 1})
        tok = encode_token(provider_cursor="curs", page_size=10, q_hash=h)
        h2 = query_hash({"a": 2})
        with pytest.raises(InvalidPaginationError):
            decode_token(tok, expected_query_hash=h2)

    def test_malformed_token_raises(self):
        with pytest.raises(InvalidPaginationError):
            decode_token("!!!not-base64!!!")

    def test_empty_token_raises(self):
        with pytest.raises(InvalidPaginationError):
            decode_token("")

    def test_query_hash_stable_across_key_order(self):
        assert query_hash({"a": 1, "b": 2}) == query_hash({"b": 2, "a": 1})


class TestOffsetPaginator:
    def test_supports_random_access(self):
        assert OffsetPaginator.supports_random_access is True

    def test_first_page(self):
        p = OffsetPaginator()
        params = p.to_external_params(offset=0, limit=10, query_params={"q": "x"})
        assert params == {"q": "x", "offset": 0, "limit": 10}

    def test_response_emits_token_when_more(self):
        p = OffsetPaginator()
        items, token = p.from_external_response(
            {"data": [{"id": i} for i in range(10)], "offset": 0, "total": 30},
            query_params={"q": "x"},
            page_size=10,
        )
        assert len(items) == 10
        assert token is not None
        decoded = decode_token(token, expected_query_hash=query_hash({"q": "x"}))
        assert decoded["provider_cursor"] == 10

    def test_response_no_more(self):
        p = OffsetPaginator()
        items, token = p.from_external_response(
            {"data": [{"id": 1}], "offset": 0, "total": 1},
            query_params={"q": "x"},
            page_size=10,
        )
        assert items == [{"id": 1}]
        assert token is None

    def test_resume_via_token(self):
        p = OffsetPaginator()
        h = query_hash({"q": "x"})
        tok = encode_token(provider_cursor=20, page_size=10, q_hash=h)
        params = p.to_external_params(next_token=tok, query_params={"q": "x"})
        assert params["offset"] == 20
        assert params["limit"] == 10


class TestCursorPaginator:
    def test_first_page(self):
        p = CursorPaginator()
        params = p.to_external_params(limit=20, query_params={"f": "a"})
        assert params == {"f": "a", "limit": 20}

    def test_response_with_more_emits_token(self):
        p = CursorPaginator()
        items, token = p.from_external_response(
            {"data": [{"id": "a"}, {"id": "b"}], "has_more": True},
            query_params={"f": "a"},
            page_size=2,
        )
        assert len(items) == 2
        assert token is not None
        decoded = decode_token(token, expected_query_hash=query_hash({"f": "a"}))
        assert decoded["provider_cursor"] == "b"

    def test_resume_uses_starting_after(self):
        p = CursorPaginator()
        h = query_hash({})
        tok = encode_token(provider_cursor="cust_x", page_size=10, q_hash=h)
        params = p.to_external_params(limit=10, next_token=tok, query_params={})
        assert params["starting_after"] == "cust_x"

    def test_does_not_support_random_access(self):
        assert CursorPaginator.supports_random_access is False


class TestPageTokenPaginator:
    def test_first_page(self):
        p = PageTokenPaginator()
        params = p.to_external_params(limit=50, query_params={"q": "x"})
        assert params == {"q": "x", "page_size": 50}

    def test_response_emits_token(self):
        p = PageTokenPaginator()
        items, token = p.from_external_response(
            {"data": [{"id": 1}, {"id": 2}], "next_page_token": "next_xyz"},
            query_params={"q": "x"},
            page_size=2,
        )
        assert items == [{"id": 1}, {"id": 2}]
        assert token is not None

    def test_no_next_token(self):
        p = PageTokenPaginator()
        items, token = p.from_external_response(
            {"data": [{"id": 1}], "next_page_token": ""},
            query_params={},
            page_size=10,
        )
        assert token is None


class TestBuildPagination:
    def test_offset_paginator_builds_random_access_envelope(self):
        p = OffsetPaginator()
        env = p.build_pagination("tok123", offset=20, limit=10, total=100)
        assert env.supports_random_access is True
        assert env.has_more is True
        assert env.offset == 20
        assert env.limit == 10
        assert env.total == 100
        assert env.next_token == "tok123"

    def test_cursor_paginator_marks_no_random_access(self):
        p = CursorPaginator()
        env = p.build_pagination(None, limit=10)
        assert env.supports_random_access is False
        assert env.has_more is False
        assert env.next_token is None


class TestLinkHeaderPaginator:
    def test_extracts_next_link(self):
        p = LinkHeaderPaginator()
        link_header = (
            '<https://api.example.com/items?page=2>; rel="next", '
            '<https://api.example.com/items?page=10>; rel="last"'
        )
        items, token = p.from_external_response(
            {"data": [{"id": 1}], "headers": {"Link": link_header}},
            query_params={"q": "x"},
            page_size=1,
        )
        assert items == [{"id": 1}]
        assert token is not None
        decoded = decode_token(token, expected_query_hash=query_hash({"q": "x"}))
        assert decoded["provider_cursor"] == "https://api.example.com/items?page=2"

    def test_no_next_link(self):
        p = LinkHeaderPaginator()
        items, token = p.from_external_response(
            {"data": [{"id": 1}], "headers": {"Link": '<https://api.example.com/items?page=1>; rel="prev"'}},
            query_params={},
            page_size=1,
        )
        assert token is None
