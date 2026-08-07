"""CostSummary model + roll-up aggregator (Item 84).

The audit log is the source of truth for per-request cost; this module
provides the day/week/month/year roll-up surface that powers fast
"how much did tenant X spend on provider Y last month?" queries.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from zephyrex.logic.AbstractLogicManager import (
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    NumericalSearchModel,
    StringSearchModel,
    UpdateMixinModel,
)


PeriodLiteral = Literal["day", "week", "month", "year"]


class CostSummaryModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    """Aggregated per-tenant / per-provider / per-ability spend for a period."""

    tenant_id: str = Field(
        ..., description="Team or user id; matches RotationManager._tenant_id()"
    )
    provider: str = Field(..., description="Provider name (e.g. 'openai')")
    ability: str = Field(
        ..., description="Ability name; 'unknown' if the call was not labeled"
    )
    period: PeriodLiteral = Field(..., description="Aggregation window")
    period_key: str = Field(
        ..., description="Period bucket key, e.g. '2026-04-30' for daily"
    )
    total_cost_usd: Decimal = Field(
        ..., description="Sum of per-request cost rows in this bucket"
    )
    call_count: int = Field(0, description="Number of billable calls in this bucket")

    permission_references: ClassVar[List[str]] = ["meta_logging"]
    table_comment: ClassVar[str] = (
        "Daily/weekly/monthly cost roll-up (Item 84 cost observability)"
    )

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"CostSummaryModel(tenant_id={self.tenant_id!r}, "
            f"provider={self.provider!r}, ability={self.ability!r}, "
            f"period={self.period!r}, period_key={self.period_key!r}, "
            f"total_cost_usd={self.total_cost_usd}, call_count={self.call_count})"
        )

    class Create(BaseModel):
        tenant_id: str
        provider: str
        ability: str
        period: PeriodLiteral
        period_key: str
        total_cost_usd: Decimal
        call_count: int = 0

    class Update(BaseModel):
        total_cost_usd: Optional[Decimal] = None
        call_count: Optional[int] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        tenant_id: Optional[StringSearchModel] = None
        provider: Optional[StringSearchModel] = None
        ability: Optional[StringSearchModel] = None
        period: Optional[StringSearchModel] = None
        period_key: Optional[StringSearchModel] = None
        total_cost_usd: Optional[NumericalSearchModel] = None
        call_count: Optional[NumericalSearchModel] = None
        created_at: Optional[DateSearchModel] = None


@dataclass
class _Bucket:
    cost: Decimal = Decimal("0")
    count: int = 0


class CostRollupAggregator:
    """Pure aggregator. Accumulates ``(tenant, provider, ability) -> (cost, count)``
    and emits one ``CostSummaryModel.Create`` per unique key."""

    def __init__(self) -> None:
        self._buckets: Dict[Tuple[str, str, str], _Bucket] = defaultdict(_Bucket)

    def add(
        self,
        *,
        tenant_id: str,
        provider: str,
        ability: str,
        cost: Decimal,
    ) -> None:
        key = (tenant_id, provider, ability)
        bucket = self._buckets[key]
        bucket.cost = bucket.cost + Decimal(str(cost))
        bucket.count = bucket.count + 1

    def is_empty(self) -> bool:
        return not self._buckets

    def into_summaries(
        self,
        *,
        period: str,
        period_key: str,
    ) -> List[CostSummaryModel.Create]:
        summaries: List[CostSummaryModel.Create] = []
        for (tenant_id, provider, ability), bucket in self._buckets.items():
            summaries.append(
                CostSummaryModel.Create(
                    tenant_id=tenant_id,
                    provider=provider,
                    ability=ability,
                    period=period,  # type: ignore[arg-type]
                    period_key=period_key,
                    total_cost_usd=bucket.cost,
                    call_count=bucket.count,
                )
            )
        return summaries


__all__ = [
    "CostSummaryModel",
    "CostRollupAggregator",
    "PeriodLiteral",
]
