# SPDX-License-Identifier: AGPL-3.0-or-later
"""A period of a user's health log, summed up: activity, nutrition per
day, weight change and average sleep."""

from collections import Counter
from datetime import datetime
from statistics import fmean
from typing import Any, Dict, List, Optional, Sequence

from zephyrex.extensions.health.BLL_Health import moment

SECONDS_PER_DAY = 86_400


def _total(records: Sequence[Any], field: str) -> float:
    return float(sum(getattr(record, field) or 0 for record in records))


def _average(values: List[float]) -> Optional[float]:
    return round(fmean(values), 1) if values else None


def summarize(
    start: datetime,
    end: datetime,
    activities: Sequence[Any],
    meals: Sequence[Any],
    weights: Sequence[Any],
    sleeps: Sequence[Any],
) -> Dict[str, Any]:
    """The records between ``start`` and ``end``, summed up."""
    days = max((moment(end) - moment(start)).total_seconds() / SECONDS_PER_DAY, 1.0)
    weigh_ins = sorted(weights, key=lambda record: moment(record.measured_at))
    calories_eaten = _total(meals, "calories")
    return {
        "start": moment(start).isoformat(),
        "end": moment(end).isoformat(),
        "activity": {
            "sessions": len(activities),
            "minutes": int(_total(activities, "duration_minutes")),
            "calories_burned": _total(activities, "calories_burned"),
            "steps": int(_total(activities, "steps")),
            "distance_km": round(_total(activities, "distance_km"), 2),
            "by_kind": dict(Counter(record.kind for record in activities)),
        },
        "nutrition": {
            "meals": len(meals),
            "calories": calories_eaten,
            "calories_per_day": round(calories_eaten / days, 1),
            "protein_g": _total(meals, "protein_g"),
            "carbs_g": _total(meals, "carbs_g"),
            "fat_g": _total(meals, "fat_g"),
        },
        "weight": {
            "weigh_ins": len(weigh_ins),
            "first_kg": weigh_ins[0].weight_kg if weigh_ins else None,
            "last_kg": weigh_ins[-1].weight_kg if weigh_ins else None,
            "change_kg": (
                round(weigh_ins[-1].weight_kg - weigh_ins[0].weight_kg, 2)
                if weigh_ins
                else None
            ),
        },
        "sleep": {
            "nights": len(sleeps),
            "average_minutes": _average(
                [float(record.duration_minutes) for record in sleeps]
            ),
            "average_quality": _average(
                [
                    float(record.quality)
                    for record in sleeps
                    if record.quality is not None
                ]
            ),
        },
    }
