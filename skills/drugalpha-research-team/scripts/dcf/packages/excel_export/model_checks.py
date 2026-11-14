"""Generation-time reconciliation and anomaly checks for DCF exports."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from packages.dcf_core.phase_rules import is_marketed_phase


MODEL_CHECK_TYPE = "drugalpha_dcf_model_check_v1"


def build_model_check_report(
    draft: dict[str, Any],
    valuation: dict[str, Any],
    financial_assumptions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    years = valuation.get("forecastYears") or draft.get("forecast_years") or []
    base_result = next(
        (item for item in valuation.get("scenarioResults") or [] if item.get("scenario") == "base"),
        (valuation.get("scenarioResults") or [{}])[0],
    )
    checks: list[dict[str, Any]] = []

    _record(
        checks,
        "forecast_years",
        "company",
        _valid_forecast_years(years),
        "预测年份完整且按升序排列",
    )

    product_results = base_result.get("productResults") or []
    product_ids = [str(item.get("productId") or "") for item in product_results]
    _record(
        checks,
        "unique_product_ids",
        "company",
        bool(product_ids) and len(product_ids) == len(set(product_ids)) and all(product_ids),
        "产品估值结果存在且产品ID不重复",
    )

    expected_company_revenue = [0.0] * len(years)
    for product in product_results:
        product_id = str(product.get("productId") or product.get("drugName") or "未命名产品")
        product_revenue = _numeric_vector(product.get("totalRevenueCny100m"), len(years))
        _record(
            checks,
            "product_revenue_vector",
            product_id,
            product_revenue is not None,
            "产品收入序列与预测年份一致且均为有限数值",
        )
        if product_revenue is None:
            continue
        _record(
            checks,
            "product_revenue_non_negative",
            product_id,
            all(value >= -1e-8 for value in product_revenue),
            "产品收入不存在负值",
        )
        expected_company_revenue = [
            expected_company_revenue[idx] + product_revenue[idx]
            for idx in range(len(years))
        ]

        indication_results = product.get("indicationResults") or []
        if indication_results:
            indication_sum = [0.0] * len(years)
            for indication in indication_results:
                indication_id = str(
                    indication.get("indicationId")
                    or indication.get("indicationText")
                    or "未命名适应症"
                )
                indication_revenue = _numeric_vector(indication.get("totalRevenueCny100m"), len(years))
                _record(
                    checks,
                    "indication_revenue_vector",
                    f"{product_id}/{indication_id}",
                    indication_revenue is not None,
                    "适应症收入序列与预测年份一致且均为有限数值",
                )
                if indication_revenue is None:
                    continue
                indication_sum = [
                    indication_sum[idx] + indication_revenue[idx]
                    for idx in range(len(years))
                ]
                _check_indication_markets(checks, product_id, indication, years, indication_revenue)
            _record_vector_tie(
                checks,
                "product_indication_tie",
                product_id,
                product_revenue,
                indication_sum,
                "产品收入等于各适应症收入之和",
            )

        for reconciliation in product.get("peakAnchorReconciliation") or []:
            anchor = _finite_float(reconciliation.get("productPeakSalesAnchor"))
            allocated = _finite_float(reconciliation.get("allocatedPeakSalesSum"))
            gross_pre_pos = _finite_float(reconciliation.get("grossPrePosPeakSum"))
            market = str(reconciliation.get("market") or "market")
            if anchor is None or anchor <= 0:
                continue
            allocation_tolerance = max(0.05, abs(anchor) * 0.01)
            pre_pos_tolerance = max(0.05, abs(anchor) * 0.10)
            _record_numeric_tie(
                checks,
                "peak_anchor_allocation",
                f"{product_id}/{market}",
                allocated,
                anchor,
                allocation_tolerance,
                "适应症峰值锚点分配合计与产品峰值一致",
            )
            if gross_pre_pos is not None:
                _record_numeric_tie(
                    checks,
                    "peak_anchor_pre_pos",
                    f"{product_id}/{market}",
                    gross_pre_pos,
                    anchor,
                    pre_pos_tolerance,
                    "POS前峰值销售合计与产品峰值锚点偏差不超过10%",
                )
        _check_product_valuation_sanity(checks, product)

    company_revenue = _numeric_vector(base_result.get("companyRevenueCny100m"), len(years))
    _record(
        checks,
        "company_revenue_vector",
        "company",
        company_revenue is not None,
        "公司收入序列与预测年份一致且均为有限数值",
    )
    if company_revenue is not None:
        _record_vector_tie(
            checks,
            "company_product_tie",
            "company",
            company_revenue,
            expected_company_revenue,
            "公司收入等于全部产品收入之和",
        )

    company_fcf = _numeric_vector(base_result.get("companyFreeCashFlowCny100m"), len(years))
    _record(
        checks,
        "company_fcf_vector",
        "company",
        company_fcf is not None,
        "公司自由现金流序列与预测年份一致且均为有限数值",
    )
    _check_company_dcf(checks, base_result.get("companyDcf") or {}, company_fcf)
    _check_business_scenarios(checks, valuation.get("scenarioResults") or [])
    _check_marketed_launch_dates(checks, draft)
    _check_financial_inputs(checks, draft, base_result, financial_assumptions)

    failed = sum(1 for item in checks if item["status"] == "fail")
    warnings = sum(1 for item in checks if item["status"] == "warning")
    if failed:
        status = "fail"
        display_status = f"{failed}项异常"
    elif warnings:
        status = "review"
        display_status = f"{warnings}项需复核"
    else:
        status = "pass"
        display_status = "通过"
    return {
        "modelType": MODEL_CHECK_TYPE,
        "companyName": valuation.get("companyName") or draft.get("company_name"),
        "forecastYears": years,
        "status": status,
        "displayStatus": display_status,
        "checkCount": len(checks),
        "failedCount": failed,
        "warningCount": warnings,
        "checks": checks,
    }


def _check_marketed_launch_dates(
    checks: list[dict[str, Any]],
    draft: dict[str, Any],
) -> None:
    for product in draft.get("products") or []:
        product_name = str(product.get("drug_name") or product.get("product_id") or "未命名产品")
        for indication in product.get("indications") or []:
            if not is_marketed_phase(indication.get("phase")):
                continue
            indication_name = str(
                indication.get("indication_name")
                or indication.get("indication")
                or indication.get("indication_text")
                or "未命名适应症"
            )
            has_actual_date = bool(str(indication.get("status_start_date") or "").strip())
            if has_actual_date:
                actual_year = _extract_year(indication.get("status_start_date"))
                launch_year = indication.get("launch_year")
                _record(
                    checks,
                    "marketed_launch_year_alignment",
                    f"{product_name}/{indication_name}",
                    actual_year is not None and launch_year == actual_year,
                    "已上市适应症的收入起算年与实际获批年份一致",
                    actual=launch_year,
                    expected=actual_year,
                )
                for market in indication.get("markets") or []:
                    if str(market.get("market") or "") != "china":
                        continue
                    _record(
                        checks,
                        "marketed_domestic_market_launch_alignment",
                        f"{product_name}/{indication_name}/china",
                        actual_year is not None and market.get("launch_year") == actual_year,
                        "已上市国内市场按实际获批年份进入对应销售生命周期",
                        actual=market.get("launch_year"),
                        expected=actual_year,
                    )
                continue
            _record_warning(
                checks,
                "marketed_launch_date_missing",
                f"{product_name}/{indication_name}",
                "已上市适应症缺少实际获批日期，收入节奏正在使用回退上市年，需复核",
            )


def _check_financial_inputs(
    checks: list[dict[str, Any]],
    draft: dict[str, Any],
    base_result: dict[str, Any],
    financial_assumptions: dict[str, Any] | None,
) -> None:
    if not _has_supported_stock_code(draft.get("stock_codes")):
        return
    if not financial_assumptions:
        _record_warning(
            checks,
            "financial_assumptions_missing",
            "company",
            "公司已有证券代码，但导出未带入iFinD/备选财务假设，经营曲线和每股价值需复核",
        )
        return

    source_status = financial_assumptions.get("sourceStatus") or {}
    provider = str(source_status.get("historyProvider") or "").strip()
    if not provider:
        _record_warning(
            checks,
            "financial_provider_missing",
            "company",
            "财务假设缺少数据提供方标识，需确认是否实际调用iFinD或备选数据源",
        )
    if source_status.get("mcpHistory"):
        gaps = source_status.get("missingCoreFields") or []
        missing_years = source_status.get("missingHistoricalYears") or []
        if gaps or missing_years:
            _record_warning(
                checks, "mcp_financial_history_incomplete", "company",
                "金融MCP历史财务有缺项，预测曲线按原生规则回填，需复核。",
                actual={"fields": gaps, "years": missing_years},
            )
    if source_status.get("ifindCurrent") is False:
        _record_warning(
            checks,
            "ifind_current_history_unavailable",
            "company",
            "本次未取得最新年度iFinD营业收入，已明确使用备选数据或缺失回退规则",
            actual=source_status.get("ifindStatus"),
        )
    if source_status.get("ifindCoreComplete") is False:
        _record_warning(
            checks,
            "ifind_core_fields_incomplete",
            "company",
            "iFinD核心财务字段未完整取得，缺失字段将采用备选数据或明确回退规则",
            actual=source_status.get("ifindMissingCoreFields") or [],
        )

    share_count = _finite_float(financial_assumptions.get("shareCount100m"))
    if share_count is None or share_count <= 0:
        _record_warning(
            checks,
            "share_count_missing",
            "company",
            "总股本未取得，每股价值将无法计算",
        )
    else:
        _record_review_check(
            checks,
            "share_count_unit_sanity",
            "company",
            0.01 <= share_count <= 100.0,
            "总股本数量级合理",
            "总股本数量级异常，需确认是否按亿股口径传入",
            actual=share_count,
            expected={"unit": "亿股", "reviewRange": [0.01, 100.0]},
        )
        enterprise_value = _finite_float((base_result.get("companyDcf") or {}).get("enterpriseValueCny100m"))
        if enterprise_value is not None and enterprise_value > 0:
            implied_per_share = enterprise_value / share_count
            _record_review_check(
                checks,
                "implied_per_share_unit_sanity",
                "company",
                0.01 <= implied_per_share <= 1000.0,
                "每股价值数量级合理",
                "每股价值数量级异常，需复核企业价值与总股本单位",
                actual=round(implied_per_share, 6),
                expected={"unit": "元/股", "reviewRange": [0.01, 1000.0]},
            )

    latest_historical = financial_assumptions.get("latestHistorical") or {}
    historical_revenue = _finite_float(latest_historical.get("revenue"))
    forecast_years = draft.get("forecast_years") or []
    first_forecast_year = forecast_years[0] if forecast_years else None
    historical_year = latest_historical.get("year")
    historical_revenue_source = (
        (latest_historical.get("field_sources") or {}).get("revenue")
        or latest_historical.get("source")
    )
    has_mcp_history = bool((financial_assumptions.get("sourceStatus") or {}).get("mcpHistory"))
    has_yoy_base = (
        isinstance(first_forecast_year, int)
        and historical_year == first_forecast_year - 1
        and historical_revenue is not None
        and historical_revenue > 0
        and (has_mcp_history or str(historical_revenue_source or "").startswith("ifind_quantapi_http"))
    )
    if not has_yoy_base:
        _record_warning(
            checks,
            "previous_year_revenue_missing",
            "company",
            "未取得首个预测年前一年的可核营业收入，首年YoY应保持空白",
            actual={
                "year": historical_year,
                "source": historical_revenue_source,
            },
            expected={"year": first_forecast_year - 1 if isinstance(first_forecast_year, int) else None},
        )
    forecast_revenue = _numeric_vector(
        base_result.get("companyRevenueCny100m"),
        len(base_result.get("companyRevenueCny100m") or []),
    )
    if historical_revenue is None or historical_revenue <= 0 or not forecast_revenue:
        return
    first_forecast_revenue = forecast_revenue[0]
    if first_forecast_revenue + 1e-8 < historical_revenue * 0.5:
        _record_warning(
            checks,
            "first_year_revenue_bridge",
            "company",
            "首个预测年产品收入低于最近历史营业收入的50%，需检查已上市产品日期和收入衔接",
            actual=first_forecast_revenue,
            expected=historical_revenue,
        )
    first_year_yoy = first_forecast_revenue / historical_revenue - 1
    if first_year_yoy > 3.0:
        _record_warning(
            checks,
            "first_year_yoy_outlier",
            "company",
            "首个预测年YoY超过300%，需检查已上市产品收入衔接、里程碑收入和峰值爬坡",
            actual=round(first_year_yoy, 6),
            expected={"upperReviewThreshold": 3.0},
        )


def _has_supported_stock_code(value: Any) -> bool:
    import re

    text = str(value or "")
    return bool(
        re.search(r"(?<!\d)\d{1,5}\.HK\b", text, flags=re.IGNORECASE)
        or re.search(r"(?<!\d)\d{6}\.(?:SH|SZ|BJ)\b", text, flags=re.IGNORECASE)
    )


def _check_business_scenarios(
    checks: list[dict[str, Any]],
    scenario_results: list[dict[str, Any]],
) -> None:
    scenarios = {
        str(item.get("scenario") or "").lower(): item
        for item in scenario_results
        if str(item.get("scenario") or "").lower() in {"bear", "base", "bull"}
    }
    if not {"bear", "base", "bull"}.issubset(scenarios):
        return

    base_assumptions = scenarios["base"].get("assumptions") or {}
    unchanged_fields = [
        "penetration_multiplier",
        "price_multiplier",
        "launch_delay_years",
        "discount_rate",
        "terminal_growth_rate",
        "gross_margin",
        "sales_expense_rate",
        "rd_expense_rate",
        "admin_expense_rate",
        "tax_rate",
    ]
    same_assumptions = all(
        (scenarios[name].get("assumptions") or {}).get(field) == base_assumptions.get(field)
        for name in ("bear", "bull")
        for field in unchanged_fields
    )
    _record(
        checks,
        "scenario_single_driver",
        "company",
        same_assumptions,
        "Bear/Base/Bull除产品收入兑现率外其余假设一致",
    )

    expected_realization = {"bear": 0.80, "base": 1.00, "bull": 1.20}
    realization_values = {
        name: _finite_float(
            (scenarios[name].get("assumptions") or {}).get("revenue_realization_multiplier")
        )
        for name in expected_realization
    }
    realization_ok = all(
        realization_values[name] is not None
        and abs(realization_values[name] - expected) <= 1e-8
        for name, expected in expected_realization.items()
    )
    _record(
        checks,
        "scenario_revenue_realization",
        "company",
        realization_ok,
        "Bear/Base/Bull产品收入兑现率分别为80%/100%/120%",
    )

    enterprise_values = {
        name: _finite_float((scenarios[name].get("companyDcf") or {}).get("enterpriseValueCny100m"))
        for name in ("bear", "base", "bull")
    }
    ordered = (
        all(value is not None for value in enterprise_values.values())
        and enterprise_values["bear"] <= enterprise_values["base"] + 0.02
        and enterprise_values["base"] <= enterprise_values["bull"] + 0.02
    )
    _record(
        checks,
        "scenario_value_order",
        "company",
        ordered,
        "情景企业价值满足Bear不高于Base、Base不高于Bull",
    )


def write_model_check_report(output_path: str | Path, report: dict[str, Any]) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _check_indication_markets(
    checks: list[dict[str, Any]],
    product_id: str,
    indication: dict[str, Any],
    years: list[int],
    indication_revenue: list[float],
) -> None:
    indication_id = str(indication.get("indicationId") or indication.get("indicationText") or "未命名适应症")
    markets = []
    for market in indication.get("marketResults") or []:
        market_name = str(market.get("market") or "market")
        scope = f"{product_id}/{indication_id}/{market_name}"
        adjusted = _numeric_vector(market.get("equityAdjustedRevenueCny100m"), len(years))
        gross = _numeric_vector(market.get("grossSales"), len(years))
        _record(
            checks,
            "market_revenue_vector",
            scope,
            adjusted is not None and gross is not None,
            "市场收入序列与预测年份一致且均为有限数值",
        )
        if adjusted is None or gross is None:
            continue
        markets.append(adjusted)
        _record(
            checks,
            "market_revenue_non_negative",
            scope,
            all(value >= -1e-8 for value in adjusted + gross),
            "市场销售额不存在负值",
        )
        launch_year = market.get("launchYear")
        if isinstance(launch_year, int):
            pre_launch = [adjusted[idx] for idx, year in enumerate(years) if year < launch_year]
            _record(
                checks,
                "pre_launch_revenue",
                scope,
                all(abs(value) <= 0.01 for value in pre_launch),
                "上市前风险/权益调整收入为0",
            )
        _check_ratio(checks, scope, "success_probability", market.get("successProbability"))
        bridge = market.get("economicsBridgeAtPeak") or {}
        _check_ratio(checks, scope, "economic_share_ratio", bridge.get("economicShareRatio"))
        diagnostics = market.get("patientFlowDiagnostics") or {}
        _check_ratio(checks, scope, "biomarker_ratio", diagnostics.get("biomarkerRatio"))
        _check_ratio(checks, scope, "target_treatment_ratio", diagnostics.get("targetTreatmentRatio"))
        _check_ratio(checks, scope, "peak_penetration", diagnostics.get("maxPenetration"))
        if market_name != "china":
            success_probability = _finite_float(market.get("successProbability"))
            economic_share = _finite_float(bridge.get("economicShareRatio"))
            exchange_rate = _finite_float(bridge.get("exchangeRate"))
            if None in {success_probability, economic_share, exchange_rate}:
                _record_warning(
                    checks,
                    "overseas_revenue_bridge",
                    scope,
                    "海外收入桥缺少POS、分成比例或汇率，需补充后复核",
                )
            else:
                expected_adjusted = [
                    value * success_probability * economic_share * exchange_rate
                    for value in gross
                ]
                _record_vector_tie(
                    checks,
                    "overseas_revenue_bridge",
                    scope,
                    adjusted,
                    expected_adjusted,
                    "海外收入等于总销售额×POS×分成比例×汇率",
                )

    if markets:
        market_sum = [sum(vector[idx] for vector in markets) for idx in range(len(years))]
        _record_vector_tie(
            checks,
            "indication_market_tie",
            f"{product_id}/{indication_id}",
            indication_revenue,
            market_sum,
            "适应症收入等于国内/海外市场收入之和",
        )


def _check_product_valuation_sanity(
    checks: list[dict[str, Any]],
    product: dict[str, Any],
) -> None:
    enterprise_value = _finite_float((product.get("dcf") or {}).get("enterpriseValueCny100m"))
    if enterprise_value is None:
        return
    product_id = str(product.get("productId") or product.get("drugName") or "未命名产品")
    exchange_rates: dict[str, float] = {}
    for indication in product.get("indicationResults") or []:
        for market in indication.get("marketResults") or []:
            market_name = str(market.get("market") or "")
            exchange_rate = _finite_float((market.get("economicsBridgeAtPeak") or {}).get("exchangeRate"))
            if market_name and exchange_rate is not None and exchange_rate > 0:
                exchange_rates[market_name] = exchange_rate

    gross_peak_cny = 0.0
    has_comparable_anchor = False
    for reconciliation in product.get("peakAnchorReconciliation") or []:
        market_name = str(reconciliation.get("market") or "")
        anchor = _finite_float(reconciliation.get("productPeakSalesAnchor"))
        if anchor is None or anchor <= 0:
            continue
        if market_name == "china":
            gross_peak_cny += anchor
            has_comparable_anchor = True
            continue
        exchange_rate = exchange_rates.get(market_name)
        if exchange_rate is not None:
            gross_peak_cny += anchor * exchange_rate
            has_comparable_anchor = True

    if not has_comparable_anchor or gross_peak_cny <= 0:
        return
    value_to_peak = enterprise_value / gross_peak_cny
    _record_review_check(
        checks,
        "product_value_to_peak_sales",
        product_id,
        enterprise_value >= 0 and value_to_peak <= 10.0,
        "产品DCF价值与POS前未调整峰值销售处于合理数量级",
        "产品DCF价值明显高于POS前未调整峰值销售，需复核峰值、费用率和终值",
        actual={
            "productDcfCny100m": round(enterprise_value, 6),
            "grossPeakSalesCny100m": round(gross_peak_cny, 6),
            "valueToPeak": round(value_to_peak, 6),
        },
        expected={"upperReviewThreshold": 10.0},
    )


def _check_company_dcf(
    checks: list[dict[str, Any]],
    dcf: dict[str, Any],
    company_fcf: list[float] | None,
) -> None:
    wacc = _finite_float(dcf.get("discountRate"))
    growth = _finite_float(dcf.get("terminalGrowthRate"))
    _record(
        checks,
        "wacc_terminal_growth",
        "company",
        wacc is not None and growth is not None and wacc > growth,
        "WACC高于永续增长率",
    )
    if wacc is not None:
        _record_review_check(
            checks,
            "wacc_range_sanity",
            "company",
            0.05 <= wacc <= 0.20,
            "WACC处于常用合理性校验范围",
            "WACC超出5%至20%的常用校验范围，需复核",
            actual=wacc,
            expected={"reviewRange": [0.05, 0.20]},
        )
    if growth is not None:
        _record_review_check(
            checks,
            "terminal_growth_range_sanity",
            "company",
            -0.02 <= growth <= 0.05,
            "永续增长率处于合理性校验范围",
            "永续增长率超出-2%至5%的校验范围，需复核",
            actual=growth,
            expected={"reviewRange": [-0.02, 0.05]},
        )
    forecast_pv = _finite_float(dcf.get("pvForecastFcfCny100m"))
    terminal_pv = _finite_float(dcf.get("pvTerminalValueCny100m"))
    enterprise_value = _finite_float(dcf.get("enterpriseValueCny100m"))
    expected = None if forecast_pv is None or terminal_pv is None else forecast_pv + terminal_pv
    _record_numeric_tie(
        checks,
        "enterprise_value_tie",
        "company",
        enterprise_value,
        expected,
        0.02,
        "企业价值等于预测期FCF现值与终值现值之和",
    )
    if enterprise_value is not None and enterprise_value > 0 and terminal_pv is not None:
        terminal_value_share = terminal_pv / enterprise_value
        _record_review_check(
            checks,
            "terminal_value_share_sanity",
            "company",
            terminal_value_share <= 0.80,
            "终值现值占企业价值比例不高于80%",
            "终值现值占企业价值比例超过80%，估值对远期假设较敏感",
            actual=round(terminal_value_share, 6),
            expected={"upperReviewThreshold": 0.80},
        )
    if company_fcf:
        _record_review_check(
            checks,
            "terminal_year_fcf_sanity",
            "company",
            company_fcf[-1] > 0,
            "末年自由现金流为正，可支持Gordon Growth终值",
            "末年自由现金流不为正，Gordon Growth终值需复核",
            actual=company_fcf[-1],
            expected={"lowerBoundExclusive": 0},
        )


def _check_ratio(checks: list[dict[str, Any]], scope: str, check_id: str, value: Any) -> None:
    numeric = _finite_float(value)
    labels = {
        "success_probability": "上市概率",
        "economic_share_ratio": "权益/分成比例",
        "biomarker_ratio": "Biomarker比例",
        "target_treatment_ratio": "目标治疗人群比例",
        "peak_penetration": "峰值渗透率",
    }
    label = labels.get(check_id, "比例")
    if numeric is None:
        _record_warning(
            checks,
            check_id,
            scope,
            f"缺少{label}有效值，需补充或复核",
        )
        return
    _record(
        checks,
        check_id,
        scope,
        0 <= numeric <= 1,
        f"{label}位于0%至100%之间",
        actual=numeric,
    )


def _record_vector_tie(
    checks: list[dict[str, Any]],
    check_id: str,
    scope: str,
    actual: list[float],
    expected: list[float],
    message: str,
    tolerance: float = 0.02,
) -> None:
    differences = [abs(actual[idx] - expected[idx]) for idx in range(min(len(actual), len(expected)))]
    maximum = max(differences, default=math.inf)
    _record(
        checks,
        check_id,
        scope,
        len(actual) == len(expected) and maximum <= tolerance,
        message,
        actual={"maxAbsDifference": round(maximum, 6)},
        expected={"tolerance": tolerance},
    )


def _record_numeric_tie(
    checks: list[dict[str, Any]],
    check_id: str,
    scope: str,
    actual: float | None,
    expected: float | None,
    tolerance: float,
    message: str,
) -> None:
    difference = None if actual is None or expected is None else abs(actual - expected)
    _record(
        checks,
        check_id,
        scope,
        difference is not None and difference <= tolerance,
        message,
        actual=actual,
        expected=expected,
        tolerance=tolerance,
    )


def _record(
    checks: list[dict[str, Any]],
    check_id: str,
    scope: str,
    passed: bool,
    message: str,
    *,
    actual: Any = None,
    expected: Any = None,
    tolerance: float | None = None,
) -> None:
    item = {
        "id": check_id,
        "scope": scope,
        "status": "pass" if passed else "fail",
        "message": message,
    }
    if actual is not None:
        item["actual"] = actual
    if expected is not None:
        item["expected"] = expected
    if tolerance is not None:
        item["tolerance"] = tolerance
    checks.append(item)


def _record_warning(
    checks: list[dict[str, Any]],
    check_id: str,
    scope: str,
    message: str,
    *,
    actual: Any = None,
    expected: Any = None,
) -> None:
    item = {
        "id": check_id,
        "scope": scope,
        "status": "warning",
        "message": message,
    }
    if actual is not None:
        item["actual"] = actual
    if expected is not None:
        item["expected"] = expected
    checks.append(item)


def _record_review_check(
    checks: list[dict[str, Any]],
    check_id: str,
    scope: str,
    passed: bool,
    pass_message: str,
    warning_message: str,
    *,
    actual: Any = None,
    expected: Any = None,
) -> None:
    if passed:
        _record(
            checks,
            check_id,
            scope,
            True,
            pass_message,
            actual=actual,
            expected=expected,
        )
        return
    _record_warning(
        checks,
        check_id,
        scope,
        warning_message,
        actual=actual,
        expected=expected,
    )


def _valid_forecast_years(years: Any) -> bool:
    return (
        isinstance(years, list)
        and bool(years)
        and all(isinstance(year, int) and not isinstance(year, bool) for year in years)
        and years == sorted(set(years))
    )


def _numeric_vector(value: Any, expected_length: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != expected_length:
        return None
    result = [_finite_float(item) for item in value]
    if any(item is None for item in result):
        return None
    return [float(item) for item in result if item is not None]


def _finite_float(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _extract_year(value: Any) -> int | None:
    match = re.search(r"(?:19|20|21)\d{2}", str(value or ""))
    return int(match.group(0)) if match else None
