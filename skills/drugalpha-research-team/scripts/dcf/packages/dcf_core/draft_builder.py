"""Build editable patient-flow DCF draft models from source snapshots."""

from __future__ import annotations

import re
from typing import Any

from ..source_adapters.clinical_pfs import resolve_tumor_treatment_duration
from .defaults import DEFAULT_SCENARIOS, FORECAST_YEARS, PHASE_TO_LAUNCH_YEAR
from .indication_merge import merge_duplicate_indications, normalized_indication_key
from .launch_timing import resolve_china_launch_year
from .market_launch_timing import (
    is_explicit_overseas_market_status,
    resolve_overseas_launch_year,
)
from .peak_allocation import allocate_market_space_weights, calculate_market_space
from .sales_ramp import (
    penetration_curve_from_peak,
    ramp_profile_id,
    resolve_peak_year,
)
from .phase_rules import is_marketed_phase, is_nda_bla_phase
from .schemas import (
    CompanyDraftModel,
    IndicationDraft,
    MarketDraft,
    ModelWarning,
    PatientFlowAssumptions,
    PopulationFilter,
    ProductDraft,
)
from .success_probability import phase_success_probability
from .treatment_status_rules import classify_treatment_status, infer_biomarker_ratio
from .treatment_pricing import classify_cost_profile, default_price_curve, price_source_note


BROAD_INDICATION_TOKENS = (
    "实体瘤",
    "恶性肿瘤",
    "多瘤种",
    "泛瘤种",
    "solidtumor",
    "solidtumors",
    "advancedtumor",
    "advancedtumors",
)

# The NSCLC knowledge page deliberately stores public lung-cancer totals as
# its epidemiology base. The explicit ``nsclc-share`` subgroup is therefore a
# mandatory base-population conversion, not a biomarker filter. Keep this
# narrowly scoped until other disease pages declare equivalent parent-disease
# denominator metadata.
BASE_POPULATION_SUBGROUP_RULES = {
    "nsclc": {
        "filter_key": "nsclc-share",
        "parent_population_marker": "肺癌",
    },
}


def build_draft_model_from_snapshot(snapshot: dict[str, Any], *, independent_revenue: bool = False) -> dict[str, Any]:
    diseases = {
        disease.get("diseaseKey"): disease
        for disease in snapshot.get("diseaseKnowledge", {}).get("matchedDiseases", [])
        if disease.get("diseaseKey")
    }
    source_warnings = [_warning_from_source(item) for item in snapshot.get("warnings") or []]

    products = []
    for product_source in snapshot.get("pipeline", {}).get("products", []):
        if not product_source.get("isDcfSourceProduct"):
            continue
        product = _build_product(product_source, diseases, independent_revenue=independent_revenue)
        if product and product.indications:
            products.append(product)

    included_indications = {
        (product.drug_name, normalized_indication_key(indication.indication_text))
        for product in products
        for indication in product.indications
    }
    included_products = {product.drug_name for product in products}
    warnings = [
        warning
        for warning in source_warnings
        if (
            warning.product_name in included_products
            and (
                warning.indication_text is None
                or (
                    warning.product_name,
                    normalized_indication_key(warning.indication_text),
                ) in included_indications
            )
        )
    ]
    for product in products:
        warnings.extend(product.warnings)
    warnings = _dedupe_warnings(warnings)

    company = snapshot.get("company") or {}
    model = CompanyDraftModel(
        model_type="patient_flow_dcf_draft",
        source_snapshot_id=snapshot.get("snapshotId"),
        company_key=company.get("companyKey"),
        company_name=company.get("companyName"),
        stock_codes=company.get("stockCodes"),
        forecast_years=FORECAST_YEARS,
        products=products,
        scenarios=DEFAULT_SCENARIOS,
        warnings=warnings,
        disclaimer=snapshot.get("disclaimer")
        or "当前模型由AI程序生成，不代表DrugAlpha数据库估值，请根据实际情况自行调整参数确定估值。",
    )
    draft = model.to_dict()
    if independent_revenue:
        # Preserve selection before indication/market filtering can drop a row.
        draft["valuation_scope"] = list({
            (_stable_id(product.get("productKey") or product.get("drugName")), market): {
                "product_id": _stable_id(product.get("productKey") or product.get("drugName")),
                "drug_name": product.get("drugName"),
                "target": product.get("target"),
                "market": market,
            }
            for product in snapshot.get("pipeline", {}).get("products", [])
            for market, geography, peak_field in (
                ("china", "domestic", "estimatedPeakSales"),
                ("us", "overseas", "estimatedPeakSalesUsd"),
            )
            for source in [(product.get("valuationTracking") or {}).get(geography) or {}]
            if source.get("includedInValuation") is True
            and _positive_peak_anchor(source.get(peak_field)) is not None
        }.values())
    return draft


def _build_product(
    product_source: dict[str, Any],
    diseases: dict[str, dict[str, Any]],
    *,
    independent_revenue: bool = False,
) -> ProductDraft | None:
    valuation = product_source.get("valuationTracking") or {}
    domestic = valuation.get("domestic") or {}
    overseas = valuation.get("overseas") or {}

    indications = []
    eligible_indications, valuation_selection = _valuation_indications(product_source)
    if valuation_selection and _has_overseas_peak_anchor(overseas):
        selected_identity = str(valuation_selection.get("selectedSourceRowIdentity") or "")
        overseas_selection = {
            selected_identity: (
                "该估值资产暂无成熟适应症；国内与海外统一采用 DrugAlpha AI 联网选择的同一具体瘤种，"
                "仅峰值锚点、权益/分成和汇率按市场分别处理。"
            )
        }
    else:
        overseas_selection = _select_overseas_indications(eligible_indications, overseas, diseases)
    domestic_eligible_count = max(
        sum(
            1
            for item in eligible_indications
            if item.get("rowType") != "valuation_ai_selection_pending"
            and not _exclude_from_domestic_market(item)
        ),
        1,
    )
    overseas_eligible_count = max(len(overseas_selection), 1)
    for index, indication_source in enumerate(eligible_indications, start=1):
        overseas_reason = overseas_selection.get(_indication_identity(indication_source))
        research_market = indication_source.get("dcfResearchMarket") if independent_revenue else None
        if research_market:
            overseas_reason = indication_source["representativeSelectionBasis"] if research_market == "us" else None
        indication_selection = (
            valuation_selection
            if valuation_selection
            and valuation_selection.get("selectedSourceRowIdentity") == _indication_identity(indication_source)
            else None
        )
        indications.append(
            _build_indication(
                product_source,
                indication_source,
                diseases,
                index,
                {} if research_market == "us" else domestic,
                {} if research_market == "china" else overseas,
                domestic_eligible_count,
                overseas_eligible_count,
                include_overseas=overseas_reason is not None,
                overseas_reason=overseas_reason or _not_overseas_reason(overseas),
                valuation_selection=indication_selection,
                independent_revenue=independent_revenue,
            )
        )

    # Patient-flow DCF is market-specific.  A product-level anchor in one
    # geography must never cause an unanchored geography to enter the model.
    indications = [item for item in indications if item.markets]
    if not indications:
        return None

    if not independent_revenue and product_source.get("modelingMode") != "simplified_growth":
        _apply_market_space_peak_allocations(indications)

    # Product coverage is the union of configured valuation assets and mature
    # pipeline assets. Indication coverage is narrower: mature indications only,
    # except for one DeepSeek web-researched early indication when a valuation asset
    # has no mature indication at all.
    included = bool(product_source.get("isDcfSourceProduct"))
    product_warnings = []
    if valuation_selection:
        product_warnings.append(
            ModelWarning(
                level="info",
                code="AI_SELECTED_EARLY_INDICATION_FOR_VALUATION",
                message=(
                    "该估值跟踪资产暂无成熟适应症，已由联网检索结果与 DrugAlpha AI 选择一个早期适应症作为峰值测算基准；必须人工复核。"
                ),
                product_name=product_source.get("drugName"),
                indication_text=valuation_selection.get("selectedIndication"),
            )
        )
    elif product_source.get("isValuationTrackingProduct") and not product_source.get("valuationOnly"):
        if not any(item.get("isDcfMaturePhase") for item in product_source.get("indications") or []):
            product_warnings.append(
                ModelWarning(
                    level="warning",
                    code="VALUATION_INDICATION_AI_SELECTION_PENDING",
                    message="该估值跟踪资产暂无成熟适应症，DrugAlpha AI 联网选择未返回有效结果，暂保留待确认占位块。",
                    product_name=product_source.get("drugName"),
                    indication_text=None,
                )
            )
    return ProductDraft(
        product_id=_stable_id(product_source.get("productKey") or product_source.get("drugName")),
        drug_name=product_source.get("drugName"),
        drug_key=valuation.get("matchedDrugKey"),
        target=product_source.get("target"),
        modeling_mode="patient_flow" if independent_revenue else product_source.get("modelingMode") or "patient_flow",
        modeling_reason=product_source.get("modelingReason") or "按适应症患者流测算",
        included_in_valuation=included,
        source_kinds=list(product_source.get("dcfSourceKinds") or []),
        valuation_only=bool(product_source.get("valuationOnly")),
        domestic_peak_sales_cny_100m=None if independent_revenue else domestic.get("estimatedPeakSales"),
        domestic_equity_ratio=None if independent_revenue else domestic.get("equityRatio"),
        overseas_peak_sales_usd_100m=None if independent_revenue else overseas.get("estimatedPeakSalesUsd"),
        overseas_revenue_share_ratio=None if independent_revenue else overseas.get("revenueShareRatio"),
        overseas_success_probability=None if independent_revenue else overseas.get("successProbability"),
        exchange_rate=None if independent_revenue else overseas.get("exchangeRate"),
        indications=indications,
        simplified_actual_sales={} if independent_revenue else _normalized_simplified_actual_sales(domestic, overseas),
        valuation_indication_selection=valuation_selection,
        warnings=product_warnings,
    )


def _normalized_simplified_actual_sales(
    domestic: dict[str, Any],
    overseas: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Pass through only explicit product-level, adjusted CNY revenue history.

    The current valuation source does not yet publish this field.  Keeping the
    contract here lets a later read-only source addition use actual product sales
    without ever substituting company-level iFinD revenue.
    """

    result: dict[str, list[dict[str, Any]]] = {}
    for market_name, source in (("china", domestic), ("us", overseas)):
        rows = []
        for item in source.get("actualSalesHistory") or []:
            if not isinstance(item, dict):
                continue
            year = item.get("year")
            revenue = item.get("revenueCny100m")
            if (
                isinstance(year, int)
                and not isinstance(year, bool)
                and isinstance(revenue, (int, float))
                and not isinstance(revenue, bool)
                and revenue >= 0
            ):
                rows.append(
                    {
                        "year": year,
                        "revenue_cny_100m": float(revenue),
                        "source": item.get("source") or item.get("sourceName") or "单品历史销售数据",
                    }
                )
        if rows:
            result[market_name] = sorted(rows, key=lambda item: item["year"])
    return result


def _valuation_indications(
    product_source: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    source_indications = list(product_source.get("indications") or [])
    if product_source.get("modelingMode") == "simplified_growth":
        # Simplified products are forecast once at product/market level. Keep
        # published pipeline rows only for launch timing and POS resolution; no
        # indication-level patient-flow or peak allocation is performed.
        return merge_duplicate_indications(source_indications), None
    mature_indications = [
        item for item in source_indications if item.get("isDcfMaturePhase")
    ]
    if mature_indications:
        return merge_duplicate_indications(mature_indications), None

    if product_source.get("valuationOnly"):
        return source_indications, None

    selection = product_source.get("valuationIndicationSelection") or None
    if product_source.get("isValuationTrackingProduct") and _valid_valuation_selection(selection):
        selected_identity = str(selection.get("selectedSourceRowIdentity") or "")
        selected = next(
            (
                item
                for item in source_indications
                if _indication_identity(item) == selected_identity
            ),
            None,
        )
        if selected is not None:
            return [selected], selection

    if product_source.get("isValuationTrackingProduct"):
        return [_valuation_indication_placeholder(product_source)], None
    return [], None


def _apply_market_space_peak_allocations(indications: list[IndicationDraft]) -> None:
    for market_name in ("china", "us"):
        entries = [
            (indication, market)
            for indication in indications
            for market in indication.markets
            if market.market == market_name
        ]
        if not entries:
            continue
        spaces = [
            calculate_market_space(
                base_patient_count_10k=market.patient_flow.base_patient_count_10k,
                patient_growth_rate=market.patient_flow.patient_growth_rate,
                biomarker_ratio=market.patient_flow.biomarker_ratio,
                target_treatment_ratio=market.patient_flow.target_treatment_ratio,
                monthly_price=market.patient_flow.monthly_price,
                treatment_months=market.patient_flow.treatment_months,
                peak_year=market.peak_year or indication.peak_year,
                forecast_years=FORECAST_YEARS,
            )
            for indication, market in entries
        ]
        weights, method = allocate_market_space_weights(spaces)
        for (indication, market), space, weight in zip(entries, spaces, weights):
            market.market_space_capacity_100m = space
            market.peak_sales_allocation_weight = round(weight, 8)
            market.peak_sales_allocation_method = method
            market.allocated_peak_sales_anchor = (
                round(float(market.peak_sales_anchor) * weight, 6)
                if isinstance(market.peak_sales_anchor, (int, float))
                else None
            )
            if market.allocated_peak_sales_anchor is not None:
                flow = market.patient_flow
                flow.penetration_curve = _backsolve_penetration_curve(
                    flow.base_patient_count_10k,
                    patient_growth_rate=flow.patient_growth_rate,
                    eligible_patient_ratio=(
                        float(flow.biomarker_ratio or 0)
                        * float(flow.target_treatment_ratio or 0)
                    ),
                    monthly_price=flow.monthly_price,
                    treatment_months=float(_first_non_null(flow.treatment_months) or 0),
                    peak_sales_anchor=market.allocated_peak_sales_anchor,
                    eligible_indication_count=1,
                    launch_year=market.launch_year or indication.launch_year,
                    peak_year=market.peak_year or indication.peak_year,
                    market=market.market,
                )


def _valuation_indication_placeholder(product_source: dict[str, Any]) -> dict[str, Any]:
    product_key = product_source.get("productKey") or product_source.get("drugName") or "unknown"
    return {
        "sourceRowIdentity": f"valuation_ai_pending::{product_key}",
        "rowType": "valuation_ai_selection_pending",
        "indicationText": "估值跟踪资产，具体瘤种待 DrugAlpha AI 联网确认",
        "phase": None,
        "isDcfMaturePhase": False,
        "diseaseMatch": {},
    }


def _dedupe_warnings(warnings: list[ModelWarning]) -> list[ModelWarning]:
    result: list[ModelWarning] = []
    seen: set[tuple[str, str, str, str]] = set()
    for warning in warnings:
        normalized_indication = normalized_indication_key(warning.indication_text)
        key = (
            str(warning.code or ""),
            str(warning.product_name or ""),
            normalized_indication,
            "" if normalized_indication else str(warning.message or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(warning)
    return result


def _first_non_null(values: list[Any]) -> Any:
    return next((value for value in values if value is not None), None)


def _build_indication(
    product_source: dict[str, Any],
    indication_source: dict[str, Any],
    diseases: dict[str, dict[str, Any]],
    index: int,
    domestic: dict[str, Any],
    overseas: dict[str, Any],
    domestic_indication_count: int,
    overseas_indication_count: int,
    *,
    include_overseas: bool,
    overseas_reason: str,
    valuation_selection: dict[str, Any] | None = None,
    independent_revenue: bool = False,
) -> IndicationDraft:
    match = indication_source.get("diseaseMatch") or {}
    disease = diseases.get(match.get("diseaseKey")) or {}
    phase = indication_source.get("phase")
    indication_text = (
        str(valuation_selection.get("selectedIndication") or "").strip()
        if valuation_selection
        else str(indication_source.get("indicationText") or "").strip()
    )
    launch_year, launch_year_basis = resolve_china_launch_year(
        phase=phase,
        status_start_date=indication_source.get("statusStartDate"),
        indication_text=indication_text,
        latest_progress=indication_source.get("latestProgress"),
        ai_suggested_year=(valuation_selection or {}).get("suggestedLaunchYear"),
    )
    peak_year, peak_year_basis = resolve_peak_year(
        launch_year=launch_year,
        market="china",
        suggested_peak_year=(valuation_selection or {}).get("suggestedPeakYear"),
    )
    overseas_launch_year, overseas_launch_year_basis = resolve_overseas_launch_year(
        indication=indication_source,
        product_indications=list(product_source.get("indications") or []),
        domestic_launch_year=launch_year,
    )
    overseas_peak_year, overseas_peak_year_basis = resolve_peak_year(
        launch_year=overseas_launch_year,
        market="us",
    )
    # China POS is phase-locked. DeepSeek indication selection must not override it.
    pos = _phase_pos(phase)
    is_pending_placeholder = indication_source.get("rowType") == "valuation_ai_selection_pending"
    exclude_from_domestic = _exclude_from_domestic_market(indication_source)
    market_domestic = {} if is_pending_placeholder or exclude_from_domestic else domestic
    market_overseas = {} if is_pending_placeholder else overseas
    warnings = []
    if not disease:
        warnings.append(
            ModelWarning(
                level="info",
                code="MISSING_EPIDEMIOLOGY",
                message="DrugAlpha 当前未找到患者流行病学数据，请手动补充。",
                product_name=product_source.get("drugName"),
                indication_text=indication_text,
            )
        )

    return IndicationDraft(
        indication_id=f"{_stable_id(product_source.get('productKey') or product_source.get('drugName'))}_ind_{index}",
        source_row_identity=indication_source.get("sourceRowIdentity"),
        indication_text=indication_text,
        trial_reg_no=indication_source.get("trialRegNo"),
        trial_reg_nos=list(indication_source.get("trialRegNos") or ([indication_source.get("trialRegNo")] if indication_source.get("trialRegNo") else [])),
        trial_evidence=list(indication_source.get("trialEvidence") or []),
        merged_trial_count=int(indication_source.get("mergedTrialCount") or 1),
        representative_selection_basis=indication_source.get("representativeSelectionBasis"),
        sponsor=indication_source.get("sponsor"),
        sponsors=list(indication_source.get("sponsors") or ([indication_source.get("sponsor")] if indication_source.get("sponsor") else [])),
        overseas_eligible=include_overseas,
        overseas_eligibility_reason=overseas_reason,
        disease_key=match.get("diseaseKey"),
        disease_title=disease.get("title"),
        disease_match_confidence=match.get("confidence") or 0,
        line_of_therapy=_infer_line_of_therapy(indication_text),
        phase=phase,
        status_start_date=indication_source.get("statusStartDate"),
        launch_year=launch_year,
        launch_year_basis=launch_year_basis,
        peak_year=peak_year,
        peak_year_basis=peak_year_basis,
        pos=pos,
        markets=_build_markets(
            product_source.get("drugName"),
            product_source.get("target"),
            disease,
            market_domestic,
            market_overseas,
            indication_text,
            launch_year,
            peak_year,
            _infer_line_of_therapy(indication_text),
            domestic_indication_count,
            structured_biomarkers=list(((indication_source.get("indicationFacets") or {}).get("biomarkers") or [])),
            include_overseas=include_overseas,
            overseas_indication_count=overseas_indication_count,
            domestic_launch_year_basis=launch_year_basis,
            overseas_launch_year=overseas_launch_year,
            overseas_launch_year_basis=overseas_launch_year_basis,
            overseas_peak_year=overseas_peak_year,
            domestic_peak_year_basis=peak_year_basis,
            overseas_peak_year_basis=overseas_peak_year_basis,
            independent_revenue=independent_revenue,
        ),
        warnings=warnings,
    )


def _valid_valuation_selection(selection: Any) -> bool:
    if not isinstance(selection, dict):
        return False
    selected_indication = str(selection.get("selectedIndication") or "").strip()
    if not selected_indication:
        return False
    normalized = re.sub(r"[\s\-_/,，、]+", "", selected_indication).lower()
    return not any(token in normalized for token in BROAD_INDICATION_TOKENS)


def _build_markets(
    drug_name: str | None,
    target: str | None,
    disease: dict[str, Any],
    domestic: dict[str, Any],
    overseas: dict[str, Any],
    indication_text: str,
    launch_year: int | None,
    peak_year: int | None,
    line_of_therapy: str | None,
    domestic_indication_count: int,
    *,
    structured_biomarkers: list[dict[str, Any]] | None = None,
    include_overseas: bool,
    overseas_indication_count: int,
    domestic_launch_year_basis: str,
    overseas_launch_year: int | None,
    overseas_launch_year_basis: str,
    overseas_peak_year: int | None,
    domestic_peak_year_basis: str,
    overseas_peak_year_basis: str,
    independent_revenue: bool = False,
) -> list[MarketDraft]:
    markets: list[MarketDraft] = []
    domestic_peak_anchor = _positive_peak_anchor(domestic.get("estimatedPeakSales"))
    overseas_peak_anchor = _positive_peak_anchor(overseas.get("estimatedPeakSalesUsd"))
    if independent_revenue:
        # Scope uses the positive-peak flag only. No native forecast is seeded.
        return [
            MarketDraft(market=market, currency=currency,
                        patient_flow=PatientFlowAssumptions(market, None, "", 0.0))
            for market, currency, selected in (
                ("china", "CNY", domestic_peak_anchor is not None),
                ("us", "USD", include_overseas and overseas_peak_anchor is not None),
            )
            if selected
        ]
    if domestic_peak_anchor is not None:
        markets.append(MarketDraft(
            market="china",
            currency="CNY",
            launch_year=launch_year,
            launch_year_basis=domestic_launch_year_basis,
            peak_year=peak_year,
            peak_year_basis=domestic_peak_year_basis,
            sales_ramp_profile=ramp_profile_id("china"),
            patient_flow=_build_patient_flow(
                "china",
                drug_name,
                target,
                disease,
                indication_text,
                domestic_peak_anchor,
                domestic_indication_count,
                launch_year,
                peak_year,
                line_of_therapy,
                structured_biomarkers,
            ),
            peak_sales_anchor=domestic_peak_anchor,
            peak_sales_anchor_unit="CNY_100m",
            equity_ratio=domestic.get("equityRatio"),
        ))

    if include_overseas and overseas_peak_anchor is not None:
        markets.append(MarketDraft(
            market="us",
            currency="USD",
            launch_year=overseas_launch_year,
            launch_year_basis=overseas_launch_year_basis,
            peak_year=overseas_peak_year,
            peak_year_basis=overseas_peak_year_basis,
            sales_ramp_profile=ramp_profile_id("us"),
            patient_flow=_build_patient_flow(
                "us",
                drug_name,
                target,
                disease,
                indication_text,
                overseas_peak_anchor,
                overseas_indication_count,
                overseas_launch_year,
                overseas_peak_year,
                line_of_therapy,
                structured_biomarkers,
            ),
            peak_sales_anchor=overseas_peak_anchor,
            peak_sales_anchor_unit="USD_100m",
            revenue_share_ratio=overseas.get("revenueShareRatio"),
            success_probability=overseas.get("successProbability"),
            exchange_rate=overseas.get("exchangeRate"),
        ))
    return markets


def _build_patient_flow(
    market: str,
    drug_name: str | None,
    target: str | None,
    disease: dict[str, Any],
    indication_text: str,
    peak_sales_anchor: float | None,
    eligible_indication_count: int,
    launch_year: int | None,
    peak_year: int | None,
    line_of_therapy: str | None,
    structured_biomarkers: list[dict[str, Any]] | None = None,
) -> PatientFlowAssumptions:
    regional = _find_region(disease, market)
    filters = _population_filters(disease)
    base_count, base_metric, patient_source_notes = _resolve_patient_population(
        regional,
        disease,
        filters,
    )
    cost_profile = classify_cost_profile(
        drug_name=drug_name,
        target=target,
        indication_text=indication_text,
    )
    default_treatment_months = _default_treatment_months(line_of_therapy)
    treatment_duration = resolve_tumor_treatment_duration(
        drug_name=drug_name,
        target=target,
        indication_text=indication_text,
        line_of_therapy=line_of_therapy,
        default_months=default_treatment_months,
    )
    if cost_profile["drugCostCategory"] == "car_t_gene_therapy":
        treatment_duration = {
            "mode": "one_time_treatment",
            "treatmentMonths": 1.0,
            "referencePfsMonths": None,
            "source": "DCF一次性治疗规则",
            "sourceDetail": "按1次治疗计费；基因治疗和细胞治疗价格须按具体产品复核",
            "requiresHumanReview": "基因治疗" in str(drug_name or "") or "基因治疗" in indication_text,
            "references": [],
        }
    treatment_months = float(treatment_duration["treatmentMonths"])
    price_curve = default_price_curve(
        market=market,
        category=str(cost_profile["drugCostCategory"]),
        launch_year=launch_year,
        years=FORECAST_YEARS,
    )
    biomarker = infer_biomarker_ratio(indication_text, filters, structured_biomarkers)
    treatment_status = classify_treatment_status(
        indication_text,
        disease_key=disease.get("diseaseKey"),
        disease_title=disease.get("title"),
        market=market,
    )
    eligible_patient_ratio = float(biomarker["ratio"]) * float(treatment_status["ratio"])
    penetration_curve = _backsolve_penetration_curve(
        base_count,
        patient_growth_rate=0.005,
        eligible_patient_ratio=eligible_patient_ratio,
        monthly_price=price_curve,
        treatment_months=treatment_months,
        peak_sales_anchor=peak_sales_anchor,
        eligible_indication_count=eligible_indication_count,
        launch_year=launch_year,
        peak_year=peak_year,
        market=market,
    )

    return PatientFlowAssumptions(
        market=market,
        base_patient_count_10k=base_count,
        base_patient_metric=base_metric,
        patient_growth_rate=0.005,
        biomarker_ratio=float(biomarker["ratio"]),
        biomarker_ratio_source=str(biomarker["source"]),
        biomarker_ratio_requires_review=bool(biomarker["requiresHumanReview"]),
        biomarker_ratio_source_details=list(biomarker.get("sourceDetails") or []),
        target_treatment_ratio=float(treatment_status["ratio"]),
        treatment_status_rule_id=str(treatment_status["ruleId"]),
        treatment_status_rule_label=str(treatment_status["label"]),
        treatment_status_rule_reason=str(treatment_status["matchedOn"]),
        target_treatment_ratio_mode=str(treatment_status.get("ratioMode") or "fixed_rule"),
        target_treatment_ratio_source=str(treatment_status.get("ratioSource") or ""),
        target_treatment_ratio_source_url=str(treatment_status.get("ratioSourceUrl") or ""),
        target_treatment_ratio_requires_research=bool(treatment_status.get("requiresEvidenceResearch")),
        population_filters=filters,
        penetration_curve=penetration_curve,
        monthly_price=price_curve,
        drug_cost_category=str(cost_profile["drugCostCategory"]),
        drug_cost_category_label=str(cost_profile["drugCostCategoryLabel"]),
        price_basis=str(cost_profile["priceBasis"]),
        price_source=price_source_note(
            market,
            str(cost_profile["drugCostCategory"]),
        ),
        treatment_months=[treatment_months for _ in FORECAST_YEARS],
        treatment_months_mode=str(treatment_duration.get("mode") or "line_rule_fallback"),
        treatment_months_reference_pfs=treatment_duration.get("referencePfsMonths"),
        treatment_months_source=str(treatment_duration.get("source") or ""),
        treatment_months_source_detail=str(treatment_duration.get("sourceDetail") or ""),
        treatment_months_requires_review=bool(treatment_duration.get("requiresHumanReview")),
        treatment_months_references=list(treatment_duration.get("references") or []),
        source_notes=patient_source_notes,
        requires_user_input=base_count is None or all(value is None for value in penetration_curve),
    )


def _select_overseas_indications(
    eligible_indications: list[dict[str, Any]],
    overseas: dict[str, Any],
    diseases: dict[str, dict[str, Any]],
) -> dict[str, str]:
    if not eligible_indications or not _has_overseas_peak_anchor(overseas):
        return {}

    candidates: dict[str, dict[str, Any]] = {}

    def add_candidate(item: dict[str, Any], reason: str, priority: int) -> None:
        identity = _indication_identity(item)
        existing = candidates.get(identity)
        if existing is None or priority > int(existing["priority"]):
            candidates[identity] = {
                "item": item,
                "reason": reason,
                "priority": priority,
                "order": eligible_indications.index(item),
            }

    explicit_overseas_evidence = [
        item
        for item in eligible_indications
        if _has_explicit_overseas_market_evidence(item)
    ]
    for item in explicit_overseas_evidence:
        add_candidate(
            item,
            "该适应症存在明确境外开发、申报、获批或上市标签，纳入海外市场测算。",
            30,
        )

    for item in eligible_indications:
        nct_numbers = _nct_numbers(item)
        if item.get("isDcfMaturePhase") and nct_numbers:
            add_candidate(
                item,
                f"该适应症为3期及以上且存在NCT海外临床记录（{', '.join(nct_numbers)}），纳入海外市场测算。",
                20,
            )

    foreign_sponsored = [
        item
        for item in eligible_indications
        if item.get("isDcfMaturePhase")
        and any(
            _is_foreign_sponsor(sponsor)
            for sponsor in (item.get("sponsors") or [item.get("sponsor")])
        )
    ]
    if foreign_sponsored:
        for item in foreign_sponsored:
            add_candidate(
                item,
                "产品存在 DrugAlpha 海外市场测算；该适应症 sponsor 为海外/跨国企业。",
                10,
            )
    if candidates:
        return _dedupe_overseas_candidates(candidates)

    fallback = max(
        eligible_indications,
        key=lambda item: _indication_space_score(item, diseases),
    )
    return {
        _indication_identity(fallback): "产品存在 DrugAlpha 海外市场测算；未找到 sponsor 为海外企业的适应症，暂按已开展适应症中预计空间最大的适应症作为海外测算基准。"
    }


def _nct_numbers(indication: dict[str, Any]) -> list[str]:
    """Return normalized NCT numbers retained across merged trial evidence."""

    values: list[Any] = [
        indication.get("trialRegNo"),
        indication.get("trial_reg_no"),
        *(indication.get("trialRegNos") or []),
        *(indication.get("trial_reg_nos") or []),
    ]
    for evidence in indication.get("trialEvidence") or []:
        values.extend((evidence.get("trialRegNo"), evidence.get("trial_reg_no")))

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for number in re.findall(r"\bNCT\d+\b", str(value or ""), flags=re.IGNORECASE):
            normalized = number.upper()
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
    return result


def _dedupe_overseas_candidates(
    candidates: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Keep one indication when the same NCT trial appears on multiple rows."""

    ranked = sorted(
        candidates.items(),
        key=lambda entry: (
            -int(entry[1]["priority"]),
            -len(normalized_indication_key(entry[1]["item"].get("indicationText"))),
            int(entry[1]["order"]),
        ),
    )
    selected: dict[str, str] = {}
    claimed_ncts: set[str] = set()
    for identity, candidate in ranked:
        nct_numbers = set(_nct_numbers(candidate["item"]))
        if nct_numbers and claimed_ncts.intersection(nct_numbers):
            continue
        selected[identity] = str(candidate["reason"])
        claimed_ncts.update(nct_numbers)
    return selected


def _has_explicit_overseas_market_evidence(indication: dict[str, Any]) -> bool:
    evidence = list(indication.get("trialEvidence") or [])
    evidence.append(indication)
    return any(is_explicit_overseas_market_status(item) for item in evidence)


def _has_overseas_peak_anchor(overseas: dict[str, Any]) -> bool:
    return _positive_peak_anchor(overseas.get("estimatedPeakSalesUsd")) is not None


def _positive_peak_anchor(value: Any) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > 0
    ):
        return float(value)
    return None


def _not_overseas_reason(overseas: dict[str, Any]) -> str:
    if not _has_overseas_peak_anchor(overseas):
        return "DrugAlpha估值跟踪未提供有效海外峰值销售锚点，不生成海外患者流测算。"
    return "该适应症未被选为本产品海外市场测算基准。"


def _indication_space_score(
    indication_source: dict[str, Any],
    diseases: dict[str, dict[str, Any]],
) -> float:
    disease = diseases.get((indication_source.get("diseaseMatch") or {}).get("diseaseKey")) or {}
    for market in ("us", "china"):
        base_count, _metric = _base_patient_count(_find_region(disease, market), disease)
        if base_count is not None:
            return base_count
    text = indication_source.get("indicationText") or ""
    broad_disease_keywords = ["哮喘", "COPD", "特应性皮炎", "胃癌", "多发性骨髓瘤", "过敏性鼻炎"]
    return 1.0 if any(keyword in text for keyword in broad_disease_keywords) else 0.0


def _indication_identity(indication_source: dict[str, Any]) -> str:
    return (
        indication_source.get("sourceRowIdentity")
        or "|".join(
            str(indication_source.get(key) or "")
            for key in ("indicationText", "phase", "trialRegNo", "sponsor")
        )
    )


def _is_foreign_sponsor(sponsor: str | None) -> bool:
    value = (sponsor or "").strip()
    if not value or value.upper() in {"-", "—", "–", "N/A", "NA", "NONE", "NULL"}:
        return False
    known_foreign = [
        "astrazeneca",
        "阿斯利康",
        "merck",
        "默沙东",
        "roche",
        "罗氏",
        "novartis",
        "诺华",
        "pfizer",
        "辉瑞",
        "bristol",
        "百时美施贵宝",
        "bms",
        "eli lilly",
        "礼来",
        "sanofi",
        "赛诺菲",
        "gsk",
        "葛兰素史克",
        "johnson",
        "强生",
        "abbvie",
        "艾伯维",
        "amgen",
        "安进",
        "takeda",
        "武田",
        "daiichi",
        "第一三共",
        "gilead",
        "吉利德",
        "regeneron",
        "再生元",
        "biogen",
        "渤健",
    ]
    lowered = value.lower()
    if any(name in lowered or name in value for name in known_foreign):
        return True
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", value))
    return not has_cjk


def _has_foreign_sponsor_evidence(indication: dict[str, Any]) -> bool:
    sponsors: list[Any] = [
        indication.get("sponsor"),
        *(indication.get("sponsors") or []),
    ]
    for evidence in indication.get("trialEvidence") or []:
        sponsors.append(evidence.get("sponsor"))
    return any(_is_foreign_sponsor(sponsor) for sponsor in sponsors)


def _exclude_from_domestic_market(indication: dict[str, Any]) -> bool:
    """Exclude foreign-only development while retaining a parallel China trial."""

    if not _has_foreign_sponsor_evidence(indication):
        return False
    if any(_is_domestic_sponsor(sponsor) for sponsor in indication.get("sponsors") or []):
        return False

    evidence_rows = list(indication.get("trialEvidence") or [])
    if not evidence_rows:
        evidence_rows = [indication]
    return not any(_is_domestic_development_evidence(item) for item in evidence_rows)


def _is_domestic_development_evidence(evidence: dict[str, Any]) -> bool:
    sponsor = evidence.get("sponsor")
    if _is_foreign_sponsor(sponsor):
        return False
    if _is_domestic_sponsor(sponsor):
        return True

    trial_reg_no = str(
        evidence.get("trial_reg_no") or evidence.get("trialRegNo") or ""
    ).upper()
    if re.search(r"(?:^|[^A-Z0-9])(?:CTR|CHICTR)\d+", trial_reg_no):
        return True
    enrollment = str(
        evidence.get("enrollment_text") or evidence.get("enrollmentText") or ""
    )
    return "国内" in enrollment or "中国" in enrollment


def _is_domestic_sponsor(sponsor: Any) -> bool:
    value = str(sponsor or "").strip()
    if not value or value.upper() in {"-", "—", "–", "N/A", "NA", "NONE", "NULL"}:
        return False
    return not _is_foreign_sponsor(value) and bool(re.search(r"[\u4e00-\u9fff]", value))


def _default_treatment_months(line_of_therapy: str | None) -> float:
    if line_of_therapy == "一线":
        return 10.0
    if line_of_therapy == "二线":
        return 7.0
    if line_of_therapy in {"三线", "后线", "围手术期"}:
        return 6.0
    return 8.0


def _phase_launch_year(phase: str | None) -> int | None:
    if phase in PHASE_TO_LAUNCH_YEAR:
        return PHASE_TO_LAUNCH_YEAR[phase]
    value = re.sub(r"\s+", "", str(phase or "").upper())
    if is_marketed_phase(value):
        return PHASE_TO_LAUNCH_YEAR["已上市"]
    if is_nda_bla_phase(value):
        return PHASE_TO_LAUNCH_YEAR["NDA"]
    if re.search(r"(^|[^0-9])3期($|[^0-9])", value) or "III期" in value or "PHASE3" in value or "PHASEIII" in value:
        return PHASE_TO_LAUNCH_YEAR["3期"]
    return None


def _phase_pos(phase: str | None) -> float | None:
    return phase_success_probability(phase)


def _backsolve_penetration_curve(
    base_patient_count_10k: float | None,
    patient_growth_rate: float,
    eligible_patient_ratio: float,
    monthly_price: float | list[float],
    treatment_months: float,
    peak_sales_anchor: float | None,
    eligible_indication_count: int,
    launch_year: int | None,
    peak_year: int | None,
    market: str,
) -> list[float | None]:
    if (
        base_patient_count_10k is None
        or base_patient_count_10k <= 0
        or peak_sales_anchor is None
        or launch_year is None
        or peak_year is None
        or eligible_patient_ratio <= 0
        or treatment_months <= 0
    ):
        return [None for _ in FORECAST_YEARS]

    peak_index = max(peak_year - FORECAST_YEARS[0], 0)
    peak_price = (
        float(monthly_price[min(peak_index, len(monthly_price) - 1)])
        if isinstance(monthly_price, list) and monthly_price
        else float(monthly_price)
    )
    if peak_price <= 0:
        return [None for _ in FORECAST_YEARS]
    peak_patients = (
        base_patient_count_10k
        * ((1 + patient_growth_rate) ** peak_index)
        * eligible_patient_ratio
    )
    annual_cost = peak_price * treatment_months
    peak_sales_per_indication = peak_sales_anchor / max(eligible_indication_count, 1)
    peak_penetration = peak_sales_per_indication / (peak_patients * annual_cost)
    peak_penetration = min(max(peak_penetration, 0.0), 0.65)

    return penetration_curve_from_peak(
        years=FORECAST_YEARS,
        peak_penetration=peak_penetration,
        launch_year=launch_year,
        peak_year=peak_year,
        market=market,
    )


def _find_region(disease: dict[str, Any], market: str) -> dict[str, Any] | None:
    region_key = "china" if market == "china" else "us"
    for item in disease.get("regionalStats") or []:
        if item.get("key") == region_key:
            return item
    return None


def _base_patient_count(
    region: dict[str, Any] | None,
    disease: dict[str, Any] | None = None,
) -> tuple[float | None, str]:
    is_tumor = str((disease or {}).get("areaKey") or "") in {
        "oncology-solid",
        "hematology",
    }
    preferred_metric = "incidence" if is_tumor else "prevalence"
    fallback_metric = "prevalence" if is_tumor else "incidence"
    if not region:
        return None, preferred_metric
    values = {
        "incidence": region.get("incidenceCount10k"),
        "prevalence": region.get("prevalenceCount10k"),
    }
    if values[preferred_metric] is not None:
        return values[preferred_metric], preferred_metric
    if values[fallback_metric] is not None:
        return values[fallback_metric], fallback_metric
    return None, preferred_metric


def _population_filters(disease: dict[str, Any]) -> list[PopulationFilter]:
    return [
        PopulationFilter(
            key=item.get("key") or "",
            label=item.get("label") or "",
            ratio=item.get("ratioMidpoint"),
            raw_text=item.get("countRaw"),
            source="disease-knowledge",
            requires_user_input=item.get("ratioMidpoint") is None,
            source_details=list(item.get("sourceDetails") or []),
        )
        for item in (disease.get("subgroupCounts") or [])
    ]


def _resolve_patient_population(
    region: dict[str, Any] | None,
    disease: dict[str, Any],
    population_filters: list[PopulationFilter],
) -> tuple[float | None, str, list[dict[str, Any]]]:
    """Return the patient base after explicit parent-disease conversions.

    The output remains the editable "患者规模" row. Downstream biomarker and
    treatment-status filters are intentionally kept separate, so the Excel
    funnel shows each denominator exactly once.
    """

    base_count, metric = _base_patient_count(region, disease)
    source_notes = _source_notes(region, metric)
    if base_count is None or not region:
        return base_count, metric, source_notes

    rule = BASE_POPULATION_SUBGROUP_RULES.get(str(disease.get("diseaseKey") or ""))
    if not rule:
        return base_count, metric, source_notes

    raw_base = str(region.get(f"{metric}CountRaw") or "")
    if str(rule["parent_population_marker"]) not in raw_base:
        return base_count, metric, source_notes

    subgroup = next(
        (item for item in population_filters if item.key == rule["filter_key"]),
        None,
    )
    if subgroup is None or not isinstance(subgroup.ratio, (int, float)):
        return base_count, metric, source_notes

    adjusted_count = round(base_count * float(subgroup.ratio), 6)
    source_notes.append(
        {
            "field": "base_patient_count_adjustment",
            "rawValue": (
                f"{raw_base} × {subgroup.label} {float(subgroup.ratio):.1%} "
                f"= {adjusted_count:.1f}万人"
            ),
            "sourceDetails": list(subgroup.source_details),
        }
    )
    return adjusted_count, metric, source_notes


def _source_notes(
    region: dict[str, Any] | None,
    patient_metric: str,
) -> list[dict[str, Any]]:
    if not region:
        return []
    source_field = "prevalenceCount" if patient_metric == "prevalence" else "incidenceCount"
    return [
        {
            "field": source_field,
            "rawValue": region.get(f"{patient_metric}CountRaw"),
            "sourceDetails": region.get("sourceDetails", {}).get(source_field, []),
        }
    ]


def _infer_line_of_therapy(text: str | None) -> str | None:
    value = text or ""
    if re.search(r"(?:经|接受过?).{0,10}二线.{0,10}(?:失败|进展)", value):
        return "三线"
    if re.search(r"(?:经|接受过?).{0,10}一线.{0,10}(?:失败|进展)", value):
        return "二线"
    if "二线及以上" in value or "二线以上" in value:
        return "二线"
    if "一线" in value or "1L" in value:
        return "一线"
    if "二线" in value or "2L" in value:
        return "二线"
    if "三线" in value or "3L" in value:
        return "三线"
    if "后线" in value:
        return "后线"
    if "围手术" in value:
        return "围手术期"
    return None


def _stable_id(value: str | None) -> str:
    raw = (value or "unknown").lower()
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", raw).strip("_") or "unknown"


def _warning_from_source(item: dict[str, Any]) -> ModelWarning:
    return ModelWarning(
        level=item.get("level") or "info",
        code=item.get("code") or "SOURCE_WARNING",
        message=item.get("message") or "",
        product_name=item.get("productName"),
        indication_text=item.get("indicationText"),
    )
