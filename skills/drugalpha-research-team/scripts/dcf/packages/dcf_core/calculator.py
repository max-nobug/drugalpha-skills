"""MVP patient-flow DCF calculations.

The calculator prefers bottom-up patient-flow sales when a market has enough
inputs. If patient count, penetration, price, or treatment duration is missing,
it falls back to a DrugAlpha peak-sales anchor curve and labels the method in
the result payload.
"""

from __future__ import annotations

from typing import Any

from .peak_allocation import (
    allocate_market_space_weights,
    market_space_from_flow,
)
from .sales_ramp import ramp_description, ramp_profile_id, sales_ramp_factor
from .shared_patient_flow_sales import calculate_patient_flow_gross_sales
from .success_probability import phase_success_probability, resolve_market_success_probability


def calculate_draft_model(draft: dict[str, Any]) -> dict[str, Any]:
    years = draft["forecast_years"]
    scenario_results = []
    for scenario in draft.get("scenarios") or []:
        scenario_results.append(_calculate_scenario(draft, scenario, years))

    return {
        "modelType": "patient_flow_dcf_valuation",
        "sourceSnapshotId": draft.get("source_snapshot_id"),
        "companyKey": draft.get("company_key"),
        "companyName": draft.get("company_name"),
        "forecastYears": years,
        "scenarioResults": scenario_results,
        "disclaimer": draft.get("disclaimer"),
    }


def _calculate_scenario(
    draft: dict[str, Any],
    scenario: dict[str, Any],
    years: list[int],
) -> dict[str, Any]:
    product_results = [
        _calculate_product(product, scenario, years)
        for product in draft.get("products") or []
        if product.get("included_in_valuation")
    ]

    company_revenue = _sum_series([item["totalRevenueCny100m"] for item in product_results], years)
    company_fcf = _sum_series([item["freeCashFlowCny100m"] for item in product_results], years)
    dcf = _dcf(company_fcf, scenario, years)

    return {
        "scenario": scenario["name"],
        "assumptions": scenario,
        "productResults": product_results,
        "companyRevenueCny100m": company_revenue,
        "companyFreeCashFlowCny100m": company_fcf,
        "companyDcf": dcf,
    }


def _calculate_product(
    product: dict[str, Any],
    scenario: dict[str, Any],
    years: list[int],
) -> dict[str, Any]:
    if product.get("modeling_mode") == "simplified_growth":
        return _calculate_simplified_product(product, scenario, years)

    peak_allocations = {} if product.get("independent_revenue") else _product_peak_allocations(product, years)
    indication_results = [
        _calculate_indication(product, indication, scenario, years, peak_allocations)
        for indication in product.get("indications") or []
    ]
    total_revenue = _sum_series([item["totalRevenueCny100m"] for item in indication_results], years)
    fcf = _free_cash_flow(total_revenue, scenario)
    dcf = _dcf(fcf, scenario, years)
    return {
        "productId": product.get("product_id"),
        "drugName": product.get("drug_name"),
        "target": product.get("target"),
        "domesticPeakSalesCny100m": product.get("domestic_peak_sales_cny_100m"),
        "overseasPeakSalesUsd100m": product.get("overseas_peak_sales_usd_100m"),
        "indicationResults": indication_results,
        "peakAnchorReconciliation": _product_peak_anchor_reconciliation(indication_results),
        "totalRevenueCny100m": total_revenue,
        "freeCashFlowCny100m": fcf,
        "dcf": dcf,
    }


def _calculate_simplified_product(
    product: dict[str, Any],
    scenario: dict[str, Any],
    years: list[int],
) -> dict[str, Any]:
    """Calculate one product-level curve per market without patient flow."""

    market_results = []
    for market_name in ("china", "us"):
        peak_anchor = (
            product.get("domestic_peak_sales_cny_100m")
            if market_name == "china"
            else product.get("overseas_peak_sales_usd_100m")
        )
        if not isinstance(peak_anchor, (int, float)) or isinstance(peak_anchor, bool) or peak_anchor <= 0:
            continue

        candidates = [
            (indication, market)
            for indication in product.get("indications") or []
            for market in indication.get("markets") or []
            if market.get("market") == market_name
        ]
        launch_years = [
            market.get("launch_year") or indication.get("launch_year")
            for indication, market in candidates
            if isinstance(market.get("launch_year") or indication.get("launch_year"), int)
        ]
        peak_years = [
            market.get("peak_year") or indication.get("peak_year")
            for indication, market in candidates
            if isinstance(market.get("peak_year") or indication.get("peak_year"), int)
        ]
        launch_year = min(launch_years) if launch_years else None
        launch_year_source = "pipeline_timing" if launch_year is not None else "forecast_start_fallback"
        if launch_year is None and years:
            # A valuation-only simplified product can lack a matched pipeline row.
            # Keep it visible and editable instead of silently producing an all-zero
            # curve; the fallback is explicitly surfaced in the workbook for review.
            launch_year = years[0]
        peak_year = min(peak_years) if peak_years else None
        if launch_year is not None and (peak_year is None or peak_year < launch_year):
            peak_year = launch_year + 7
        launch_year = (
            launch_year + scenario.get("launch_delay_years", 0)
            if isinstance(launch_year, int)
            else None
        )
        peak_year = (
            peak_year + scenario.get("launch_delay_years", 0)
            if isinstance(peak_year, int)
            else None
        )

        current_phase_probabilities = [
            (phase_success_probability(indication.get("phase")), indication.get("phase"))
            for indication, _market in candidates
        ]
        current_phase_probabilities = [
            item for item in current_phase_probabilities if item[0] is not None
        ]
        if current_phase_probabilities:
            indication_pos, indication_phase = max(current_phase_probabilities, key=lambda item: item[0])
        else:
            stored_probabilities = [
                indication.get("pos")
                for indication, _market in candidates
                if isinstance(indication.get("pos"), (int, float))
            ]
            indication_pos = max(stored_probabilities) if stored_probabilities else None
            indication_phase = next(
                (indication.get("phase") for indication, _market in candidates if indication.get("phase")),
                None,
            )
        economics = {
            "market": market_name,
            "equity_ratio": product.get("domestic_equity_ratio"),
            "revenue_share_ratio": product.get("overseas_revenue_share_ratio"),
            "success_probability": product.get("overseas_success_probability"),
            "exchange_rate": product.get("exchange_rate"),
        }
        gross_sales = [
            _sales_curve_value(
                year,
                launch_year,
                peak_year,
                float(peak_anchor),
                scenario,
                market=market_name,
            )
            for year in years
        ]
        adjusted_revenue = _apply_market_economics(
            gross_sales,
            economics,
            {"pos": indication_pos, "phase": indication_phase},
        )
        scenario_peak = (
            float(peak_anchor)
            * _scenario_revenue_realization(scenario)
        )
        adjusted_peak_revenue = _apply_market_economics(
            [scenario_peak],
            economics,
            {"pos": indication_pos, "phase": indication_phase},
        )[0]
        adjusted_revenue, baseline = _apply_simplified_actual_sales_if_available(
            product=product,
            market_name=market_name,
            years=years,
            launch_year=launch_year,
            peak_year=peak_year,
            adjusted_peak_revenue=adjusted_peak_revenue,
            fallback_revenue=adjusted_revenue,
        )
        editable_base_year = next(
            (year for year, value in zip(years, adjusted_revenue) if value > 0),
            None,
        )
        editable_base_revenue = (
            adjusted_revenue[years.index(editable_base_year)]
            if editable_base_year in years
            else None
        )
        effective_pos, pos_source = resolve_market_success_probability(
            market=economics,
            indication_pos=indication_pos,
            indication_phase=indication_phase,
        )
        market_results.append(
            {
                "market": market_name,
                "currency": "CNY" if market_name == "china" else "USD",
                "launchYear": launch_year,
                "launchYearSource": launch_year_source,
                "peakYear": peak_year,
                "peakSalesAnchor": peak_anchor,
                "peakSalesAnchorUnit": "CNY_100m" if market_name == "china" else "USD_100m",
                "successProbability": round(effective_pos, 4),
                "successProbabilitySource": pos_source,
                "grossSales": _round_series(gross_sales),
                "equityAdjustedRevenueCny100m": _round_series(adjusted_revenue),
                "adjustedPeakRevenueCny100m": round(adjusted_peak_revenue, 4),
                "baselineYear": baseline["year"],
                "baselineRevenueCny100m": baseline["revenue"],
                "baselineSource": baseline["source"],
                "baselineSourceLabel": baseline["sourceLabel"],
                "baselineSourceDetail": baseline["sourceDetail"],
                "editableBaseYear": editable_base_year,
                "editableBaseRevenueCny100m": (
                    round(editable_base_revenue, 4)
                    if isinstance(editable_base_revenue, (int, float))
                    else None
                ),
                "defaultGrowthRates": _growth_rates(adjusted_revenue),
                "method": "simplified_product_growth",
                "methodNote": (
                    "不做患者流拆分；单品历史销售可用时优先衔接实际值，否则以产品级"
                    "DrugAlpha峰值销售和上市时间反推规则预填收入。Excel中首个有效年度"
                    "收入及后续逐年增速可编辑，收入不超过风险/权益调整后的峰值上限。"
                ),
            }
        )

    total_revenue = _sum_series(
        [item["equityAdjustedRevenueCny100m"] for item in market_results],
        years,
    )
    fcf = _free_cash_flow(total_revenue, scenario)
    return {
        "productId": product.get("product_id"),
        "drugName": product.get("drug_name"),
        "target": product.get("target"),
        "modelingMode": "simplified_growth",
        "modelingReason": product.get("modeling_reason"),
        "domesticPeakSalesCny100m": product.get("domestic_peak_sales_cny_100m"),
        "overseasPeakSalesUsd100m": product.get("overseas_peak_sales_usd_100m"),
        "indicationResults": [],
        "simplifiedMarketResults": market_results,
        "totalRevenueCny100m": total_revenue,
        "freeCashFlowCny100m": fcf,
        "dcf": _dcf(fcf, scenario, years),
    }


def _apply_simplified_actual_sales_if_available(
    *,
    product: dict[str, Any],
    market_name: str,
    years: list[int],
    launch_year: int | None,
    peak_year: int | None,
    adjusted_peak_revenue: float,
    fallback_revenue: list[float],
) -> tuple[list[float], dict[str, Any]]:
    """Scale the rule curve from the latest normalized product actual, if supplied.

    The optional ``simplified_actual_sales`` payload is DCF-owned and must already
    use risk/equity-adjusted CNY 100m.  Company-level iFinD revenue is deliberately
    not accepted as a substitute for product sales.
    """

    histories = product.get("simplified_actual_sales") or {}
    entries = histories.get(market_name) if isinstance(histories, dict) else None
    normalized = []
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        year = item.get("year")
        revenue = item.get("revenue_cny_100m")
        if (
            isinstance(year, int)
            and not isinstance(year, bool)
            and isinstance(revenue, (int, float))
            and not isinstance(revenue, bool)
            and revenue >= 0
        ):
            normalized.append(item)

    first_forecast_year = years[0] if years else None
    eligible = [
        item
        for item in normalized
        if first_forecast_year is None or item["year"] <= first_forecast_year
    ]
    latest = max(eligible, key=lambda item: item["year"], default=None)
    if latest is not None and launch_year is not None and peak_year is not None:
        actual_factor = sales_ramp_factor(
            latest["year"],
            launch_year=launch_year,
            peak_year=peak_year,
            market=market_name,
        )
        if actual_factor > 0:
            actual_revenue = float(latest["revenue_cny_100m"])
            scaled = [
                min(
                    max(adjusted_peak_revenue, 0.0),
                    actual_revenue
                    * sales_ramp_factor(
                        year,
                        launch_year=launch_year,
                        peak_year=peak_year,
                        market=market_name,
                    )
                    / actual_factor,
                )
                for year in years
            ]
            return scaled, {
                "year": latest["year"],
                "revenue": round(actual_revenue, 4),
                "source": "product_actual_sales",
                "sourceLabel": "单品历史销售衔接",
                "sourceDetail": latest.get("source") or "DCF单品历史销售数据",
            }

    editable_year = next(
        (year for year, value in zip(years, fallback_revenue) if value > 0),
        None,
    )
    editable_revenue = (
        fallback_revenue[years.index(editable_year)]
        if editable_year in years
        else None
    )
    return fallback_revenue, {
        "year": editable_year,
        "revenue": round(editable_revenue, 4) if isinstance(editable_revenue, (int, float)) else None,
        "source": "peak_anchor_launch_timing_backsolve",
        "sourceLabel": "峰值锚点＋上市时间反推",
        "sourceDetail": "当前无单品历史销售数据；不使用公司层面iFinD营业收入替代。",
    }


def _growth_rates(values: list[float]) -> list[float | None]:
    rates: list[float | None] = [None]
    for previous, current in zip(values, values[1:]):
        if previous > 0:
            rates.append(round(current / previous - 1, 6))
        else:
            rates.append(None)
    return rates


def _calculate_indication(
    product: dict[str, Any],
    indication: dict[str, Any],
    scenario: dict[str, Any],
    years: list[int],
    peak_allocations: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    current_phase_pos = phase_success_probability(indication.get("phase"))
    market_results = [
        _calculate_market(
            product,
            indication,
            market,
            scenario,
            years,
            peak_allocations.get(
                (str(indication.get("indication_id") or ""), str(market.get("market") or "")),
                {},
            ),
        )
        for market in indication.get("markets") or []
    ]
    total_revenue = _sum_series([item["equityAdjustedRevenueCny100m"] for item in market_results], years)
    return {
        "indicationId": indication.get("indication_id"),
        "indicationText": indication.get("indication_text"),
        "diseaseKey": indication.get("disease_key"),
        "phase": indication.get("phase"),
        "launchYear": _scenario_launch_year(indication, scenario),
        "peakYear": _scenario_peak_year(indication, scenario),
        "pos": current_phase_pos if current_phase_pos is not None else indication.get("pos"),
        "marketResults": market_results,
        "totalRevenueCny100m": total_revenue,
    }


def _calculate_market(
    product: dict[str, Any],
    indication: dict[str, Any],
    market: dict[str, Any],
    scenario: dict[str, Any],
    years: list[int],
    peak_allocation: dict[str, Any],
) -> dict[str, Any]:
    launch_year = _scenario_market_launch_year(indication, market, scenario)
    peak_year = _scenario_market_peak_year(indication, market, scenario)
    peak_anchor = market.get("peak_sales_anchor")
    peak_per_indication = peak_allocation.get("allocatedPeakSalesAnchor")
    if peak_per_indication is None and peak_anchor is not None:
        peak_per_indication = market.get("allocated_peak_sales_anchor")

    patient_flow_sales = _patient_flow_gross_sales(market, scenario, years)
    independent = bool(market.get("revenue_input_sources"))
    if independent:
        if patient_flow_sales is None or peak_anchor is not None or peak_per_indication is not None:
            raise ValueError("Independent revenue requires a complete patient flow without a peak anchor")
        gross_sales = [value * (1 + market.get("europe_ratio", 0)) for value in patient_flow_sales]
        peak_year = years[gross_sales.index(max(gross_sales))] if any(gross_sales) else None
        method = "independent_patient_flow"
        method_note = "可治疗患者数 × 产品渗透率 × 治疗时长 × 月净价。海外含适用的欧洲折算。"
    elif patient_flow_sales is not None:
        gross_sales = patient_flow_sales
        method = "patient_flow_bottom_up"
        method_note = (
            "使用本产品患者数 × 年治疗费用；其中年治疗费用=月价格×单年度治疗月数。"
            "肿瘤治疗月数直接采用参考mPFS，不设12个月上限且不做跨年队列结转。"
            "存在产品级峰值锚点时，先按市场空间分配到各适应症，再由患者规模、价格和疗程反推峰值渗透率；"
            "峰值锚点仅用于校准渗透率，不再对逐年销售额设置MIN硬上限。"
        )
    elif peak_anchor is None:
        gross_sales = [0.0 for _ in years]
        method = "missing_sales_inputs"
        method_note = "缺少患者流输入和峰值销售锚点，请手动补充。"
    else:
        gross_sales = [
            _sales_curve_value(
                year,
                launch_year,
                peak_year,
                peak_per_indication,
                scenario,
                market=str(market.get("market") or "china"),
            )
            for year in years
        ]
        method = "peak_sales_anchor_curve"
        method_note = (
            "患者流关键字段缺失，使用按峰值年市场空间权重分配后的 DrugAlpha 峰值销售锚点，"
            f"按{ramp_description(str(market.get('market') or 'china'))}；"
            "分配及销售爬坡均不使用研发阶段或上市成功率。"
        )

    equity_adjusted = _apply_market_economics(gross_sales, market, indication)
    effective_pos, pos_source = resolve_market_success_probability(
        market=market,
        indication_pos=indication.get("pos"),
        indication_phase=indication.get("phase"),
    )

    return {
        "market": market.get("market"),
        "currency": market.get("currency"),
        "launchYear": launch_year,
        "launchYearBasis": market.get("launch_year_basis") or indication.get("launch_year_basis"),
        "peakYear": peak_year,
        "peakYearBasis": market.get("peak_year_basis") or indication.get("peak_year_basis"),
        "salesRampProfile": market.get("sales_ramp_profile") or ramp_profile_id(str(market.get("market") or "china")),
        "peakSalesAnchor": peak_anchor,
        "peakSalesAnchorUnit": market.get("peak_sales_anchor_unit"),
        "peakSalesPerIndication": None if peak_per_indication is None else round(peak_per_indication, 4),
        "marketSpaceCapacity100m": peak_allocation.get("marketSpaceCapacity100m"),
        "peakSalesAllocationWeight": peak_allocation.get("peakSalesAllocationWeight"),
        "peakSalesAllocationMethod": peak_allocation.get("peakSalesAllocationMethod"),
        "peakAnchorCalibration": market.get("peak_anchor_calibration"),
        "peakSalesCapApplied": False,
        "successProbability": round(effective_pos, 4),
        "successProbabilitySource": pos_source,
        "patientFlowDiagnostics": _patient_flow_diagnostics(market, years),
        "anchorVarianceAtPeak": _anchor_variance_at_peak(
            gross_sales,
            years,
            peak_year,
            peak_per_indication,
        ),
        "economicsBridgeAtPeak": _peak_economics_bridge(
            max(gross_sales) if independent else peak_per_indication,
            market,
            indication,
        ),
        "grossSales": _round_series(gross_sales),
        "equityAdjustedRevenueCny100m": _round_series(equity_adjusted),
        "method": method,
        "methodNote": method_note,
    }


def _product_peak_allocations(
    product: dict[str, Any],
    years: list[int],
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for market_name in ("china", "us"):
        entries = [
            (indication, market)
            for indication in product.get("indications") or []
            for market in indication.get("markets") or []
            if market.get("market") == market_name
        ]
        if not entries:
            continue
        spaces = [
            market_space_from_flow(
                market.get("patient_flow") or {},
                peak_year=market.get("peak_year") or indication.get("peak_year"),
                forecast_years=years,
            )
            for indication, market in entries
        ]
        weights, method = allocate_market_space_weights(spaces)
        for (indication, market), space, weight in zip(entries, spaces, weights):
            anchor = market.get("peak_sales_anchor")
            result[(str(indication.get("indication_id") or ""), market_name)] = {
                "marketSpaceCapacity100m": space,
                "peakSalesAllocationWeight": round(weight, 8),
                "peakSalesAllocationMethod": method,
                "allocatedPeakSalesAnchor": (
                    round(float(anchor) * weight, 6)
                    if isinstance(anchor, (int, float))
                    else None
                ),
            }
    return result


def _patient_flow_diagnostics(market: dict[str, Any], years: list[int]) -> dict[str, Any]:
    patient_flow = market.get("patient_flow") or {}
    biomarker_ratio = patient_flow.get("biomarker_ratio")
    target_treatment_ratio = patient_flow.get("target_treatment_ratio")
    penetration_curve = patient_flow.get("penetration_curve") or []
    monthly_price = patient_flow.get("monthly_price") or []
    treatment_months = patient_flow.get("treatment_months") or []
    max_penetration = max([value for value in penetration_curve if value is not None] or [None])
    missing_fields = []
    if patient_flow.get("base_patient_count_10k") is None:
        missing_fields.append("base_patient_count_10k")
    if not isinstance(biomarker_ratio, (int, float)):
        missing_fields.append("biomarker_ratio")
    if not isinstance(target_treatment_ratio, (int, float)):
        missing_fields.append("target_treatment_ratio")
    if len(penetration_curve) < len(years) or any(value is None for value in penetration_curve[: len(years)]):
        missing_fields.append("penetration_curve")
    if len(monthly_price) < len(years) or any(value is None for value in monthly_price[: len(years)]):
        missing_fields.append("monthly_price")
    if len(treatment_months) < len(years) or any(value is None for value in treatment_months[: len(years)]):
        missing_fields.append("treatment_months")
    return {
        "basePatientCount10k": patient_flow.get("base_patient_count_10k"),
        "basePatientMetric": patient_flow.get("base_patient_metric"),
        "biomarkerRatio": biomarker_ratio,
        "targetTreatmentRatio": target_treatment_ratio,
        "treatmentStatusRuleId": patient_flow.get("treatment_status_rule_id"),
        "drugCostCategory": patient_flow.get("drug_cost_category"),
        "priceBasis": patient_flow.get("price_basis"),
        "requiresUserInput": bool(patient_flow.get("requires_user_input") or missing_fields),
        "missingFields": missing_fields,
        "maxPenetration": None if max_penetration is None else round(max_penetration, 4),
        "penetrationCapped": bool(max_penetration is not None and max_penetration >= 1.0),
        "editableAssumptionFields": [
            "base_patient_count_10k",
            "biomarker_ratio",
            "target_treatment_ratio",
            "penetration_curve",
            "monthly_price",
            "treatment_months",
        ],
    }


def _peak_economics_bridge(
    gross_peak: float | None,
    market: dict[str, Any],
    indication: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(gross_peak, (int, float)):
        return None
    pos, pos_source = resolve_market_success_probability(
        market=market,
        indication_pos=indication.get("pos"),
        indication_phase=indication.get("phase"),
    )
    after_pos = float(gross_peak) * pos
    if market.get("market") == "china":
        share = market.get("equity_ratio")
        share = 1.0 if share is None else float(share)
        exchange_rate = 1.0
        gross_unit = "CNY_100m"
    else:
        share = market.get("revenue_share_ratio")
        share = 1.0 if share is None else float(share)
        exchange_rate = market.get("exchange_rate")
        exchange_rate = 7.0 if exchange_rate is None else float(exchange_rate)
        gross_unit = "USD_100m"
    share *= market.get("economic_ownership", 1)
    return {
        "grossPrePosPeak": round(float(gross_peak), 4),
        "grossPrePosPeakUnit": gross_unit,
        "successProbability": round(pos, 4),
        "successProbabilitySource": pos_source,
        "afterPosPeak": round(after_pos, 4),
        "economicShareRatio": round(share, 4),
        "exchangeRate": round(exchange_rate, 4),
        "adjustedRevenueCny100m": round(after_pos * share * exchange_rate, 4),
    }


def _product_peak_anchor_reconciliation(indication_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reconciliations = []
    for market_name in ("china", "us"):
        rows = [
            market
            for indication in indication_results
            for market in indication.get("marketResults") or []
            if market.get("market") == market_name
        ]
        if not rows:
            continue
        anchors = [row.get("peakSalesAnchor") for row in rows if isinstance(row.get("peakSalesAnchor"), (int, float))]
        if not anchors:
            continue
        allocated = [row.get("peakSalesPerIndication") for row in rows]
        gross_at_peak = [
            (row.get("anchorVarianceAtPeak") or {}).get("grossSalesAtPeak")
            for row in rows
        ]
        adjusted = [
            (row.get("economicsBridgeAtPeak") or {}).get("adjustedRevenueCny100m")
            for row in rows
        ]
        reconciliations.append({
            "market": market_name,
            "productPeakSalesAnchor": round(float(anchors[0]), 4),
            "allocatedPeakSalesSum": round(sum(float(value) for value in allocated if isinstance(value, (int, float))), 4),
            "grossPrePosPeakSum": (
                round(sum(float(value) for value in gross_at_peak), 4)
                if gross_at_peak and all(isinstance(value, (int, float)) for value in gross_at_peak)
                else None
            ),
            "adjustedRevenuePeakSumCny100m": round(sum(float(value) for value in adjusted if isinstance(value, (int, float))), 4),
            "explanation": "峰值锚点先校准POS前总销售额，再依次应用上市成功率、权益/分成比例及海外汇率。",
        })
    return reconciliations


def _patient_flow_gross_sales(
    market: dict[str, Any],
    scenario: dict[str, Any],
    years: list[int],
) -> list[float] | None:
    flow = market.get("patient_flow") or {}
    if market.get("revenue_input_sources"):
        flow = {**flow, "launch_year": market["launch_year"] + scenario.get("launch_delay_years", 0)}
    result = calculate_patient_flow_gross_sales(
        patient_flow=flow,
        years=years,
        penetration_multiplier=scenario.get("penetration_multiplier", 1.0),
        price_multiplier=scenario.get("price_multiplier", 1.0),
        revenue_realization_multiplier=_scenario_revenue_realization(scenario),
    )
    return None if result is None else result["grossSales100m"]


def _anchor_variance_at_peak(
    gross_sales: list[float],
    years: list[int],
    peak_year: int | None,
    peak_per_indication: float | None,
) -> dict[str, float] | None:
    if peak_year is None or peak_per_indication in (None, 0) or peak_year not in years:
        return None
    peak_sales = gross_sales[years.index(peak_year)]
    return {
        "grossSalesAtPeak": round(peak_sales, 4),
        "anchorAtPeak": round(peak_per_indication, 4),
        "variancePct": round((peak_sales / peak_per_indication - 1) * 100, 2),
    }


def _sales_curve_value(
    year: int,
    launch_year: int | None,
    peak_year: int | None,
    peak_sales: float | None,
    scenario: dict[str, Any],
    *,
    market: str,
) -> float:
    if launch_year is None or peak_year is None or peak_sales is None or year < launch_year:
        return 0.0

    adjusted_peak = peak_sales * _scenario_revenue_realization(scenario)
    return adjusted_peak * sales_ramp_factor(
        year,
        launch_year=launch_year,
        peak_year=peak_year,
        market=market,
    )


def _scenario_revenue_realization(scenario: dict[str, Any]) -> float:
    """Return the single scenario driver used for product revenue realization.

    Older saved drafts do not contain the explicit field, so their former
    penetration × price convention remains readable without affecting new drafts.
    """

    explicit = scenario.get("revenue_realization_multiplier")
    if isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
        return max(float(explicit), 0.0)
    return max(
        float(scenario.get("penetration_multiplier", 1.0))
        * float(scenario.get("price_multiplier", 1.0)),
        0.0,
    )


def _apply_market_economics(
    gross_sales: list[float],
    market: dict[str, Any],
    indication: dict[str, Any],
) -> list[float]:
    pos, _source = resolve_market_success_probability(
        market=market,
        indication_pos=indication.get("pos"),
        indication_phase=indication.get("phase"),
    )
    ownership = market.get("economic_ownership", 1)
    if market.get("market") == "china":
        equity = market.get("equity_ratio")
        equity = 1.0 if equity is None else equity
        return [value * equity * pos * ownership for value in gross_sales]

    share = market.get("revenue_share_ratio")
    share = 1.0 if share is None else share
    exchange_rate = market.get("exchange_rate")
    exchange_rate = 7.0 if exchange_rate is None else exchange_rate
    return [value * share * pos * exchange_rate * ownership for value in gross_sales]


def _free_cash_flow(revenue: list[float], scenario: dict[str, Any]) -> list[float]:
    gross_margin = scenario.get("gross_margin", 0.90)
    opex = (
        scenario.get("sales_expense_rate", 0.18)
        + scenario.get("rd_expense_rate", 0.16)
        + scenario.get("admin_expense_rate", 0.05)
    )
    tax_rate = scenario.get("tax_rate", 0.15)
    return [value * max(gross_margin - opex, 0) * (1 - tax_rate) for value in revenue]


def _dcf(fcf: list[float], scenario: dict[str, Any], years: list[int]) -> dict[str, Any]:
    discount_rate = scenario.get("discount_rate", 0.10)
    terminal_growth = scenario.get("terminal_growth_rate", 0.02)
    discount_factors = [(1 + discount_rate) ** (idx + 1) for idx, _ in enumerate(years)]
    pv_fcf = [value / discount_factors[idx] for idx, value in enumerate(fcf)]
    final_fcf = fcf[-1] if fcf else 0.0
    terminal_value = 0.0
    if discount_rate > terminal_growth and final_fcf > 0:
        terminal_value = final_fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv_terminal_value = terminal_value / discount_factors[-1] if discount_factors else 0.0
    enterprise_value = sum(pv_fcf) + pv_terminal_value
    return {
        "discountRate": discount_rate,
        "terminalGrowthRate": terminal_growth,
        "pvForecastFcfCny100m": round(sum(pv_fcf), 4),
        "terminalValueCny100m": round(terminal_value, 4),
        "pvTerminalValueCny100m": round(pv_terminal_value, 4),
        "enterpriseValueCny100m": round(enterprise_value, 4),
    }


def _scenario_launch_year(indication: dict[str, Any], scenario: dict[str, Any]) -> int | None:
    launch_year = indication.get("launch_year")
    if launch_year is None:
        return None
    return launch_year + scenario.get("launch_delay_years", 0)


def _scenario_peak_year(indication: dict[str, Any], scenario: dict[str, Any]) -> int | None:
    peak_year = indication.get("peak_year")
    if peak_year is None:
        return None
    return peak_year + scenario.get("launch_delay_years", 0)


def _scenario_market_launch_year(
    indication: dict[str, Any],
    market: dict[str, Any],
    scenario: dict[str, Any],
) -> int | None:
    launch_year = market.get("launch_year")
    if not isinstance(launch_year, int):
        launch_year = indication.get("launch_year")
    if not isinstance(launch_year, int):
        return None
    return launch_year + scenario.get("launch_delay_years", 0)


def _scenario_market_peak_year(
    indication: dict[str, Any],
    market: dict[str, Any],
    scenario: dict[str, Any],
) -> int | None:
    peak_year = market.get("peak_year")
    if not isinstance(peak_year, int):
        peak_year = indication.get("peak_year")
    if not isinstance(peak_year, int):
        return None
    return peak_year + scenario.get("launch_delay_years", 0)


def _sum_series(series_list: list[list[float]], years: list[int]) -> list[float]:
    if not series_list:
        return [0.0 for _ in years]
    return _round_series([sum(series[idx] for series in series_list) for idx, _ in enumerate(years)])


def _round_series(values: list[float]) -> list[float]:
    return [round(value, 4) for value in values]
