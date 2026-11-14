"""Build indicator-level AI assumption proposal requests from DCF drafts."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .deepseek_client import chat_completion, extract_text
from .field_policy import AI_PREFILL, get_policy
from .indicator_prompts import get_prompt
from .public_web_search import dedupe_search_results, search_public_web


DEFAULT_FIELDS = [
    "base_patient_count_10k",
    "biomarker_ratio",
    "target_treatment_ratio",
    "patient_growth_rate",
    "population_filters",
    "penetration_curve",
    "monthly_price",
    "treatment_months",
    "peak_sales_anchor",
    "market_scope",
]


def build_indicator_tasks(
    draft: dict[str, Any],
    *,
    fields: list[str] | None = None,
    max_tasks: int = 20,
    public_search: bool = True,
) -> list[dict[str, Any]]:
    selected_fields = fields or DEFAULT_FIELDS
    tasks: list[dict[str, Any]] = []
    seen_task_keys: set[tuple[str, str, str, str]] = set()
    for product in draft.get("products") or []:
        if product.get("modeling_mode") == "simplified_growth":
            continue
        for indication in product.get("indications") or []:
            if _is_noncommercial_indication(indication):
                continue
            for market in indication.get("markets") or []:
                flow = market.get("patient_flow") or {}
                for field in selected_fields:
                    if not _needs_proposal(field, market, flow):
                        continue
                    task_key = tuple(
                        _normalized_key(value)
                        for value in (
                            product.get("drug_name"),
                            indication.get("indication_text"),
                            market.get("market"),
                            field,
                        )
                    )
                    if task_key in seen_task_keys:
                        continue
                    seen_task_keys.add(task_key)
                    tasks.append(
                        {
                            "taskId": f"patient-flow-{len(tasks) + 1}",
                            "field": field,
                            "_product": product,
                            "_indication": indication,
                            "_market": market,
                        }
                    )
                    if len(tasks) >= max_tasks:
                        return _attach_task_contexts(draft, tasks, public_search=public_search)
    return _attach_task_contexts(draft, tasks, public_search=public_search)


def _attach_task_contexts(draft: dict[str, Any], tasks: list[dict[str, Any]], *, public_search: bool = True) -> list[dict[str, Any]]:
    """Fetch independent public-search contexts concurrently, preserving task order."""

    if not tasks:
        return tasks
    with ThreadPoolExecutor(max_workers=min(6, len(tasks))) as executor:
        futures = [
            executor.submit(
                _task_context,
                draft,
                task.pop("_product"),
                task.pop("_indication"),
                task.pop("_market"),
                str(task["field"]),
                public_search=public_search,
            )
            for task in tasks
        ]
        for task, future in zip(tasks, futures):
            task["context"] = future.result()
    return tasks


def generate_indicator_proposals(
    draft: dict[str, Any],
    *,
    fields: list[str] | None = None,
    max_tasks: int = 20,
    batch_size: int = 6,
) -> dict[str, Any]:
    tasks = build_indicator_tasks(draft, fields=fields, max_tasks=max_tasks)
    proposals: list[dict[str, Any]] = []
    raw_batches: list[dict[str, Any]] = []
    for index in range(0, len(tasks), batch_size):
        batch = tasks[index : index + batch_size]
        messages = build_messages(draft, batch)
        try:
            response = chat_completion(
                messages,
                temperature=0.1,
                max_tokens=min(6500, 2500 + len(batch) * 550),
            )
            text = extract_text(response)
            parsed = _align_and_validate_proposals(_parse_json_array(text), batch)
            proposals.extend(parsed)
            raw_batches.append(
                {
                    "taskStart": index,
                    "taskCount": len(batch),
                    "model": response.get("model"),
                    "usage": response.get("usage"),
                    "rawText": text,
                    "parsedCount": len(parsed),
                }
            )
            parsed_task_ids = {str(item.get("taskId") or "") for item in parsed}
            for retry_task in batch:
                if str(retry_task.get("taskId") or "") in parsed_task_ids:
                    continue
                try:
                    retry_response = chat_completion(
                        build_messages(draft, [retry_task]),
                        temperature=0.1,
                        max_tokens=3200,
                    )
                    retry_text = extract_text(retry_response)
                    retry_parsed = _align_and_validate_proposals(
                        _parse_json_array(retry_text),
                        [retry_task],
                    )
                    proposals.extend(retry_parsed)
                    raw_batches.append(
                        {
                            "taskStart": index,
                            "taskCount": 1,
                            "retryTaskId": retry_task.get("taskId"),
                            "model": retry_response.get("model"),
                            "usage": retry_response.get("usage"),
                            "rawText": retry_text,
                            "parsedCount": len(retry_parsed),
                        }
                    )
                except Exception as retry_exc:
                    raw_batches.append(
                        {
                            "taskStart": index,
                            "taskCount": 1,
                            "retryTaskId": retry_task.get("taskId"),
                            "parsedCount": 0,
                            "error": str(retry_exc)[:500],
                        }
                    )
        except Exception as exc:  # Keep valid batches instead of failing the whole export.
            raw_batches.append(
                {
                    "taskStart": index,
                    "taskCount": len(batch),
                    "parsedCount": 0,
                    "error": str(exc)[:500],
                }
            )
    return {
        "companyName": draft.get("company_name"),
        "sourceSnapshotId": draft.get("source_snapshot_id"),
        "taskCount": len(tasks),
        "proposalCount": len(proposals),
        "missingTaskCount": max(len(tasks) - len({str(item.get('taskId') or '') for item in proposals}), 0),
        "batchErrorCount": sum(1 for item in raw_batches if item.get("error")),
        "tasks": tasks,
        "proposals": proposals,
        "rawBatches": raw_batches,
        "disclaimer": "AI 建议仅用于生成可编辑假设草稿，不构成投资建议；请研究员复核数据来源、口径和适用性。",
    }


def build_messages(draft: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict[str, str]]:
    system = (
        "你是创新药 DCF 投研建模助手。你只基于用户提供的上下文、公开常识和可审计假设生成草稿。"
        "context.publicSearchResults由项目在本次运行中实时检索公开网页得到，可用于相应任务；"
        "不得声称查询了未提供的外部数据库，凡需进一步确认必须写入 basisComment。"
        "basisComment 请控制在 100 个汉字以内，完整保留统计口径、不确定性和待核实点。"
        "不要输出推理过程，只输出可解析 JSON 数组。"
    )
    task_payload = []
    for task in tasks:
        prompt = get_prompt(task["field"])
        policy = get_policy(task["field"])
        task_payload.append(
            {
                "taskId": task.get("taskId"),
                "field": prompt.field,
                "title": prompt.title,
                "aiMode": policy.mode,
                "policyReason": policy.reason,
                "instruction": prompt.instruction,
                "outputHint": prompt.output_hint,
                "context": task["context"],
            }
        )
    user = {
        "company": draft.get("company_name"),
        "forecastYears": draft.get("forecast_years"),
        "schema": {
            "taskId": "string，必须原样返回任务中的 taskId",
            "drugName": "string",
            "indication": "string",
            "market": "china|us",
            "field": "string",
            "suggestedValue": "number|string|boolean|object|null",
            "suggestedCurve": "number[]|null",
            "basisComment": "中文，说明依据、口径、不确定性和待核实点",
            "confidence": "low|medium|high",
            "needsHumanReview": True,
            "aiMode": "ai_prefill|ai_check_only",
            "policyReason": "string",
            "sourceUrls": "仅可从任务context.publicSearchResults.url中选择；没有检索结果时为空数组",
        },
        "tasks": task_payload,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def _task_context(
    draft: dict[str, Any],
    product: dict[str, Any],
    indication: dict[str, Any],
    market: dict[str, Any],
    field: str,
    *,
    public_search: bool = True,
) -> dict[str, Any]:
    flow = market.get("patient_flow") or {}
    context = {
        "companyName": draft.get("company_name"),
        "forecastYears": draft.get("forecast_years"),
        "product": {
            "drugName": product.get("drug_name"),
            "target": product.get("target"),
            "domesticPeakSalesCny100m": product.get("domestic_peak_sales_cny_100m"),
            "overseasPeakSalesUsd100m": product.get("overseas_peak_sales_usd_100m"),
            "overseasRevenueShareRatio": product.get("overseas_revenue_share_ratio"),
            "overseasSuccessProbability": product.get("overseas_success_probability"),
        },
        "indication": {
            "text": indication.get("indication_text"),
            "phase": indication.get("phase"),
            "launchYear": indication.get("launch_year"),
            "peakYear": indication.get("peak_year"),
            "pos": indication.get("pos"),
            "trialRegNo": indication.get("trial_reg_no"),
            "sponsor": indication.get("sponsor"),
            "diseaseTitle": indication.get("disease_title"),
            "diseaseKey": indication.get("disease_key"),
            "overseasEligibilityReason": indication.get("overseas_eligibility_reason"),
        },
        "market": {
            "market": market.get("market"),
            "currency": market.get("currency"),
            "launchYear": market.get("launch_year") or indication.get("launch_year"),
            "launchYearBasis": market.get("launch_year_basis") or indication.get("launch_year_basis"),
            "peakYear": market.get("peak_year") or indication.get("peak_year"),
            "peakYearBasis": market.get("peak_year_basis") or indication.get("peak_year_basis"),
            "salesRampProfile": market.get("sales_ramp_profile"),
            "peakSalesAnchor": market.get("peak_sales_anchor"),
            "peakSalesAnchorUnit": market.get("peak_sales_anchor_unit"),
            "equityRatio": market.get("equity_ratio"),
            "revenueShareRatio": market.get("revenue_share_ratio"),
            "exchangeRate": market.get("exchange_rate"),
        },
        "currentPatientFlow": {
            "basePatientCount10k": flow.get("base_patient_count_10k"),
            "basePatientMetric": flow.get("base_patient_metric"),
            "patientGrowthRate": flow.get("patient_growth_rate"),
            "biomarkerRatio": flow.get("biomarker_ratio"),
            "biomarkerRatioSource": flow.get("biomarker_ratio_source"),
            "biomarkerRatioRequiresReview": flow.get("biomarker_ratio_requires_review"),
            "biomarkerSourceDetails": flow.get("biomarker_ratio_source_details"),
            "populationFilters": flow.get("population_filters"),
            "penetrationCurve": flow.get("penetration_curve"),
            "monthlyPrice": flow.get("monthly_price"),
            "treatmentMonths": flow.get("treatment_months"),
            "treatmentMonthsMode": flow.get("treatment_months_mode"),
            "treatmentMonthsSource": flow.get("treatment_months_source"),
            "drugCostCategory": flow.get("drug_cost_category"),
            "drugCostCategoryLabel": flow.get("drug_cost_category_label"),
            "priceBasis": flow.get("price_basis"),
            "priceSource": flow.get("price_source"),
            "sourceNotes": flow.get("source_notes"),
            "requiresUserInput": flow.get("requires_user_input"),
            "targetTreatmentRatio": flow.get("target_treatment_ratio"),
            "targetTreatmentRatioMode": flow.get("target_treatment_ratio_mode"),
            "targetTreatmentRatioSource": flow.get("target_treatment_ratio_source"),
            "targetTreatmentRatioRequiresResearch": flow.get("target_treatment_ratio_requires_research"),
        },
    }
    if not public_search:
        context["publicSearchResults"] = []
        context["searchProvider"] = "user_agent_pending"
        return context
    if field == "base_patient_count_10k":
        context["publicSearchResults"] = _patient_count_search_results(indication, market)
        context["searchProvider"] = "brave_html_with_duckduckgo_and_bing_fallback"
    elif field == "target_treatment_ratio":
        context["publicSearchResults"] = _target_ratio_search_results(indication, market)
        context["searchProvider"] = "brave_html_with_duckduckgo_and_bing_fallback"
    elif field == "biomarker_ratio":
        context["publicSearchResults"] = _biomarker_ratio_search_results(indication, market, flow)
        context["searchProvider"] = "brave_html_with_duckduckgo_and_bing_fallback"
    return context


def _needs_proposal(field: str, market: dict[str, Any], flow: dict[str, Any]) -> bool:
    if field == "base_patient_count_10k":
        return _policy_allows_task(field) and not _positive_number(flow.get("base_patient_count_10k"))
    if field == "target_treatment_ratio":
        return _policy_allows_task(field) and bool(flow.get("target_treatment_ratio_requires_research"))
    if field == "biomarker_ratio":
        return _policy_allows_task(field) and bool(flow.get("biomarker_ratio_requires_review"))
    if field == "patient_growth_rate":
        return _policy_allows_task(field) and not isinstance(flow.get("patient_growth_rate"), (int, float))
    if field == "population_filters":
        filters = flow.get("population_filters")
        return _policy_allows_task(field) and not (isinstance(filters, list) and filters)
    if field == "penetration_curve":
        return _policy_allows_task(field) and not _has_numeric_curve(flow.get("penetration_curve"))
    if field == "monthly_price":
        return _policy_allows_task(field) and not _has_positive_value(flow.get("monthly_price"))
    if field == "treatment_months":
        return _policy_allows_task(field) and (
            not _has_positive_value(flow.get("treatment_months"))
            or flow.get("treatment_months_mode") == "non_oncology_short_course"
        )
    if field == "peak_sales_anchor":
        return _policy_allows_task(field) and market.get("peak_sales_anchor") is None
    if field == "market_scope":
        return _policy_allows_task(field) and bool(market.get("market") == "us")
    return False


def _policy_allows_task(field: str) -> bool:
    return get_policy(field).mode in {AI_PREFILL, "ai_check_only"}


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("["):
        match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
        if match:
            cleaned = match.group(0)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    return [item for item in parsed if isinstance(item, dict)]


def _enrich_policy(item: dict[str, Any]) -> dict[str, Any]:
    policy = get_policy(item.get("field") or "")
    item.setdefault("aiMode", policy.mode)
    item.setdefault("policyReason", policy.reason)
    item.setdefault("needsHumanReview", True)
    return item


def _align_and_validate_proposals(
    parsed: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bind model output back to source tasks and reject unusable values.

    Drug/indication/market identifiers are copied from the task context rather
    than trusted from model prose, which keeps Excel matching deterministic.
    """

    task_by_id = {str(task.get("taskId")): task for task in tasks if task.get("taskId")}
    aligned: list[dict[str, Any]] = []
    used_task_ids: set[str] = set()
    for index, item in enumerate(parsed):
        task = task_by_id.get(str(item.get("taskId") or ""))
        if task is None and index < len(tasks):
            candidate = tasks[index]
            candidate_id = str(candidate.get("taskId") or "")
            if candidate_id not in used_task_ids:
                task = candidate
        if task is None:
            continue
        task_id = str(task.get("taskId") or "")
        if task_id in used_task_ids:
            continue
        context = task.get("context") or {}
        proposal = dict(item)
        proposal["taskId"] = task_id
        proposal["field"] = task.get("field")
        proposal["drugName"] = ((context.get("product") or {}).get("drugName"))
        proposal["indication"] = ((context.get("indication") or {}).get("text"))
        proposal["market"] = ((context.get("market") or {}).get("market"))
        if proposal.get("field") in {"base_patient_count_10k", "biomarker_ratio", "target_treatment_ratio"}:
            allowed_urls = {
                str(result.get("url") or "")
                for result in context.get("publicSearchResults") or []
                if result.get("url")
            }
            proposal["sourceUrls"] = [
                str(url) for url in proposal.get("sourceUrls") or [] if str(url) in allowed_urls
            ][:5]
        proposal = _enrich_policy(proposal)
        if not _valid_proposal_value(proposal):
            continue
        used_task_ids.add(task_id)
        aligned.append(proposal)
    return aligned


def _valid_proposal_value(proposal: dict[str, Any]) -> bool:
    field = proposal.get("field")
    value = proposal.get("suggestedValue")
    curve = proposal.get("suggestedCurve")
    if field == "base_patient_count_10k":
        return _bounded_number(value, 0, 200_000, lower_open=True)
    if field == "target_treatment_ratio":
        return _bounded_number(value, 0, 1, lower_open=True) and bool(proposal.get("sourceUrls"))
    if field == "biomarker_ratio":
        return _bounded_number(value, 0, 1, lower_open=True) and bool(proposal.get("sourceUrls"))
    if field == "patient_growth_rate":
        return _bounded_number(value, -0.05, 0.15)
    if field == "population_filters":
        return _bounded_number(value, 0, 1)
    if field == "penetration_curve":
        return isinstance(curve, list) and bool(curve) and all(
            item is None or _bounded_number(item, 0, 1) for item in curve
        ) and any(isinstance(item, (int, float)) for item in curve)
    if field == "monthly_price":
        return _positive_number(value) or (
            isinstance(curve, list) and any(_positive_number(item) for item in curve)
        )
    if field == "treatment_months":
        if _bounded_number(value, 0, 120, lower_open=True):
            return True
        return isinstance(curve, list) and bool(curve) and all(
            item is None or _bounded_number(item, 0, 120, lower_open=True) for item in curve
        )
    return value is not None or (isinstance(curve, list) and bool(curve))


def _bounded_number(
    value: Any,
    lower: float,
    upper: float,
    *,
    lower_open: bool = False,
) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return (value > lower if lower_open else value >= lower) and value <= upper


def _positive_number(value: Any) -> bool:
    return _bounded_number(value, 0, float("inf"), lower_open=True)


def _has_numeric_curve(value: Any) -> bool:
    return isinstance(value, list) and any(isinstance(item, (int, float)) for item in value)


def _has_positive_value(value: Any) -> bool:
    if _positive_number(value):
        return True
    return isinstance(value, list) and any(_positive_number(item) for item in value)


def _is_noncommercial_indication(indication: dict[str, Any]) -> bool:
    text = str(indication.get("indication_text") or "").strip().lower()
    return any(keyword in text for keyword in ("健康成年人", "健康受试者", "healthy volunteer"))


def _normalized_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _target_ratio_search_results(
    indication: dict[str, Any],
    market: dict[str, Any],
) -> list[dict[str, str]]:
    indication_text = str(indication.get("indication_text") or "").strip()
    disease_title = str(indication.get("disease_title") or indication.get("disease_key") or "").strip()
    geography = "中国" if market.get("market") == "china" else "美国 海外"
    queries = [
        f'"{indication_text}" {geography} 患者比例 流行病学 严重度',
        f'"{disease_title}" {geography} 轻度 中度 重度 比例 队列',
        f'"{disease_title}" {geography} moderate severe proportion epidemiology',
    ]
    results: list[dict[str, Any]] = []
    for query in queries:
        results.extend(search_public_web(query, 4))
    return dedupe_search_results(results)[:12]


def _biomarker_ratio_search_results(
    indication: dict[str, Any],
    market: dict[str, Any],
    flow: dict[str, Any],
) -> list[dict[str, str]]:
    indication_text = str(indication.get("indication_text") or "").strip()
    disease_title = str(indication.get("disease_title") or indication.get("disease_key") or "").strip()
    biomarker_text = _biomarker_query_text(indication_text, flow)
    geography = "中国" if market.get("market") == "china" else "美国"
    queries = [
        f'"{disease_title}" "{biomarker_text}" {geography} biomarker prevalence proportion',
        f'"{indication_text}" {geography} biomarker 阳性率 比例',
        f'"{disease_title}" "{biomarker_text}" {geography} 阳性率 流行病学',
    ]
    results: list[dict[str, Any]] = []
    for query in queries:
        results.extend(search_public_web(query, 4))
    return dedupe_search_results(results)[:12]


def _biomarker_query_text(indication_text: str, flow: dict[str, Any]) -> str:
    """Prefer the structured marker wording retained by the published pipeline."""

    source = str(flow.get("biomarker_ratio_source") or "")
    match = re.search(r"Biomarker限定[^；;]*", source)
    if match:
        return match.group(0)
    return indication_text


def _patient_count_search_results(
    indication: dict[str, Any],
    market: dict[str, Any],
) -> list[dict[str, str]]:
    indication_text = str(indication.get("indication_text") or "").strip()
    disease_title = str(indication.get("disease_title") or indication.get("disease_key") or "").strip()
    geography = "中国" if market.get("market") == "china" else "美国"
    queries = [
        f'"{indication_text}" {geography} 发病人数 患病人数 流行病学',
        f'"{disease_title}" {geography} incidence prevalence patients epidemiology',
    ]
    results: list[dict[str, Any]] = []
    for query in queries:
        results.extend(search_public_web(query, 5))
    return dedupe_search_results(results)[:10]
