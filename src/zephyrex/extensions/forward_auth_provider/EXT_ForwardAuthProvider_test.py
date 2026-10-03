# SPDX-License-Identifier: AGPL-3.0-or-later
"""The extension's declaration, and the pure units its endpoint decides by:
host and path normalization, rule selection, header encoding, and parsing
the operator's settings.

Rewritten with the extension: the scaffold's tests asserted version 1.0.0,
the legacy ``extension_dependencies`` list and the scaffold's columns
(path_pattern, required_roles, deny_action), none of which exist now."""

from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from zephyrex.extensions.forward_auth_provider.BLL_ForwardAuthProvider import (
    ForwardAuthMisconfigured,
    ForwardAuthRuleModel,
    covers,
    decide,
    governing_rule,
    header_value,
    normalized_host,
    normalized_path,
    parse_identity_headers,
    parse_login_url,
    parse_return_hosts,
    parse_return_parameter,
    parse_trusted_proxies,
    split_authority,
)
from zephyrex.extensions.forward_auth_provider.EXT_ForwardAuthProvider import (
    EXT_ForwardAuthProvider,
)

CREATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def a_rule(
    path_prefix: str = "/",
    host: Optional[str] = None,
    team: Optional[str] = None,
    priority: int = 0,
    rule_id: str = "r",
) -> Any:
    return ForwardAuthRuleModel.model_construct(
        id=rule_id,
        name=rule_id,
        host=host,
        path_prefix=path_prefix,
        required_team_id=team,
        priority=priority,
        enabled=True,
        created_at=CREATED_AT,
        created_by_user_id="root",
    )


class TestDeclaration:
    def test_metadata(self) -> None:
        assert EXT_ForwardAuthProvider.name == "forward_auth_provider"
        assert EXT_ForwardAuthProvider.version == "2.0.0"
        assert [d.name for d in EXT_ForwardAuthProvider.dependencies.ext] == [
            "auth_session"
        ]

    def test_abilities(self) -> None:
        assert set(EXT_ForwardAuthProvider.get_abilities()) >= {
            "list_forward_auth_rules",
            "check_forward_auth_access",
        }


class TestPaths:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("/", "/"),
            ("/admin/", "/admin"),
            ("/%61dmin", "/admin"),
            ("/a/../admin", "/admin"),
            ("/../../admin", "/admin"),
            ("//admin//x", "/admin/x"),
            ("/./a/./b", "/a/b"),
            ("/a\\..\\admin", "/admin"),
            ("/a%2Fb", "/a/b"),
            ("/a?b=/../c#d", "/a"),
            ("/%2561", "/%61"),
        ],
    )
    def test_normalized(self, raw: str, expected: str) -> None:
        assert normalized_path(raw) == expected

    @pytest.mark.parametrize("raw", ["admin", "", "http://x/", "/a%00b", "/a%0Ab"])
    def test_refused(self, raw: str) -> None:
        with pytest.raises(ValueError):
            normalized_path(raw)

    def test_covers_on_segment_boundaries(self) -> None:
        assert covers("/", "/anything")
        assert covers("/admin", "/admin")
        assert covers("/admin", "/admin/x")
        assert not covers("/admin", "/administrator")


class TestHosts:
    def test_normalized(self) -> None:
        assert normalized_host("App.Example.TEST.") == "app.example.test"
        assert normalized_host("[::1]") == "[::1]"
        for bad in ("https://a", "a/b", "a b", "-a", "", "a..b"):
            with pytest.raises(ValueError):
                normalized_host(bad)

    def test_authority(self) -> None:
        assert split_authority("App.Test:8443") == ("app.test", "app.test:8443")
        assert split_authority("[::1]:80") == ("[::1]", "[::1]:80")
        for bad in ("a:x", "a:0", "a:70000", "a:1:2", "a@b"):
            with pytest.raises(ValueError):
                split_authority(bad)


class TestRuleSelection:
    def test_longest_path_then_host_then_priority(self) -> None:
        everywhere = a_rule("/", rule_id="everywhere")
        admin_any = a_rule("/admin", rule_id="admin_any")
        admin_host = a_rule("/admin", host="a.test", rule_id="admin_host")
        root_host = a_rule("/", host="a.test", priority=9, rule_id="root_host")
        rules = [everywhere, admin_any, admin_host, root_host]
        assert governing_rule(rules, "a.test", "/admin/x") is admin_host
        assert governing_rule(rules, "b.test", "/admin/x") is admin_any
        assert governing_rule(rules, "a.test", "/other") is root_host
        assert governing_rule(rules, "b.test", "/other") is everywhere
        low, high = a_rule("/x", priority=1, rule_id="low"), a_rule(
            "/x", priority=2, rule_id="high"
        )
        assert governing_rule([low, high], "a.test", "/x") is high

    def test_decide(self) -> None:
        rules = [a_rule("/ops", team="t1", rule_id="ops")]
        assert decide(rules, "h", "/ops/a", ["t1"], False).allowed
        refused = decide(rules, "h", "/ops/a", ["t2"], False)
        assert not refused.allowed and refused.rule_id == "ops"
        assert decide(rules, "h", "/else", [], False).allowed
        assert not decide(rules, "h", "/else", [], True).allowed


class TestHeaderValues:
    def test_encoding(self) -> None:
        assert header_value("Ann O'Neil, PhD") == "Ann O'Neil, PhD"
        assert header_value("a,b", list_item=True) == "a%2Cb"
        assert header_value("x\r\nSet-Cookie: y") == "x%0D%0ASet-Cookie: y"
        assert header_value("Zoë 100%") == "Zo%C3%AB 100%25"


class TestSettings:
    def test_identity_headers(self) -> None:
        assert parse_identity_headers(" Remote-User = id , X-Groups=teams ") == (
            ("Remote-User", "id"),
            ("X-Groups", "teams"),
        )
        assert parse_identity_headers("") == ()

    @pytest.mark.parametrize(
        "raw",
        [
            "Authorization=id",
            "Set-Cookie=email",
            "X-User",
            "X-User=password",
            "X User=id",
            "X-A=id,x-a=email",
        ],
    )
    def test_identity_headers_refused(self, raw: str) -> None:
        with pytest.raises(ForwardAuthMisconfigured):
            parse_identity_headers(raw)

    def test_login_url(self) -> None:
        assert parse_login_url("") is None
        assert parse_login_url("https://l.test/in") == "https://l.test/in"
        for bad in ("/in", "javascript:alert(1)", "https://l.test/in#x"):
            with pytest.raises(ForwardAuthMisconfigured):
                parse_login_url(bad)

    def test_return_settings(self) -> None:
        assert parse_return_parameter("") == "rd"
        with pytest.raises(ForwardAuthMisconfigured):
            parse_return_parameter("r&d")
        assert parse_return_hosts("A.test, .Corp.Test") == ("a.test", ".corp.test")
        with pytest.raises(ForwardAuthMisconfigured):
            parse_return_hosts("https://a.test")

    def test_trusted_proxies(self) -> None:
        assert len(parse_trusted_proxies("10.0.0.0/8, ::1")) == 2
        for bad in ("*", "10.0.0.0/33", "proxy.local"):
            with pytest.raises(ForwardAuthMisconfigured):
                parse_trusted_proxies(bad)
