# SPDX-License-Identifier: AGPL-3.0-or-later
"""The health log: records over the API (range checks, sleep duration
kept by the server, one user's records hidden from another), the
summary over a period, and abilities acting as the user they name."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Dict, Optional

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.health.BLL_Health import moment, sleep_minutes
from zephyrex.extensions.health.EXT_Health import EXT_Health
from zephyrex.extensions.health.Summary import summarize
from zephyrex.pydantic2.registry import ModelRegistry

MONDAY = datetime(2026, 9, 28, tzinfo=UTC)


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


@dataclass
class Row:
    kind: str = "running"
    duration_minutes: int = 0
    calories_burned: Optional[float] = None
    steps: Optional[int] = None
    distance_km: Optional[float] = None
    calories: float = 0
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    measured_at: datetime = MONDAY
    weight_kg: float = 0
    quality: Optional[int] = None


class TestTimes:
    def test_sleep_minutes(self):
        assert sleep_minutes(MONDAY, MONDAY + timedelta(hours=7, minutes=30)) == 450
        assert sleep_minutes("2026-09-28T23:00:00", "2026-09-29T06:00:00+00:00") == 420

    @pytest.mark.parametrize("hours", [0, -1, 25])
    def test_impossible_sleep(self, hours):
        with pytest.raises(HTTPException):
            sleep_minutes(MONDAY, MONDAY + timedelta(hours=hours))

    def test_moments_are_utc(self):
        assert moment("2026-09-28T02:00:00+02:00") == MONDAY


class TestSummary:
    def test_a_week(self):
        summary = summarize(
            MONDAY,
            MONDAY + timedelta(days=7),
            [
                Row(kind="running", duration_minutes=30, steps=4000, distance_km=5),
                Row(kind="yoga", duration_minutes=60, calories_burned=150),
            ],
            [Row(calories=700, protein_g=40), Row(calories=700)],
            [
                Row(measured_at=MONDAY + timedelta(days=6), weight_kg=79.4),
                Row(measured_at=MONDAY, weight_kg=80.0),
            ],
            [
                Row(duration_minutes=420, quality=80),
                Row(duration_minutes=480, quality=None),
            ],
        )
        assert summary["activity"] == {
            "sessions": 2,
            "minutes": 90,
            "calories_burned": 150.0,
            "steps": 4000,
            "distance_km": 5.0,
            "by_kind": {"running": 1, "yoga": 1},
        }
        assert summary["nutrition"]["calories_per_day"] == 200.0
        assert summary["weight"]["change_kg"] == -0.6
        assert summary["sleep"] == {
            "nights": 2,
            "average_minutes": 450.0,
            "average_quality": 80.0,
        }

    def test_an_empty_period(self):
        summary = summarize(MONDAY, MONDAY + timedelta(hours=1), [], [], [], [])
        assert summary["weight"]["change_kg"] is None
        assert summary["sleep"]["average_minutes"] is None


class TestHealthLog(ExtensionServerMixin):
    extension_class = EXT_Health

    @pytest.fixture
    def attached(self, server, monkeypatch) -> None:
        registry = server.app.state.model_registry
        monkeypatch.setattr(
            ModelRegistry, "attached", classmethod(lambda cls: registry)
        )

    def test_sleep_duration_is_kept_by_the_server(self, server, admin_a):
        response = server.post(
            "/v1/health_sleep",
            json={
                "health_sleep": {
                    "bedtime": "2026-09-28T23:00:00Z",
                    "wake_time": "2026-09-29T06:30:00Z",
                    "duration_minutes": 9999,
                }
            },
            headers=auth(admin_a),
        )
        assert response.status_code == 201, response.text
        night = response.json()["health_sleep"]
        assert night["duration_minutes"] == 450
        moved = server.put(
            f"/v1/health_sleep/{night['id']}",
            json={"health_sleep": {"wake_time": "2026-09-29T07:00:00Z"}},
            headers=auth(admin_a),
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["health_sleep"]["duration_minutes"] == 480

    @pytest.mark.parametrize(
        "path, record",
        [
            ("health_weight", {"measured_at": "2026-09-28T07:00:00Z", "weight_kg": 0}),
            (
                "health_activity",
                {
                    "performed_at": "2026-09-28T07:00:00Z",
                    "kind": "running",
                    "duration_minutes": 30,
                    "heart_rate_max": 400,
                },
            ),
            (
                "health_sleep",
                {
                    "bedtime": "2026-09-29T07:00:00Z",
                    "wake_time": "2026-09-28T23:00:00Z",
                },
            ),
        ],
    )
    def test_impossible_values_are_refused(self, server, admin_a, path, record):
        response = server.post(
            f"/v1/{path}", json={path: record}, headers=auth(admin_a)
        )
        assert response.status_code == 422, response.text

    def test_another_user_sees_nothing(self, server, admin_a, admin_b):
        created = server.post(
            "/v1/health_weight",
            json={
                "health_weight": {
                    "measured_at": "2026-09-28T07:00:00Z",
                    "weight_kg": 80,
                }
            },
            headers=auth(admin_a),
        ).json()["health_weight"]
        assert (
            server.get(
                f"/v1/health_weight/{created['id']}", headers=auth(admin_b)
            ).status_code
            == 404
        )
        theirs = server.get("/v1/health_weight", headers=auth(admin_b)).json()
        assert created["id"] not in {row["id"] for row in theirs["health_weights"]}

    async def test_abilities_act_as_the_named_user(
        self, server, admin_a, admin_b, attached
    ):
        start = datetime(2026, 9, 1, tzinfo=UTC)
        await EXT_Health.log_activity(
            admin_a.id, start + timedelta(days=1), "cycling", 45, distance_km=20
        )
        await EXT_Health.log_weight(admin_a.id, start + timedelta(days=1), 81.0)
        await EXT_Health.log_weight(admin_a.id, start + timedelta(days=5), 80.2)
        await EXT_Health.log_sleep(
            admin_a.id, start + timedelta(hours=22), start + timedelta(hours=29)
        )
        summary = await EXT_Health.health_summary(
            admin_a.id, start, start + timedelta(days=7)
        )
        assert summary["activity"]["distance_km"] == 20.0
        assert summary["weight"]["change_kg"] == -0.8
        assert summary["sleep"]["average_minutes"] == 420.0

        others = await EXT_Health.health_summary(
            admin_b.id, start, start + timedelta(days=7)
        )
        assert others["activity"]["sessions"] == 0

    async def test_ability_arguments(self, attached, admin_a):
        with pytest.raises(InvalidInputExternalError):
            await EXT_Health.list_health_records(
                admin_a.id, "blood", MONDAY, MONDAY + timedelta(days=1)
            )
        with pytest.raises(InvalidInputExternalError):
            await EXT_Health.health_summary(admin_a.id, MONDAY, MONDAY)
        with pytest.raises(HTTPException) as raised:
            await EXT_Health.log_weight("", MONDAY, 80)
        assert raised.value.status_code == 400
