# SPDX-License-Identifier: AGPL-3.0-or-later
"""The If-Match helpers tests use to save as a correct client does."""

import pytest

from zephyrex.testing.factories import if_match_of


class TestIfMatchOf:
    def test_names_the_rows_updated_at(self):
        row = {"created_at": "2026-10-01T00:00:00", "updated_at": "2026-10-02T00:00:00"}
        assert if_match_of(row) == {"If-Match": '"2026-10-02T00:00:00"'}

    def test_falls_back_to_created_at_for_a_row_never_updated(self):
        row = {"created_at": "2026-10-01T00:00:00", "updated_at": None}
        assert if_match_of(row) == {"If-Match": '"2026-10-01T00:00:00"'}

    def test_a_row_without_a_version_is_refused(self):
        with pytest.raises(ValueError):
            if_match_of({"id": "x"})
