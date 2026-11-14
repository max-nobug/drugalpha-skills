"""Allocate a product-level peak-sales anchor by indication market space."""

from __future__ import annotations

from statistics import median
from typing import Any


def calculate_market_space(
    *,
    base_patient_count_10k: Any,
    patient_growth_rate: Any,
    biomarker_ratio: Any,
    target_treatment_ratio: Any,
    monthly_price: Any,
    treatment_months: Any,
    peak_year: Any,
    forecast_years: list[int],
) -> float | None:
    """Return gross addressable market space in 100m local-currency units."""

    base_patients = _positive_number(base_patient_count_10k)
    biomarker = _bounded_ratio(biomarker_ratio)
    target_ratio = _bounded_ratio(target_treatment_ratio)
    if base_patients is None or biomarker is None or target_ratio is None or not forecast_years:
        return None
    try:
        peak = int(peak_year)
    except (TypeError, ValueError):
        return None
    growth = _number(patient_growth_rate, default=0.0)
    peak_index = max(peak - forecast_years[0], 0)
    price = _series_value(monthly_price, peak_index)
    duration = _series_value(treatment_months, peak_index)
    if price is None or price <= 0 or duration is None or duration <= 0:
        return None
    peak_patients = base_patients * ((1 + growth) ** peak_index)
    return round(peak_patients * biomarker * target_ratio * price * duration, 6)


def allocate_market_space_weights(spaces: list[float | None]) -> tuple[list[float], str]:
    """Normalize market spaces without introducing phase/stage coefficients."""

    if not spaces:
        return [], "no_indications"
    positives = [float(value) for value in spaces if isinstance(value, (int, float)) and value > 0]
    if not positives:
        equal = 1.0 / len(spaces)
        return [equal for _ in spaces], "equal_fallback_all_market_space_missing"
    neutral = float(median(positives))
    used_fallback = False
    effective: list[float] = []
    for value in spaces:
        if isinstance(value, (int, float)) and value > 0:
            effective.append(float(value))
        else:
            effective.append(neutral)
            used_fallback = True
    total = sum(effective)
    method = "market_space_with_median_fallback" if used_fallback else "market_space_only"
    return [value / total for value in effective], method


def market_space_from_flow(
    flow: dict[str, Any],
    *,
    peak_year: Any,
    forecast_years: list[int],
) -> float | None:
    return calculate_market_space(
        base_patient_count_10k=flow.get("base_patient_count_10k"),
        patient_growth_rate=flow.get("patient_growth_rate"),
        biomarker_ratio=flow.get("biomarker_ratio"),
        target_treatment_ratio=flow.get("target_treatment_ratio"),
        monthly_price=flow.get("monthly_price"),
        treatment_months=flow.get("treatment_months"),
        peak_year=peak_year,
        forecast_years=forecast_years,
    )


def _series_value(value: Any, index: int) -> float | None:
    if isinstance(value, list):
        if not value:
            return None
        raw = value[min(max(index, 0), len(value) - 1)]
    else:
        raw = value
    return _positive_number(raw)


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _bounded_ratio(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(number, 0.0), 1.0)


def _number(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
