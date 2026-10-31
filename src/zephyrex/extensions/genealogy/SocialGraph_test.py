# SPDX-License-Identifier: AGPL-3.0-or-later
"""Social-graph algorithms over a known polycule and friend group.

ana ══ ben ══ cleo ══ dev        (══ partnership)
 ║
eli                              ana ══ eli ended last year
ben ── fay                       (── friendship)
dev ── fay
ben ══ ana is stored one row each way, as the client writes it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from zephyrex.extensions.genealogy.Relationships import FRIENDSHIP, PARTNERSHIP
from zephyrex.extensions.genealogy.SocialGraph import Connection, SocialGraph, Tie

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)
LAST_YEAR = NOW - timedelta(days=365)

TIES = [
    Tie("ana", "ben", PARTNERSHIP, "nesting"),
    Tie("ben", "ana", PARTNERSHIP, "nesting"),
    Tie("ben", "cleo", PARTNERSHIP, "dating"),
    Tie("cleo", "dev", PARTNERSHIP, "anchor"),
    Tie("ana", "eli", PARTNERSHIP, "dating", valid_to=LAST_YEAR),
    Tie("ben", "fay", FRIENDSHIP, "best_friend"),
    Tie("fay", "dev", FRIENDSHIP, None),
    Tie("ana", "ana", PARTNERSHIP, None),  # bad data: a self-tie is ignored
]


def _graph(kinds=(PARTNERSHIP, FRIENDSHIP), at=NOW) -> SocialGraph:
    return SocialGraph(TIES, kinds, at)


def test_a_tie_holds_both_ways_and_two_rows_fold_into_one():
    assert _graph().connections("ana") == [Connection("ben", PARTNERSHIP, "nesting")]
    assert _graph().neighbours("cleo") == {"ben", "dev"}


def test_connections_are_ordered_by_kind_then_person():
    assert _graph().connections("ben") == [
        Connection("fay", FRIENDSHIP, "best_friend"),
        Connection("ana", PARTNERSHIP, "nesting"),
        Connection("cleo", PARTNERSHIP, "dating"),
    ]


def test_an_ended_tie_is_left_out_now_but_kept_without_a_moment():
    assert "eli" not in _graph().neighbours("ana")
    assert "eli" in _graph(at=None).neighbours("ana")


def test_a_tie_not_yet_started_is_left_out():
    future = [Tie("ana", "gus", PARTNERSHIP, None, valid_from=NOW + timedelta(days=1))]
    assert SocialGraph(future, [PARTNERSHIP], NOW).neighbours("ana") == set()


def test_naive_dates_are_read_as_utc():
    naive_end = LAST_YEAR.replace(tzinfo=None)
    tie = Tie("ana", "eli", PARTNERSHIP, None, valid_to=naive_end)
    assert tie.in_effect(NOW) is False


def test_metamours_are_partners_partners_who_are_not_partners():
    assert _graph().metamours("ana") == {"cleo": {"ben"}}
    assert _graph().metamours("ben") == {"dev": {"cleo"}}
    assert _graph().metamours("dev") == {"ben": {"cleo"}}


def test_friendship_does_not_make_metamours():
    assert "fay" not in _graph().metamours("ben")


def test_a_partner_is_never_their_own_metamour():
    triad = [
        Tie("a", "b", PARTNERSHIP, None),
        Tie("b", "c", PARTNERSHIP, None),
        Tie("c", "a", PARTNERSHIP, None),
    ]
    assert SocialGraph(triad, [PARTNERSHIP], NOW).metamours("a") == {}


def test_network_walks_a_polycule_through_partnerships_only():
    assert _graph(kinds=[PARTNERSHIP]).network("ana") == {"ben": 1, "cleo": 2, "dev": 3}


def test_network_through_friendships_too_reaches_further_sooner():
    assert _graph().network("ana") == {"ben": 1, "cleo": 2, "fay": 2, "dev": 3}


@pytest.mark.parametrize(
    "depth, expected", [(0, {}), (1, {"ben": 1}), (2, {"ben": 1, "cleo": 2})]
)
def test_network_depth_limits_how_far(depth, expected):
    assert _graph(kinds=[PARTNERSHIP]).network("ana", depth) == expected


def test_someone_with_no_ties_has_an_empty_network():
    assert _graph().network("nobody") == {}


def test_ties_among_lists_each_tie_once_between_the_people_given():
    ties = _graph(kinds=[PARTNERSHIP]).ties_among(["ana", "ben", "cleo"])
    assert [
        (t.person_id, t.target_person_id, t.kind, t.discriminator) for t in ties
    ] == [
        ("ana", "ben", PARTNERSHIP, "nesting"),
        ("ben", "cleo", PARTNERSHIP, "dating"),
    ]


def test_ties_among_leaves_out_ties_to_anyone_else():
    assert _graph().ties_among(["ana", "fay"]) == []


def test_a_directed_tie_keeps_its_subject_first_when_drawn():
    mentoring = [
        Tie("zoe", "ana", "mentorship", "mentor", directed=True),
        Tie("ana", "ben", PARTNERSHIP, "nesting"),
    ]
    graph = SocialGraph(mentoring, ["mentorship", PARTNERSHIP], NOW)
    assert graph.network("ana") == {"ben": 1, "zoe": 1}
    drawn = {
        (t.person_id, t.target_person_id, t.directed)
        for t in graph.ties_among(["ana", "ben", "zoe"])
    }
    assert drawn == {("zoe", "ana", True), ("ana", "ben", False)}
