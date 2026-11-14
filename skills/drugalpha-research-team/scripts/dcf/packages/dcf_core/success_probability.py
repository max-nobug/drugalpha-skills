"""Resolve domestic and overseas launch success probabilities for DCF."""

from __future__ import annotations

import re
from typing import Any

from .phase_rules import is_marketed_phase, is_nda_bla_phase


DOMESTIC_PHASE_POS = {
    "上市": 1.00,
    "NDA_BLA": 0.95,
    "PHASE_3": 0.90,
    "PHASE_2": 0.80,
}


def phase_success_probability(phase: Any) -> float | None:
    """Map the current development phase to the user-approved China POS table."""

    value = re.sub(r"[\s_-]+", "", str(phase or "")).upper()
    if not value:
        return None
    if is_marketed_phase(value):
        return DOMESTIC_PHASE_POS["上市"]
    if is_nda_bla_phase(value):
        return DOMESTIC_PHASE_POS["NDA_BLA"]
    if (
        re.search(r"(^|[^0-9])3期($|[^0-9])", value)
        or "III期" in value
        or "PHASE3" in value
        or "PHASEIII" in value
    ):
        return DOMESTIC_PHASE_POS["PHASE_3"]
    if (
        re.search(r"(^|[^0-9])2期($|[^0-9])", value)
        or "II期" in value
        or "PHASE2" in value
        or "PHASEII" in value
        or "1/2期" in value
        or "I/II期" in value
    ):
        return DOMESTIC_PHASE_POS["PHASE_2"]
    return None


def resolve_market_success_probability(
    *,
    market: dict[str, Any],
    indication_pos: Any,
    indication_phase: Any = None,
) -> tuple[float, str]:
    """Use valuation-tracking POS overseas and phase POS in China/fallbacks."""

    if market.get("revenue_input_sources"):
        return market["success_probability"], "independent_research"
    current_phase_pos = phase_success_probability(indication_phase)
    stored_pos = _normalized_probability(indication_pos)
    if market.get("market") == "us":
        valuation_pos = _normalized_probability(market.get("success_probability"))
        if valuation_pos is not None:
            return valuation_pos, "valuation_tracking_overseas_success_probability"
        if current_phase_pos is not None:
            return current_phase_pos, "phase_fallback_overseas_success_probability_missing"
        if stored_pos is not None:
            return stored_pos, "phase_fallback_overseas_success_probability_missing"
        return 1.0, "default_overseas_success_probability_missing"
    if current_phase_pos is not None:
        return current_phase_pos, "domestic_phase_table"
    if stored_pos is not None:
        return stored_pos, "domestic_phase_table"
    return 1.0, "default_domestic_phase_unrecognized"


def success_probability_source_label(source: str) -> str:
    labels = {
        "valuation_tracking_overseas_success_probability": "估值跟踪海外上市成功率",
        "phase_fallback_overseas_success_probability_missing": "估值跟踪缺失，按研发阶段回退",
        "default_overseas_success_probability_missing": "估值跟踪及阶段均缺失，暂按100%占位",
        "domestic_phase_table": "国内阶段固定表",
        "independent_research": "独立研究输入",
        "default_domestic_phase_unrecognized": "阶段未识别，暂按100%占位",
    }
    return labels.get(source, source or "来源待确认")


def _normalized_probability(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 1 < number <= 100:
        number /= 100
    if not 0 <= number <= 1:
        return None
    return number
