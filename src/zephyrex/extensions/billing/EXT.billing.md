# `billing` extension

Cost model registry, daily cost summaries, per-tenant cost audit
emission. Formerly `extensions/CostModel.py`, `CostSummary.py`,
`CostSummaryService.py`, `CostAuditEmitter.py` directly under the
extensions root.

## Why this is an extension

Cost tracking is a business-domain concern (chargeback, billing,
finance reconstruction). A generic framework should not assume that
every deployment is monetized. Deployments without billing don't
register cost models, and the framework's rotation hot path skips
the cost-emission codepath gracefully.

## Optional dependents

- `quota` lists this as an optional dependency. USD-denominated caps
  (`TenantCostCap`) require billing's cost model. Plain
  call/token/byte/message/row caps don't.

## Layout

```
extensions/billing/
├── __init__.py
├── BLL_CostModel.py             # CostModel ABC, ConstantCostModel, TenantCostCap
├── BLL_CostSummary.py           # CostSummaryModel
├── BLL_CostSummaryService.py    # Daily/period roll-ups
├── BLL_CostAuditEmitter.py      # Emit per-call cost into the audit log
├── EXT_Billing.py
├── manifest.toml
└── migrations/versions/
```
