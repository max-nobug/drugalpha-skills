"""Research missing tumor mPFS before applying local adjacent-line fallback.

The module only mutates a DCF draft stored under the DCF engine.  Public web
search is read-only, and clinical references are read through the published
snapshot adapter in SQLite read-only mode.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .deepseek_client import chat_completion, extract_text
from .public_web_search import dedupe_search_results, search_public_web
from ..source_adapters.clinical_pfs import resolve_adjacent_line_treatment_duration


def research_missing_treatment_durations(
    draft: dict[str, Any],
    *,
    batch_size: int = 3,
    max_tasks: int = 200,
) -> dict[str, Any]:
    tasks = _build_tasks(draft, max_tasks=max_tasks)
    proposals: dict[str, dict[str, Any]] = {}
    batch_errors: list[str] = []
    for start in range(0, len(tasks), max(1, batch_size)):
        batch = tasks[start : start + max(1, batch_size)]
        try:
            response = chat_completion(
                _build_messages(batch),
                temperature=0.0,
                max_tokens=min(6500, 2200 + len(batch) * 850),
            )
            parsed = _parse_json_array(extract_text(response))
            proposals.update(_validate_proposals(parsed, batch))
        except Exception as exc:
            batch_errors.append(str(exc)[:500])

    web_applied = 0
    adjacent_applied = 0
    rule_fallback = 0
    for task in tasks:
        resolution = proposals.get(str(task["taskId"]))
        if resolution:
            _apply_resolution(task, resolution)
            web_applied += 1
            continue
        adjacent = resolve_adjacent_line_treatment_duration(
            drug_name=task.get("drugName"),
            target=task.get("target"),
            indication_text=task.get("indicationText"),
            line_of_therapy=task.get("lineOfTherapy"),
        )
        if adjacent:
            _apply_resolution(task, adjacent)
            adjacent_applied += 1
        else:
            _mark_final_rule_fallback(task)
            rule_fallback += 1

    summary = {
        "taskCount": len(tasks),
        "webResearchAppliedCount": web_applied,
        "adjacentLineAppliedCount": adjacent_applied,
        "ruleFallbackCount": rule_fallback,
        "batchErrorCount": len(batch_errors),
        "batchErrors": batch_errors[:10],
        "precedence": [
            "DrugAlpha同药/同靶点+同适应症+同线次mPFS",
            "DrugAlpha AI联网检索同药/同靶点+同适应症+同线次mPFS",
            "DrugAlpha同药优先、同靶点其次的同适应症相邻线次mPFS",
            "DCF固定线次规则",
        ],
    }
    draft["treatment_duration_research"] = summary
    return summary


def _build_tasks(draft: dict[str, Any], *, max_tasks: int) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for product in draft.get("products") or []:
        if product.get("modeling_mode") == "simplified_growth":
            continue
        for indication in product.get("indications") or []:
            markets = indication.get("markets") or []
            fallback_markets = [
                market
                for market in markets
                if str((market.get("patient_flow") or {}).get("treatment_months_mode") or "")
                == "line_rule_fallback"
            ]
            if not fallback_markets:
                continue
            task_id = f"treatment-duration-{len(tasks) + 1}"
            indication_text = str(indication.get("indication_text") or "").strip()
            tasks.append(
                {
                    "taskId": task_id,
                    "drugName": product.get("drug_name"),
                    "target": product.get("target"),
                    "indicationText": indication_text,
                    "lineOfTherapy": indication.get("line_of_therapy"),
                    "trialRegNo": indication.get("trial_reg_no"),
                    "markets": fallback_markets,
                }
            )
            if len(tasks) >= max_tasks:
                return _attach_search_results(tasks)
    return _attach_search_results(tasks)


def _attach_search_results(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run independent indication searches concurrently without changing evidence rules."""

    if not tasks:
        return tasks
    with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as executor:
        futures = [
            executor.submit(
                _search_results,
                drug_name=task.get("drugName"),
                target=task.get("target"),
                indication_text=str(task.get("indicationText") or ""),
                line_of_therapy=task.get("lineOfTherapy"),
            )
            for task in tasks
        ]
        for task, future in zip(tasks, futures):
            task["publicSearchResults"] = future.result()
    return tasks


def _search_results(
    *,
    drug_name: Any,
    target: Any,
    indication_text: str,
    line_of_therapy: Any,
) -> list[dict[str, str]]:
    line = str(line_of_therapy or "").strip()
    queries = [
        f'"{drug_name or ""}" "{indication_text}" {line} mPFS median progression-free survival',
        f'"{target or ""}" "{indication_text}" {line} mPFS clinical trial',
        f'"{indication_text}" {line} 中位无进展生存期 mPFS 临床试验',
    ]
    results: list[dict[str, Any]] = []
    for query in queries:
        results.extend(search_public_web(query, 5))
    return dedupe_search_results(results)[:15]


def _build_messages(tasks: list[dict[str, Any]]) -> list[dict[str, str]]:
    system = (
        "你是创新药DCF临床证据核验助手。只可使用任务给出的publicSearchResults，不能声称访问其他数据库。"
        "本步骤只接受与目标适应症及目标线次相符的同药或同靶点公开临床mPFS；相邻线次数据必须返回null，"
        "留给系统下一步处理。必须返回可核验URL和检索摘要中的原始证据，不得估算或用OS/PFS率替代mPFS。"
        "不要输出推理过程，只输出JSON数组。"
    )
    payload = []
    for task in tasks:
        payload.append(
            {
                "taskId": task["taskId"],
                "drugName": task.get("drugName"),
                "target": task.get("target"),
                "indication": task.get("indicationText"),
                "lineOfTherapy": task.get("lineOfTherapy"),
                "trialRegNo": task.get("trialRegNo"),
                "publicSearchResults": task.get("publicSearchResults"),
            }
        )
    user = {
        "rules": [
            "优先同药+同适应症+同线次，其次同靶点+同适应症+同线次。",
            "若疾病、分期、治疗线次或治疗场景不一致，mPfsMonths必须为null。",
            "mPFS未达到、只提供HR/PFS率/OS、或摘要无明确月数时，mPfsMonths必须为null。",
            "若多条完全匹配记录有效，可取相关度最高记录的中位数，并在basisComment说明。",
        ],
        "schema": {
            "taskId": "string",
            "mPfsMonths": "number|null，不设置12个月上限",
            "matchType": "same_drug_same_indication_web|same_target_same_indication_web|null",
            "referenceDrug": "string|null",
            "sourceUrl": "必须来自publicSearchResults.url；无有效值时为null",
            "sourceTitle": "string|null",
            "evidenceQuote": "必须逐字来自所选检索结果title或snippet，且包含mPFS月数",
            "studyPopulation": "string|null",
            "lineOfTherapy": "string|null",
            "sampleSize": "number|null",
            "publicationDate": "YYYY-MM-DD或YYYY|null",
            "basisComment": "100字以内中文核验说明",
            "confidence": "low|medium|high",
            "needsHumanReview": True,
        },
        "tasks": payload,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def _validate_proposals(
    parsed: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    task_by_id = {str(task["taskId"]): task for task in tasks}
    output: dict[str, dict[str, Any]] = {}
    for item in parsed:
        task_id = str(item.get("taskId") or "")
        task = task_by_id.get(task_id)
        months = item.get("mPfsMonths")
        if not task or not isinstance(months, (int, float)) or isinstance(months, bool):
            continue
        if not 0.5 <= float(months) <= 120:
            continue
        match_type = str(item.get("matchType") or "")
        if match_type not in {"same_drug_same_indication_web", "same_target_same_indication_web"}:
            continue
        source_url = str(item.get("sourceUrl") or "").strip()
        allowed = {
            str(result.get("url") or ""): result
            for result in task.get("publicSearchResults") or []
            if result.get("url")
        }
        source_result = allowed.get(source_url)
        if not source_result or not _evidence_is_auditable(item, source_result, float(months)):
            continue
        mode_label = "同药+同适应症+同线次" if match_type.startswith("same_drug") else "同靶点+同适应症+同线次"
        output[task_id] = {
            "mode": match_type,
            "treatmentMonths": round(float(months), 1),
            "referencePfsMonths": round(float(months), 1),
            "source": f"DrugAlpha AI联网检索公开临床mPFS（{mode_label}）",
            "sourceDetail": (
                f"{mode_label}公开证据：mPFS {float(months):.1f}个月；各预测年度直接采用，不设置12个月上限；"
                f"置信度{item.get('confidence') or 'low'}，需人工复核"
            ),
            "requiresHumanReview": True,
            "references": [
                {
                    "referenceType": "public_web",
                    "drugName": item.get("referenceDrug") or task.get("drugName"),
                    "target": task.get("target"),
                    "indication": task.get("indicationText"),
                    "lineOfTherapy": item.get("lineOfTherapy") or task.get("lineOfTherapy"),
                    "pfsMonths": round(float(months), 1),
                    "sourceTitle": item.get("sourceTitle") or source_result.get("title"),
                    "sourceUrl": source_url,
                    "evidenceQuote": item.get("evidenceQuote"),
                    "studyPopulation": item.get("studyPopulation"),
                    "sampleSize": item.get("sampleSize"),
                    "publicationDate": item.get("publicationDate"),
                    "basisComment": item.get("basisComment"),
                    "confidence": item.get("confidence") or "low",
                }
            ],
        }
    return output


def _evidence_is_auditable(item: dict[str, Any], result: dict[str, Any], months: float) -> bool:
    quote = re.sub(r"\s+", " ", str(item.get("evidenceQuote") or "")).strip()
    evidence = re.sub(
        r"\s+",
        " ",
        f"{result.get('title') or ''} {result.get('snippet') or ''}",
    ).strip()
    if not quote or _normalize_evidence(quote) not in _normalize_evidence(evidence):
        return False
    numbers = [float(value) for value in re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", quote)]
    return any(abs(number - months) <= 0.05 for number in numbers)


def _normalize_evidence(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff.]+", "", value.lower())


def _apply_resolution(task: dict[str, Any], resolution: dict[str, Any]) -> None:
    new_months = float(resolution["treatmentMonths"])
    for market in task.get("markets") or []:
        flow = market.get("patient_flow") or {}
        old_months = _first_positive(flow.get("treatment_months"))
        if old_months and new_months > 0 and market.get("peak_sales_anchor") is not None:
            flow["penetration_curve"] = _rescaled_curve(
                flow.get("penetration_curve"),
                old_months=old_months,
                new_months=new_months,
            )
        years = len(flow.get("treatment_months") or []) or 10
        flow["treatment_months"] = [round(new_months, 1) for _ in range(years)]
        flow["treatment_months_mode"] = resolution.get("mode")
        flow["treatment_months_reference_pfs"] = resolution.get("referencePfsMonths")
        flow["treatment_months_source"] = resolution.get("source")
        flow["treatment_months_source_detail"] = resolution.get("sourceDetail")
        flow["treatment_months_requires_review"] = bool(resolution.get("requiresHumanReview", True))
        flow["treatment_months_references"] = list(resolution.get("references") or [])


def _mark_final_rule_fallback(task: dict[str, Any]) -> None:
    for market in task.get("markets") or []:
        flow = market.get("patient_flow") or {}
        detail = str(flow.get("treatment_months_source_detail") or "").rstrip("；")
        flow["treatment_months_source_detail"] = (
            f"{detail}；DrugAlpha AI联网未取得可核验的精确适应症mPFS，DrugAlpha亦无可用相邻线次数据，最终使用固定规则"
        )
        flow["treatment_months_requires_review"] = True


def _rescaled_curve(value: Any, *, old_months: float, new_months: float) -> list[float | None]:
    if not isinstance(value, list):
        return []
    result: list[float | None] = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            result.append(None)
        else:
            result.append(round(min(max(float(item) * old_months / new_months, 0.0), 0.65), 4))
    return result


def _first_positive(value: Any) -> float | None:
    if not isinstance(value, list):
        return None
    return next(
        (float(item) for item in value if isinstance(item, (int, float)) and not isinstance(item, bool) and item > 0),
        None,
    )


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    cleaned = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("["):
        match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
        if match:
            cleaned = match.group(0)
    try:
        payload = json.loads(cleaned)
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        payload = [payload]
    return [item for item in payload if isinstance(item, dict)]
