"""Shared single-drug / single-indication / single-market gross-sales engine.

All money is expressed in local-currency 100m: patient count is 10k people and
monthly price is 10k local currency.  This deliberately excludes POS, equity,
revenue sharing, FX translation, company costs and discounting.
"""

from __future__ import annotations

from typing import Any

from .sales_ramp import sales_ramp_factor


def calculate_patient_flow_gross_sales(
    *,
    patient_flow: dict[str, Any],
    years: list[int],
    penetration_multiplier: float = 1.0,
    price_multiplier: float = 1.0,
    revenue_realization_multiplier: float = 1.0,
) -> dict[str, Any] | None:
    """Return unadjusted annual sales and patient funnel, or ``None`` if incomplete."""
    annual_patients = patient_flow.get("annual_eligible_patients_10k")
    shares = patient_flow.get("product_share_curve")
    base_patients = _number(patient_flow.get("base_patient_count_10k"))
    growth_rate = _number(patient_flow.get("patient_growth_rate"))
    biomarker_ratio = _ratio(patient_flow.get("biomarker_ratio", 1.0))
    target_ratio = _ratio(patient_flow.get("target_treatment_ratio", 1.0))
    penetrations = patient_flow.get("penetration_curve") or []
    monthly_prices = patient_flow.get("monthly_price") or []
    treatment_months = patient_flow.get("treatment_months") or []
    if None in (base_patients, growth_rate, biomarker_ratio, target_ratio):
        return None
    if annual_patients is not None and (len(annual_patients) != len(years) or shares is None or len(shares) != len(years)):
        return None
    if min(len(penetrations), len(monthly_prices), len(treatment_months)) < len(years):
        return None

    sales: list[float] = []
    funnel: list[dict[str, float]] = []
    for index, year in enumerate(years):
        penetration = _ratio(penetrations[index])
        price = _number(monthly_prices[index])
        duration = _number(treatment_months[index])
        share = _ratio(shares[index]) if shares is not None else 1.0
        if None in (penetration, price, duration, share):
            return None
        population = base_patients * ((1 + growth_rate) ** index)
        eligible = _number(annual_patients[index]) if annual_patients is not None else population * biomarker_ratio * target_ratio
        if eligible is None:
            return None
        if annual_patients is not None:
            population = eligible
            if year < patient_flow["launch_year"]:
                penetration = 0.0
        applied_penetration = _ratio(penetration * penetration_multiplier)
        applied_price = price * max(price_multiplier, 0)
        treated = eligible * applied_penetration * share
        sales.append(treated * applied_price * duration * max(revenue_realization_multiplier, 0))
        funnel.append({
            "year": year,
            "patientCount10k": population,
            "eligiblePatients10k": eligible,
            "treatedPatients10k": treated,
            "penetration": applied_penetration,
            "monthlyPrice10k": applied_price,
            "treatmentMonths": duration,
        })
    return {"grossSales100m": sales, "patientFunnel": funnel}


def build_single_market_patient_flow(
    *,
    annual_patients: float,
    biomarker_ratio: float,
    target_treatment_ratio: float,
    peak_penetration: float,
    monthly_treatment_cost: float,
    treatment_months: float,
    years: list[int],
    launch_year: int | None,
    peak_year: int | None,
    market: str,
    patient_growth_rate: float = 0.0,
    price_curve_multiplier: list[float] | None = None,
) -> dict[str, Any]:
    """Normalize UI/API inputs into the DCF patient-flow input contract."""
    prices = price_curve_multiplier or [1.0] * len(years)
    return {
        "base_patient_count_10k": annual_patients / 10000,
        "patient_growth_rate": patient_growth_rate,
        "biomarker_ratio": biomarker_ratio,
        "target_treatment_ratio": target_treatment_ratio,
        "penetration_curve": [
            peak_penetration * sales_ramp_factor(year, launch_year=launch_year, peak_year=peak_year, market=market)
            for year in years
        ],
        "monthly_price": [monthly_treatment_cost / 10000 * float(prices[index]) for index in range(len(years))],
        "treatment_months": [treatment_months] * len(years),
    }


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _ratio(value: Any) -> float | None:
    number = _number(value)
    return None if number is None else min(max(number, 0.0), 1.0)
