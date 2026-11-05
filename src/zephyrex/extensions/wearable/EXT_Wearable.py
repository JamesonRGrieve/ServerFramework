# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wearables: a day's activity, heart and sleep data, the paired devices,
and a metric's trend over a period, through Fitbit's Web API under the
provider rotation (an instance is an account's OAuth token).
"""

import re
from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# Activity resources with a daily value, and the trend periods allowed.
METRICS = (
    "steps",
    "calories",
    "distance",
    "floors",
    "elevation",
    "minutesSedentary",
    "minutesLightlyActive",
    "minutesFairlyActive",
    "minutesVeryActive",
    "heart",
    "sleep",
)
PERIODS = ("7d", "30d", "1w", "1m", "3m")
_DATE = re.compile(r"^(today|\d{4}-\d{2}-\d{2})$")


def checked_metric(metric: str) -> str:
    if metric not in METRICS:
        raise InvalidInputExternalError(f"metric must be one of {', '.join(METRICS)}")
    return metric


def checked_date(date: str) -> str:
    if not _DATE.match(date):
        raise InvalidInputExternalError("date must be today or YYYY-MM-DD")
    return date


def checked_period(period: str) -> str:
    if period not in PERIODS:
        raise InvalidInputExternalError(f"period must be one of {', '.join(PERIODS)}")
    return period


def trend(values: List[float]) -> Dict[str, Any]:
    """Summary of a series: its count, min, max, mean, and the change from
    the first half's mean to the second's."""
    if not values:
        return {"count": 0}
    half = len(values) // 2
    first, second = values[:half] or values, values[half:]
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
        "change": sum(second) / len(second) - sum(first) / len(first),
    }


class AbstractWearableProvider(AbstractStaticProvider):
    """A wearable platform. Every ability takes the rotated instance."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "get_health_data",
        "get_devices",
        "analyze_trends",
    }
    _env: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    @abstractmethod
    async def get_health_data(
        cls, instance: ProviderInstanceModel, metric: str, date: str
    ) -> Dict[str, Any]:
        """``metric`` for the day ``date``."""

    @classmethod
    @abstractmethod
    async def get_devices(cls, instance: ProviderInstanceModel) -> List[Dict[str, Any]]:
        """The account's paired devices with battery and last sync."""

    @classmethod
    @abstractmethod
    async def get_series(
        cls, instance: ProviderInstanceModel, metric: str, period: str
    ) -> List[Dict[str, Any]]:
        """``metric``'s daily values over the period ending today:
        each a ``date`` and numeric ``value``."""

    @classmethod
    def services(cls) -> List[str]:
        return ["wearable", "health", "activity"]


class EXT_Wearable(AbstractStaticExtension):
    name: ClassVar[str] = "wearable"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Wearable activity, heart and sleep data, devices and trends (Fitbit)"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = AbstractWearableProvider._abilities

    @classmethod
    def get_required_permissions(cls) -> List[str]:
        return ["wearable:read"]

    @classmethod
    @ability("get_health_data")
    async def get_health_data(cls, metric: str, date: str = "today") -> Dict[str, Any]:
        data: Dict[str, Any] = await cls.rotate_provider(
            "get_health_data", checked_metric(metric), checked_date(date)
        )
        return data

    @classmethod
    @ability("get_devices")
    async def get_devices(cls) -> List[Dict[str, Any]]:
        devices: List[Dict[str, Any]] = await cls.rotate_provider("get_devices")
        return devices

    @classmethod
    @ability("analyze_trends")
    async def analyze_trends(cls, metric: str, period: str = "7d") -> Dict[str, Any]:
        """``metric`` day by day over ``period``, with its summary."""
        metric = checked_metric(metric)
        if metric in ("heart", "sleep"):
            raise InvalidInputExternalError(
                f"{metric} has no single daily value to trend; read it by day"
            )
        series: List[Dict[str, Any]] = await cls.rotate_provider(
            "get_series", metric, checked_period(period)
        )
        return {
            "metric": metric,
            "period": period,
            "series": series,
            "summary": trend([float(point["value"]) for point in series]),
        }
