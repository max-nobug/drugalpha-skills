"""Build DCF operating assumptions from historical financials.

The core policy is absolute expense first, ratio second:
- Use historical expense amounts as the starting point for forward expenses.
- Let amounts converge gradually to mature operating-rate anchors.
- Convert forecast expense amounts back to rates versus DCF revenue.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any


OPERATING_RATE_KEYS = [
    "gross_margin",
    "sales_expense_rate",
    "rd_expense_rate",
    "admin_expense_rate",
    "tax_rate",
    "da_rate",
    "capex_rate",
    "nwc_rate",
]


@dataclass(frozen=True)
class CurvePolicy:
    sales_early_growth: float = 0.08
    rd_early_growth: float = 0.05
    admin_early_growth: float = 0.03
    early_years: int = 3
    convergence_years: int = 4
    mature_sales_expense_rate: float = 0.30
    mature_rd_expense_rate: float = 0.15
    mature_admin_expense_rate: float = 0.06
    max_sales_growth_during_convergence: float = 0.20
    max_rd_growth_during_convergence: float = 0.10
    max_admin_growth_during_convergence: float = 0.06
    max_sales_decline: float = 0.05
    max_rd_decline: float = 0.10
    max_admin_decline: float = 0.05
    da_growth: float = 0.03
    capex_growth: float = 0.03
    min_expense_rate: float = 0.02
    max_sales_expense_rate: float = 0.45
    max_sales_admin_expense_rate: float = 0.63
    max_rd_expense_rate: float = 0.55
    max_admin_expense_rate: float = 0.18
    mature_tax_rate: float = 0.15


def build_assumption_curves(
    history: list[dict[str, Any]],
    forecast_revenue: list[float],
    years: list[int],
    policy: CurvePolicy | None = None,
) -> dict[str, Any]:
    policy = policy or CurvePolicy()
    latest = _latest_row(history)
    revenue = [_safe_float(item) for item in forecast_revenue]

    gross_margin = _gross_margin_curve(latest, len(years))
    expense_mode = _sales_admin_expense_mode(latest)
    if expense_mode == "combined_sales_admin":
        # This disclosure cannot be reliably split. Keep one amount row, but
        # converge it to the sum of the sales and administrative mature anchors.
        sales_amount = _three_stage_expense_amount_curve(
            latest,
            "sales_admin_expense",
            revenue,
            policy.sales_early_growth,
            policy.mature_sales_expense_rate + policy.mature_admin_expense_rate,
            policy.early_years,
            policy.convergence_years,
            policy.max_sales_growth_during_convergence,
            policy.max_sales_decline,
            policy.min_expense_rate,
        )
        sales_rate = _rates_from_amounts(sales_amount, revenue, policy.min_expense_rate, policy.max_sales_admin_expense_rate)
        admin_amount = [0.0 for _ in years]
        admin_rate = [0.0 for _ in years]
    else:
        sales_amount = _three_stage_expense_amount_curve(
            latest, "sales_expense", revenue, policy.sales_early_growth,
            policy.mature_sales_expense_rate, policy.early_years, policy.convergence_years,
            policy.max_sales_growth_during_convergence, policy.max_sales_decline,
            policy.min_expense_rate,
        )
        admin_amount = _three_stage_expense_amount_curve(
            latest, "admin_expense", revenue, policy.admin_early_growth,
            policy.mature_admin_expense_rate, policy.early_years, policy.convergence_years,
            policy.max_admin_growth_during_convergence, policy.max_admin_decline,
            policy.min_expense_rate,
        )
        sales_rate = _rates_from_amounts(sales_amount, revenue, policy.min_expense_rate, policy.max_sales_expense_rate)
        admin_rate = _rates_from_amounts(admin_amount, revenue, policy.min_expense_rate, policy.max_admin_expense_rate)
    rd_amount = _three_stage_expense_amount_curve(
        latest, "rd_expense", revenue, policy.rd_early_growth,
        policy.mature_rd_expense_rate, policy.early_years, policy.convergence_years,
        policy.max_rd_growth_during_convergence, policy.max_rd_decline,
        policy.min_expense_rate,
    )
    rd_rate = _rates_from_amounts(rd_amount, revenue, policy.min_expense_rate, policy.max_rd_expense_rate)
    tax_rate = _tax_curve(latest, len(years), policy.mature_tax_rate)
    da_amount = _expense_amount_curve(
        latest, "depreciation_amortization", revenue, policy.da_growth, 0.0
    )
    da_rate = _expense_rate_curve(latest, "depreciation_amortization", revenue, policy.da_growth, 0.0, 0.08)
    capex_amount = _expense_amount_curve(latest, "capex", revenue, policy.capex_growth, 0.0)
    capex_rate = _expense_rate_curve(latest, "capex", revenue, policy.capex_growth, 0.0, 0.12)
    nwc_rate, nwc_metadata = _nwc_curve(history, len(years))
    nwc_amount = _nwc_amount_curve(revenue, nwc_rate)

    return {
        "source": "historical_absolute_expense_model",
        "operatingExpenseMode": expense_mode,
        "years": years,
        "curves": {
            "gross_margin": gross_margin,
            "sales_expense_rate": sales_rate,
            "rd_expense_rate": rd_rate,
            "admin_expense_rate": admin_rate,
            "tax_rate": tax_rate,
            "da_rate": da_rate,
            "capex_rate": capex_rate,
            "nwc_rate": nwc_rate,
        },
        "amountCurves": {
            "sales_expense": sales_amount,
            "rd_expense": rd_amount,
            "admin_expense": admin_amount,
            "da": da_amount,
            "capex": capex_amount,
            "nwc_change": nwc_amount,
        },
        "curveMetadata": {
            "nwc_rate": nwc_metadata,
            "operating_expenses": {
                "method": "three_stage_amount_convergence",
                "earlyYears": policy.early_years,
                "convergenceYears": policy.convergence_years,
                "matureRateAnchors": {
                    "sales": policy.mature_sales_expense_rate,
                    "rd": policy.mature_rd_expense_rate,
                    "admin": policy.mature_admin_expense_rate,
                },
            },
        },
        "methodNotes": [
            "销售/研发/管理费用采用三阶段金额曲线：前3年温和增长，中间4年向成熟期费用率锚点收敛，成熟期分别锚定销售30%、研发15%、管理6%；Excel中金额可编辑，费用率由金额除以 DCF 收入自动计算。",
            "如仅披露销售及管理费用合计，则直接按合计口径外推，不主观拆分销售与管理费用。",
            "历史费用率仅作为上下限和合理性校验，避免低收入年份费用率失真。",
            "税率从历史有效税率向成熟税率过渡，亏损年份不直接外推。",
            "营运资本变动以历史口径形成金额初稿，Excel中金额可编辑；占收入增量比例由金额除以收入增量自动计算。",
        ],
        "latestHistorical": latest,
        "historicalRows": history,
    }


def _latest_row(history: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in history if row.get("year")]
    if not rows:
        return {}
    return sorted(rows, key=lambda row: row.get("year"))[-1]


def _sales_admin_expense_mode(latest: dict[str, Any]) -> str:
    combined = _safe_float(latest.get("sales_admin_expense"))
    sales = _safe_float(latest.get("sales_expense"))
    admin = _safe_float(latest.get("admin_expense"))
    if combined is not None and combined > 0 and (sales is None or admin is None):
        return "combined_sales_admin"
    return "separate_sales_admin"


def _gross_margin_curve(latest: dict[str, Any], count: int) -> list[float]:
    revenue = _safe_float(latest.get("revenue"))
    cost = _safe_float(latest.get("cost"))
    if revenue and cost is not None:
        start = max(min((revenue - cost) / revenue, 0.98), 0.30)
    else:
        start = 0.88
    mature = min(max(start + 0.02, start), 0.92)
    return [_round_rate(start + (mature - start) * min(idx / max(count - 1, 1), 1)) for idx in range(count)]


def _rates_from_amounts(
    amounts: list[float],
    forecast_revenue: list[float],
    floor: float,
    cap: float,
) -> list[float]:
    rates = []
    for amount, revenue in zip(amounts, forecast_revenue):
        rate = amount / revenue if revenue and revenue > 0 else floor
        rates.append(_round_rate(min(max(rate, floor), cap)))
    return rates


def _three_stage_expense_amount_curve(
    latest: dict[str, Any],
    field: str,
    forecast_revenue: list[float],
    early_growth: float,
    mature_rate: float,
    early_years: int,
    convergence_years: int,
    max_growth: float,
    max_decline: float,
    fallback_rate: float,
) -> list[float]:
    base_amount = _safe_float(latest.get(field))
    if base_amount is None or base_amount <= 0:
        fallback = _safe_float(latest.get(f"{field}_rate"))
        effective_rate = max(fallback if fallback is not None else fallback_rate, 0.0)
        return [round(max(revenue or 0.0, 0.0) * effective_rate, 6) for revenue in forecast_revenue]

    amounts: list[float] = []
    previous = base_amount
    early_amount = base_amount * ((1 + early_growth) ** max(early_years, 0))
    for idx, revenue in enumerate(forecast_revenue):
        year_number = idx + 1
        if year_number <= early_years:
            desired = base_amount * ((1 + early_growth) ** year_number)
        else:
            target_amount = max(float(revenue or 0.0), 0.0) * mature_rate
            if year_number <= early_years + convergence_years:
                weight = (year_number - early_years) / max(convergence_years, 1)
                desired = early_amount * (1 - weight) + target_amount * weight
            else:
                desired = target_amount
            desired = min(desired, previous * (1 + max_growth))
        lower_bound = previous * (1 - max_decline)
        upper_bound = previous * (1 + (early_growth if year_number <= early_years else max_growth))
        amount = min(max(desired, lower_bound), upper_bound)
        amount = round(max(amount, 0.0), 6)
        amounts.append(amount)
        previous = amount
    return amounts


def _expense_amount_curve(
    latest: dict[str, Any],
    field: str,
    forecast_revenue: list[float],
    growth: float,
    fallback_rate: float,
) -> list[float]:
    """Keep D&A and capex on their existing historical-amount growth curves."""
    base_amount = _safe_float(latest.get(field))
    if base_amount is not None and base_amount > 0:
        return [round(base_amount * ((1 + growth) ** (idx + 1)), 6) for idx in range(len(forecast_revenue))]
    fallback = _safe_float(latest.get(f"{field}_rate"))
    effective_rate = max(fallback if fallback is not None else fallback_rate, 0.0)
    return [round(max(revenue or 0.0, 0.0) * effective_rate, 6) for revenue in forecast_revenue]


def _expense_rate_curve(
    latest: dict[str, Any],
    field: str,
    forecast_revenue: list[float],
    growth: float,
    floor: float,
    cap: float,
) -> list[float]:
    amounts = _expense_amount_curve(latest, field, forecast_revenue, growth, floor)
    return _rates_from_amounts(amounts, forecast_revenue, floor, cap)


def _nwc_amount_curve(forecast_revenue: list[float], rates: list[float]) -> list[float]:
    amounts: list[float] = []
    previous_revenue = 0.0
    for revenue, rate in zip(forecast_revenue, rates):
        current_revenue = revenue or 0.0
        amounts.append(round((current_revenue - previous_revenue) * rate, 6))
        previous_revenue = current_revenue
    return amounts


def _tax_curve(latest: dict[str, Any], count: int, mature_tax_rate: float) -> list[float]:
    profit = _safe_float(latest.get("profit_before_tax"))
    tax = _safe_float(latest.get("income_tax"))
    if profit and profit > 0 and tax is not None:
        start = min(max(tax / profit, 0), mature_tax_rate)
    else:
        start = 0.0
    return [_round_rate(start + (mature_tax_rate - start) * min(idx / 5, 1)) for idx in range(count)]


def _nwc_curve(history: list[dict[str, Any]], count: int) -> tuple[list[float], dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    rows = sorted((row for row in history if row.get("year")), key=lambda row: row.get("year"))
    for previous, current in zip(rows, rows[1:]):
        previous_revenue = _safe_float(previous.get("revenue"))
        current_revenue = _safe_float(current.get("revenue"))
        if previous_revenue is None or current_revenue is None:
            continue
        revenue_change = current_revenue - previous_revenue
        if revenue_change <= 0:
            continue

        capital_change = _safe_float(current.get("operating_capital_change"))
        source_field = "operating_capital_change"
        if capital_change is None:
            previous_capital = _safe_float(previous.get("operating_capital"))
            current_capital = _safe_float(current.get("operating_capital"))
            if previous_capital is not None and current_capital is not None:
                capital_change = current_capital - previous_capital
                source_field = "operating_capital_balance_difference"
        if capital_change is None:
            continue

        raw_rate = capital_change / revenue_change
        normalized_rate = min(max(raw_rate, -0.08), 0.08)
        observations.append(
            {
                "year": current.get("year"),
                "revenueChangeCny100m": round(revenue_change, 6),
                "operatingCapitalChangeCny100m": round(capital_change, 6),
                "rawRate": round(raw_rate, 6),
                "normalizedRate": _round_rate(normalized_rate),
                "sourceField": source_field,
            }
        )

    if not observations:
        fallback = ([0.00, 0.02, 0.03, 0.04, 0.05] + [0.05] * max(count - 5, 0))[:count]
        return [_round_rate(value) for value in fallback], {
            "method": "commercialization_ramp_fallback",
            "matureRate": 0.05,
            "historicalObservations": [],
        }

    start_rate = float(median(item["normalizedRate"] for item in observations[-3:]))
    mature_rate = 0.05
    curve = [
        _round_rate(start_rate + (mature_rate - start_rate) * min(index / 4, 1))
        for index in range(count)
    ]
    return curve, {
        "method": "historical_operating_capital",
        "startRate": _round_rate(start_rate),
        "matureRate": mature_rate,
        "rateBounds": [-0.08, 0.08],
        "historicalObservations": observations,
    }


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_rate(value: float) -> float:
    return round(value, 4)
