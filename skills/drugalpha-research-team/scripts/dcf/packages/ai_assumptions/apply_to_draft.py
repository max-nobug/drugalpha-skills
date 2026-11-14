"""Apply approved AI-prefill proposals to a DCF-owned draft copy.

The module deliberately keeps DrugAlpha source facts immutable.  It only
updates fields classified as ``ai_prefill`` and then calibrates penetration
against an existing product peak-sales anchor before valuation is calculated.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .field_policy import AI_PREFILL, get_policy
from packages.dcf_core.launch_timing import (
    extract_status_start_date_from_identity,
    resolve_china_launch_year,
)
from packages.dcf_core.peak_allocation import (
    allocate_market_space_weights,
    market_space_from_flow,
)
from packages.dcf_core.phase_rules import is_marketed_phase
from packages.dcf_core.sales_ramp import penetration_curve_from_peak, resolve_peak_year


def apply_ai_prefill_to_draft(
    draft: dict[str, Any],
    ai_assumptions: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an enriched DCF draft and an auditable application summary."""

    enriched = deepcopy(draft)
    timing_repair = repair_stale_market_timing(enriched)
    lookup = _proposal_lookup(ai_assumptions)
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for product in enriched.get("products") or []:
        if product.get("modeling_mode") == "simplified_growth":
            continue
        for indication in product.get("indications") or []:
            for market in indication.get("markets") or []:
                flow = market.setdefault("patient_flow", {})
                for field in _FLOW_FIELDS:
                    proposal = lookup.get(
                        _proposal_key(
                            product.get("drug_name"),
                            indication.get("indication_text"),
                            market.get("market"),
                            field,
                        )
                    )
                    if not proposal:
                        continue
                    if get_policy(field).mode != AI_PREFILL or proposal.get("aiMode") not in (None, AI_PREFILL):
                        rejected.append(_summary_item(product, indication, market, field, "policy_rejected"))
                        continue
                    if field == "biomarker_ratio" and (
                        not flow.get("biomarker_ratio_requires_review")
                        or not _has_http_source_urls(proposal.get("sourceUrls"))
                    ):
                        rejected.append(_summary_item(product, indication, market, field, "biomarker_source_or_review_required"))
                        continue
                    value = _proposal_value(proposal)
                    if value is None:
                        rejected.append(_summary_item(product, indication, market, field, "missing_value"))
                        continue
                    if field == "peak_sales_anchor":
                        if _positive_number(market.get("peak_sales_anchor")) is not None:
                            rejected.append(_summary_item(product, indication, market, field, "source_anchor_preserved"))
                            continue
                        market["peak_sales_anchor"] = value
                    else:
                        flow[field] = _fit_curve(value, len(enriched.get("forecast_years") or []))
                    if field == "biomarker_ratio":
                        flow["biomarker_ratio_source"] = "DrugAlpha AI联网检索公开Biomarker比例"
                        flow["biomarker_ratio_requires_review"] = True
                        flow["biomarker_ratio_source_details"] = [
                            {"label": "DrugAlpha AI联网检索", "url": url}
                            for url in proposal.get("sourceUrls") or []
                        ]
                    flow.setdefault("ai_prefill_provenance", {})[field] = {
                        "basisComment": proposal.get("basisComment"),
                        "confidence": proposal.get("confidence"),
                        "needsHumanReview": bool(proposal.get("needsHumanReview", True)),
                        "sourceUrls": proposal.get("sourceUrls") or [],
                        **({"researchProvider": "user_agent"} if proposal.get("researchProvider") == "user_agent" else {}),
                    }
                    applied.append(_summary_item(product, indication, market, field, "applied"))

    rule_prefill = prefill_missing_patient_counts_from_peak_anchors(enriched)
    calibration = calibrate_penetration_to_peak_anchors(enriched)
    enriched["ai_prefill_application"] = {
        "appliedCount": len(applied),
        "rejectedCount": len(rejected),
        "applied": applied,
        "rejected": rejected,
        "timingRepair": timing_repair,
        "rulePatientPrefill": rule_prefill,
        "peakAnchorCalibration": calibration,
    }
    return enriched, enriched["ai_prefill_application"]


def repair_stale_market_timing(draft: dict[str, Any]) -> dict[str, Any]:
    """Refresh marketed China timing when a recoverable actual date exists.

    This is deliberately limited to facts already embedded in the DCF draft;
    it never writes to or guesses values for the upstream pipeline module.
    """

    repaired: list[dict[str, Any]] = []
    for product in draft.get("products") or []:
        for indication in product.get("indications") or []:
            if not is_marketed_phase(indication.get("phase")):
                continue
            recovered_date = _recover_status_start_date(indication)
            if not recovered_date:
                continue
            launch_year, launch_basis = resolve_china_launch_year(
                phase=indication.get("phase"),
                status_start_date=recovered_date,
                indication_text=indication.get("indication_text"),
            )
            peak_year, peak_basis = resolve_peak_year(
                launch_year=launch_year,
                market="china",
            )
            before = {
                "statusStartDate": indication.get("status_start_date"),
                "launchYear": indication.get("launch_year"),
                "peakYear": indication.get("peak_year"),
            }
            indication["status_start_date"] = recovered_date
            indication["launch_year"] = launch_year
            indication["launch_year_basis"] = launch_basis
            indication["peak_year"] = peak_year
            indication["peak_year_basis"] = peak_basis
            for market in indication.get("markets") or []:
                if market.get("market") != "china":
                    continue
                market["launch_year"] = launch_year
                market["launch_year_basis"] = launch_basis
                market["peak_year"] = peak_year
                market["peak_year_basis"] = peak_basis
            after = {
                "statusStartDate": recovered_date,
                "launchYear": launch_year,
                "peakYear": peak_year,
            }
            if before != after:
                repaired.append({
                    "drugName": product.get("drug_name"),
                    "indication": indication.get("indication_text"),
                    "before": before,
                    "after": after,
                })
    return {"repairedCount": len(repaired), "repaired": repaired}


def _recover_status_start_date(indication: dict[str, Any]) -> str | None:
    direct = indication.get("status_start_date")
    if direct:
        return str(direct)
    identities = [indication.get("source_row_identity")]
    for evidence in indication.get("trial_evidence") or []:
        status_date = evidence.get("status_start_date") or evidence.get("statusStartDate")
        if status_date and is_marketed_phase(evidence.get("phase") or indication.get("phase")):
            return str(status_date)
        identities.extend((evidence.get("source_row_identity"), evidence.get("sourceRowIdentity")))
    for identity in identities:
        recovered = extract_status_start_date_from_identity(identity)
        if recovered:
            return recovered
    return None


def prefill_missing_patient_counts_from_peak_anchors(draft: dict[str, Any]) -> dict[str, Any]:
    """Keep patient counts non-empty when AI has no usable proposal.

    The fallback stays inside the DCF draft and is explicitly labelled.  It
    uses a neutral 5% peak penetration solely to derive a starting patient
    population; the next calibration pass then solves penetration against the
    same allocated product anchor.
    """

    years = draft.get("forecast_years") or []
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if not years:
        return {"appliedCount": 0, "skippedCount": 0, "applied": [], "skipped": []}

    for product in draft.get("products") or []:
        if product.get("modeling_mode") == "simplified_growth":
            continue
        for market_name in ("china", "us"):
            entries = [
                (indication, market)
                for indication in product.get("indications") or []
                for market in indication.get("markets") or []
                if market.get("market") == market_name
            ]
            if not entries:
                continue
            product_anchor = next(
                (
                    value
                    for _, market in entries
                    if (value := _positive_number(market.get("peak_sales_anchor"))) is not None
                ),
                None,
            )
            if product_anchor is None:
                continue
            initial_spaces = [
                market_space_from_flow(
                    market.get("patient_flow") or {},
                    peak_year=market.get("peak_year") or indication.get("peak_year"),
                    forecast_years=years,
                )
                for indication, market in entries
            ]
            weights, method = allocate_market_space_weights(initial_spaces)
            for (indication, market), weight in zip(entries, weights):
                flow = market.setdefault("patient_flow", {})
                if _positive_number(flow.get("base_patient_count_10k")) is not None:
                    continue
                peak_year = market.get("peak_year") or indication.get("peak_year")
                if not isinstance(peak_year, int):
                    skipped.append(_calibration_item(product, indication, market, "missing_peak_year"))
                    continue
                peak_index = max(peak_year - years[0], 0)
                price = _series_positive_value(flow.get("monthly_price"), peak_index)
                duration = _series_positive_value(flow.get("treatment_months"), peak_index)
                biomarker = _bounded_ratio(flow.get("biomarker_ratio"), default=1.0)
                target = _bounded_ratio(flow.get("target_treatment_ratio"), default=1.0)
                growth = _number(flow.get("patient_growth_rate"), default=0.0)
                if price is None or duration is None or biomarker <= 0 or target <= 0:
                    skipped.append(_calibration_item(product, indication, market, "missing_price_duration_or_ratio"))
                    continue
                allocated_anchor = product_anchor * weight
                peak_patients = allocated_anchor / (price * duration * biomarker * target * 0.05)
                base_patients = peak_patients / ((1 + growth) ** peak_index)
                flow["base_patient_count_10k"] = round(max(base_patients, 0.01), 4)
                flow["base_patient_source"] = "peak_anchor_rule_fallback"
                flow["base_patient_source_detail"] = "AI未返回有效患者规模，按峰值锚点和5%基准峰值渗透率反推；需人工复核。"
                flow["requires_user_input"] = True
                market["peak_sales_allocation_weight"] = round(weight, 8)
                market["allocated_peak_sales_anchor"] = round(allocated_anchor, 6)
                market["peak_sales_allocation_method"] = method
                applied.append({
                    **_calibration_item(product, indication, market, "rule_prefilled"),
                    "basePatientCount10k": flow["base_patient_count_10k"],
                    "allocatedPeakSalesAnchor": round(allocated_anchor, 6),
                })

    return {
        "appliedCount": len(applied),
        "skippedCount": len(skipped),
        "applied": applied,
        "skipped": skipped,
    }


def calibrate_penetration_to_peak_anchors(draft: dict[str, Any]) -> dict[str, Any]:
    """Backsolve peak penetration so gross, pre-POS sales reconcile to anchors."""

    years = draft.get("forecast_years") or []
    calibrated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if not years:
        return {"calibratedCount": 0, "skippedCount": 0, "calibrated": [], "skipped": []}

    for product in draft.get("products") or []:
        if product.get("modeling_mode") == "simplified_growth":
            continue
        for market_name in ("china", "us"):
            entries = [
                (indication, market)
                for indication in product.get("indications") or []
                for market in indication.get("markets") or []
                if market.get("market") == market_name
            ]
            if not entries:
                continue
            anchors = [_positive_number(market.get("peak_sales_anchor")) for _, market in entries]
            product_anchor = next((value for value in anchors if value is not None), None)
            if product_anchor is None:
                continue
            spaces = [
                market_space_from_flow(
                    market.get("patient_flow") or {},
                    peak_year=market.get("peak_year") or indication.get("peak_year"),
                    forecast_years=years,
                )
                for indication, market in entries
            ]
            weights, allocation_method = allocate_market_space_weights(spaces)
            for (indication, market), capacity, weight in zip(entries, spaces, weights):
                allocated_anchor = product_anchor * weight
                flow = market.setdefault("patient_flow", {})
                launch_year = market.get("launch_year") or indication.get("launch_year")
                peak_year = market.get("peak_year") or indication.get("peak_year")
                if not isinstance(capacity, (int, float)) or capacity <= 0:
                    skipped.append(_calibration_item(product, indication, market, "missing_patient_flow_capacity"))
                    continue
                if not isinstance(launch_year, int) or not isinstance(peak_year, int):
                    skipped.append(_calibration_item(product, indication, market, "missing_launch_or_peak_year"))
                    continue
                required_penetration = allocated_anchor / capacity
                applied_penetration = min(max(required_penetration, 0.0), 1.0)
                reachable = required_penetration <= 1.0
                flow["penetration_curve"] = penetration_curve_from_peak(
                    years=years,
                    peak_penetration=applied_penetration,
                    launch_year=launch_year,
                    peak_year=peak_year,
                    market=market_name,
                )
                flow["penetration_source"] = "valuation_tracking_peak_anchor_backsolve"
                flow["penetration_requires_review"] = not reachable
                calibration_payload = {
                    "method": "peak_anchor_backsolve_pre_pos_pre_equity",
                    "productPeakSalesAnchor": round(product_anchor, 6),
                    "allocationWeight": round(weight, 8),
                    "allocationMethod": allocation_method,
                    "allocatedPeakSalesAnchor": round(allocated_anchor, 6),
                    "marketSpaceCapacity100m": round(float(capacity), 6),
                    "requiredPeakPenetration": round(required_penetration, 8),
                    "appliedPeakPenetration": round(applied_penetration, 8),
                    "reachable": reachable,
                }
                market["peak_anchor_calibration"] = calibration_payload
                market["market_space_capacity_100m"] = round(float(capacity), 6)
                market["peak_sales_allocation_weight"] = round(weight, 8)
                market["allocated_peak_sales_anchor"] = round(allocated_anchor, 6)
                market["peak_sales_allocation_method"] = allocation_method
                calibrated.append(
                    {
                        **_calibration_item(product, indication, market, "calibrated"),
                        **calibration_payload,
                    }
                )

    return {
        "calibratedCount": len(calibrated),
        "skippedCount": len(skipped),
        "calibrated": calibrated,
        "skipped": skipped,
    }


_FLOW_FIELDS = (
    "base_patient_count_10k",
    "biomarker_ratio",
    "target_treatment_ratio",
    "patient_growth_rate",
    "population_filters",
    "penetration_curve",
    "monthly_price",
    "treatment_months",
    "peak_sales_anchor",
)


def _proposal_lookup(payload: dict[str, Any] | None) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in (payload or {}).get("proposals") or []:
        key = _proposal_key(item.get("drugName"), item.get("indication"), item.get("market"), item.get("field"))
        if all(key):
            result.setdefault(key, item)
    return result


def _proposal_key(drug: Any, indication: Any, market: Any, field: Any) -> tuple[str, str, str, str]:
    return tuple(_normalize(value) for value in (drug, indication, market, field))


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _proposal_value(proposal: dict[str, Any]) -> Any:
    curve = proposal.get("suggestedCurve")
    if isinstance(curve, list) and curve:
        return curve
    value = proposal.get("suggestedValue")
    if isinstance(value, bool):
        return value
    return value if isinstance(value, (int, float, list, dict)) else None


def _has_http_source_urls(value: Any) -> bool:
    return isinstance(value, list) and any(
        isinstance(url, str) and url.strip().startswith(("https://", "http://"))
        for url in value
    )


def _fit_curve(value: Any, length: int) -> Any:
    if not isinstance(value, list) or length <= 0:
        return value
    if not value:
        return value
    return [value[min(index, len(value) - 1)] for index in range(length)]


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _series_positive_value(value: Any, index: int) -> float | None:
    if isinstance(value, list):
        if not value:
            return None
        value = value[min(max(index, 0), len(value) - 1)]
    return _positive_number(value)


def _bounded_ratio(value: Any, *, default: float) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return default


def _number(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _summary_item(product: dict[str, Any], indication: dict[str, Any], market: dict[str, Any], field: str, status: str) -> dict[str, Any]:
    return {
        "drugName": product.get("drug_name"),
        "indication": indication.get("indication_text"),
        "market": market.get("market"),
        "field": field,
        "status": status,
    }


def _calibration_item(product: dict[str, Any], indication: dict[str, Any], market: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "drugName": product.get("drug_name"),
        "indication": indication.get("indication_text"),
        "market": market.get("market"),
        "status": status,
    }
