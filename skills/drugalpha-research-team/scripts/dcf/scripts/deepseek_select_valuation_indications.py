"""Select one early indication for valuation-tracking assets with no mature indication.

The workflow is intentionally downstream and auditable:

1. Read a DCF-owned source snapshot.
2. Search public web results for each affected product (read-only Bing RSS).
3. Give the early pipeline candidates and search evidence to DeepSeek.
4. Persist the selected source-row identity, assumptions, and source links under
   the DCF engine's own data directory.

No upstream DrugAlpha source is modified.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.ai_assumptions.deepseek_client import chat_completion, extract_text
from packages.dcf_core.launch_timing import resolve_china_launch_year
from packages.dcf_core.phase_rules import is_marketed_phase, is_nda_bla_phase, is_withdrawn_phase
from packages.dcf_core.success_probability import phase_success_probability
from packages.source_adapters.clinical_pfs import (
    TUMOR_FAMILY_ALIASES,
    find_same_target_indication_evidence,
)
from packages.source_adapters.io_utils import write_json


NONCOMMERCIAL_TOKENS = ("健康受试者", "健康志愿者", "健康成人", "PK/安全性")
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
DEFAULT_SEARCH_RESULT_LIMIT = 4


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, help="DCF-owned source snapshot JSON")
    parser.add_argument("--out", required=True, help="Output JSON under the DCF engine")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--search-result-limit", type=int, default=DEFAULT_SEARCH_RESULT_LIMIT)
    parser.add_argument("--match-drug", help="Optional exact normalized drug-name filter for audit/retry")
    parser.add_argument("--max-tasks", type=int, help="Optional maximum task count for audit/retry")
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    tasks = _build_tasks(
        snapshot,
        max(1, args.search_result_limit),
        match_drug=args.match_drug,
        max_tasks=args.max_tasks,
    )
    selections, raw_batches = _generate_selections(snapshot, tasks, max(1, args.batch_size))
    selected_ids = {item.get("productKey") for item in selections}
    payload = {
        "companyName": (snapshot.get("company") or {}).get("companyName"),
        "sourceSnapshotId": snapshot.get("snapshotId"),
        "taskCount": len(tasks),
        "selectionCount": len(selections),
        "missingSelectionCount": sum(1 for item in tasks if item.get("productKey") not in selected_ids),
        "searchProvider": "brave_html_with_duckduckgo_and_bing_fallback",
        "tasks": tasks,
        "selections": selections,
        "rawBatches": raw_batches,
        "disclaimer": "联网检索结果与 DrugAlpha AI 选择仅用于生成可编辑估值初稿；需研究员复核适应症、上市时间、成功率及峰值销售口径。",
    }
    write_json(Path(args.out), payload)
    print(json.dumps(_summary(payload, Path(args.out)), ensure_ascii=False, indent=2))
    return 0


def _build_tasks(
    snapshot: dict[str, Any],
    result_limit: int,
    *,
    match_drug: str | None = None,
    max_tasks: int | None = None,
    public_search: bool = True,
) -> list[dict[str, Any]]:
    company = snapshot.get("company") or {}
    company_name = company.get("companyName") or company.get("companyKey") or ""
    tasks: list[dict[str, Any]] = []
    for product in snapshot.get("pipeline", {}).get("products") or []:
        if product.get("modelingMode") == "simplified_growth":
            continue
        if match_drug and _normalize(product.get("drugName")) != _normalize(match_drug):
            continue
        if not product.get("isValuationTrackingProduct") or product.get("valuationOnly"):
            continue
        indications = product.get("indications") or []
        if any(_is_mature(item.get("phase")) for item in indications):
            continue
        candidates = _candidate_indications(indications)
        if not candidates:
            continue
        same_target_clinical_evidence = find_same_target_indication_evidence(
            target=product.get("target"),
            limit=24,
        )
        queries = _search_queries(company_name, product, candidates)
        search_results: list[dict[str, str]] = []
        if public_search:
            for query in queries:
                search_results.extend(_search_public_web(query, result_limit))
        search_results = _dedupe_search_results(search_results)[:12]
        tasks.append(
            {
                "taskId": f"valuation-indication-{len(tasks) + 1}",
                "productKey": product.get("productKey") or product.get("drugName"),
                "drugName": product.get("drugName"),
                "target": product.get("target"),
                "domesticPeakSalesCny100m": ((product.get("valuationTracking") or {}).get("domestic") or {}).get("estimatedPeakSales"),
                "overseasPeakSalesUsd100m": ((product.get("valuationTracking") or {}).get("overseas") or {}).get("estimatedPeakSalesUsd"),
                "candidates": candidates,
                "sameTargetClinicalEvidence": same_target_clinical_evidence,
                "searchQueries": queries,
                "searchResults": search_results,
            }
        )
        if max_tasks is not None and len(tasks) >= max(0, max_tasks):
            break
    return tasks


def _candidate_indications(indications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in indications:
        text = str(item.get("indicationText") or "").strip()
        if (
            not text
            or _is_mature(item.get("phase"))
            or is_withdrawn_phase(item.get("phase"))
            or _is_noncommercial(text)
        ):
            continue
        identity = _indication_identity(item)
        key = (_normalize(text), _normalize(item.get("phase")))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "sourceRowIdentity": identity,
                "indication": text,
                "phase": item.get("phase"),
                "statusStartDate": item.get("statusStartDate"),
                "trialRegNo": item.get("trialRegNo"),
                "sponsor": item.get("sponsor"),
                "latestProgress": item.get("latestProgress"),
            }
        )
    return candidates


def _search_queries(company_name: str, product: dict[str, Any], candidates: list[dict[str, Any]]) -> list[str]:
    drug = str(product.get("drugName") or "").strip()
    target = str(product.get("target") or "").strip()
    quoted_drug = f'"{drug}"' if drug else ""
    quoted_company = f'"{company_name}"' if company_name else ""
    queries = [" ".join(part for part in (quoted_company, quoted_drug, target, "临床 适应症 最新进展") if part)]
    if target:
        queries.extend(
            [
                " ".join((f'"{target}"', "同靶点 药物 临床 适应症 最新进展")),
                " ".join((f'"{target}"', "clinical pipeline indications latest development")),
            ]
        )
    ranked = sorted(candidates, key=lambda item: _phase_rank(item.get("phase")), reverse=True)
    for item in ranked[:3]:
        indication = str(item.get("indication") or "").strip()
        if indication:
            queries.append(" ".join(part for part in (quoted_drug, indication, item.get("trialRegNo"), "临床") if part))
        if _is_broad_indication(indication):
            trial_reg_no = str(item.get("trialRegNo") or "").strip()
            queries.extend(
                [
                    " ".join(part for part in (quoted_drug, trial_reg_no, "扩展队列 具体瘤种 注册 临床数据") if part),
                    " ".join(part for part in (quoted_drug, trial_reg_no, "phase II cohorts tumor types registration") if part),
                    " ".join(part for part in (quoted_company, quoted_drug, "开发重点 具体瘤种 ORR") if part),
                ]
            )
    return list(dict.fromkeys(query.strip() for query in queries if query.strip()))


def _search_public_web(query: str, limit: int) -> list[dict[str, str]]:
    brave_url = "https://search.brave.com/search?q=" + urllib.parse.quote(query)
    try:
        body = _fetch_search_html(brave_url)
        results = _parse_brave_results(query, body, limit)
        if results:
            return results
    except Exception:
        pass
    duckduckgo_url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    try:
        body = _fetch_search_html(duckduckgo_url)
        results = _parse_duckduckgo_results(query, body, limit)
        if results:
            return results
    except Exception:
        pass
    return _search_bing_rss(query, limit)


def _fetch_search_html(url: str) -> str:
    # curl.exe follows the same Windows proxy/network path as the desktop app;
    # urllib remains a portable fallback for other runtimes.
    if sys.platform.startswith("win"):
        completed = subprocess.run(
            ["curl.exe", "-L", "--fail", "--silent", "--max-time", "20", "-A", "Mozilla/5.0", url],
            capture_output=True,
            check=True,
            timeout=25,
        )
        return completed.stdout.decode("utf-8", errors="replace")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 DrugAlpha-DCF/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def _parse_brave_results(query: str, body: str, limit: int) -> list[dict[str, str]]:
    js_string = r'((?:\\.|[^"\\])*)'
    pattern = re.compile(
        r'\{title:"' + js_string + r'",url:"' + js_string
        + r'",full_title:.*?,description:"' + js_string + r'"',
        flags=re.DOTALL,
    )
    results: list[dict[str, str]] = []
    for match in pattern.finditer(body):
        try:
            title = _decode_js_string(match.group(1))
            url = _decode_js_string(match.group(2))
            snippet = _strip_html(_decode_js_string(match.group(3)))[:500]
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not url.startswith(("http://", "https://")):
            continue
        results.append({"query": query, "title": title, "url": url, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def _parse_duckduckgo_results(query: str, body: str, limit: int) -> list[dict[str, str]]:
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        r'.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        flags=re.DOTALL | re.IGNORECASE,
    )
    results: list[dict[str, str]] = []
    for raw_url, raw_title, raw_snippet in pattern.findall(body)[:limit]:
        decoded_url = html.unescape(raw_url)
        if decoded_url.startswith("//"):
            decoded_url = "https:" + decoded_url
        parsed = urllib.parse.urlparse(decoded_url)
        if parsed.netloc.endswith("duckduckgo.com"):
            decoded_url = urllib.parse.parse_qs(parsed.query).get("uddg", [decoded_url])[0]
        results.append(
            {
                "query": query,
                "title": _strip_html(raw_title),
                "url": decoded_url,
                "snippet": _strip_html(raw_snippet)[:500],
            }
        )
    return results


def _search_bing_rss(query: str, limit: int) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote(query)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 DrugAlpha-DCF/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read()
        root = ET.fromstring(body)
    except Exception as exc:
        return [{"query": query, "title": "检索失败", "url": url, "snippet": str(exc)[:200]}]
    results: list[dict[str, str]] = []
    for item in root.findall("./channel/item")[:limit]:
        results.append(
            {
                "query": query,
                "title": _clean_text(item.findtext("title")),
                "url": _clean_text(item.findtext("link")),
                "snippet": _clean_text(item.findtext("description"))[:500],
            }
        )
    return results


def _generate_selections(
    snapshot: dict[str, Any],
    tasks: list[dict[str, Any]],
    batch_size: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Difficult valuation-only assets are intentionally processed one product at
    # a time.  ``batch_size`` remains in the CLI contract for compatibility.
    _ = batch_size
    selections: list[dict[str, Any]] = []
    raw_batches: list[dict[str, Any]] = []
    for task_index, task in enumerate(tasks):
        screened_candidates: list[dict[str, Any]] = []
        try:
            screening_response = chat_completion(
                _screening_messages(snapshot, task),
                temperature=0.1,
                max_tokens=4200,
            )
            screening_text = extract_text(screening_response)
            screening_parsed = _parse_json_array(screening_text)
            if not screening_parsed:
                screening_parsed = _recover_truncated_selections(screening_text, [task])
            screened_candidates = _normalize_screened_candidates(screening_parsed, task)
            raw_batches.append(
                {
                    "stage": "evidence_screening",
                    "taskStart": task_index,
                    "taskCount": 1,
                    "taskId": task.get("taskId"),
                    "model": screening_response.get("model"),
                    "usage": screening_response.get("usage"),
                    "rawText": screening_text,
                    "parsedCount": len(screened_candidates),
                }
            )
        except Exception as screening_exc:
            raw_batches.append(
                {
                    "stage": "evidence_screening",
                    "taskStart": task_index,
                    "taskCount": 1,
                    "taskId": task.get("taskId"),
                    "parsedCount": 0,
                    "error": str(screening_exc)[:500],
                }
            )

        aligned: list[dict[str, Any]] = []
        try:
            selection_response = chat_completion(
                _selection_messages(snapshot, task, screened_candidates),
                temperature=0.0,
                max_tokens=3200,
            )
            selection_text = extract_text(selection_response)
            selection_parsed = _parse_json_array(selection_text)
            if not selection_parsed:
                selection_parsed = _recover_truncated_selections(selection_text, [task])
            aligned = _validate_selections(selection_parsed, [task])
            raw_batches.append(
                {
                    "stage": "final_selection",
                    "taskStart": task_index,
                    "taskCount": 1,
                    "taskId": task.get("taskId"),
                    "model": selection_response.get("model"),
                    "usage": selection_response.get("usage"),
                    "rawText": selection_text,
                    "parsedCount": len(aligned),
                    "screenedCandidateCount": len(screened_candidates),
                }
            )
        except Exception as selection_exc:
            raw_batches.append(
                {
                    "stage": "final_selection",
                    "taskStart": task_index,
                    "taskCount": 1,
                    "taskId": task.get("taskId"),
                    "parsedCount": 0,
                    "error": str(selection_exc)[:500],
                }
            )

        if not aligned:
            try:
                retry_response = chat_completion(
                    _selection_messages(snapshot, task, screened_candidates, retry=True),
                    temperature=0.0,
                    max_tokens=2200,
                )
                retry_text = extract_text(retry_response)
                retry_parsed = _parse_json_array(retry_text)
                if not retry_parsed:
                    retry_parsed = _recover_truncated_selections(retry_text, [task])
                aligned = _validate_selections(retry_parsed, [task])
                raw_batches.append(
                    {
                        "stage": "final_selection_retry",
                        "taskStart": task_index,
                        "taskCount": 1,
                        "taskId": task.get("taskId"),
                        "model": retry_response.get("model"),
                        "usage": retry_response.get("usage"),
                        "rawText": retry_text,
                        "parsedCount": len(aligned),
                        "screenedCandidateCount": len(screened_candidates),
                    }
                )
            except Exception as retry_exc:
                raw_batches.append(
                    {
                        "stage": "final_selection_retry",
                        "taskStart": task_index,
                        "taskCount": 1,
                        "taskId": task.get("taskId"),
                        "parsedCount": 0,
                        "error": str(retry_exc)[:500],
                    }
                )
        selections.extend(aligned)
    return selections, raw_batches


def _screening_messages(snapshot: dict[str, Any], task: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "你是创新药适应症证据筛选助手。本步骤只负责把证据收敛为最多3个具体适应症候选，不计算上市年、峰值年或POS。"
        "严格按三级证据排序："
        "第一优先本药 candidates 中已经开展的具体适应症或篮子试验内明确队列；第二参考 sameTargetClinicalEvidence 中"
        "DrugAlpha 已发布临床数据库的同靶点药物适应症；第三才参考 searchResults 中同靶点药物当前公开开发或获批情况。"
        "第二、三级证据只用于在本药已开展的疾病范围内排序和细化，不得据此扩展到本药从未开展、且篮子试验也未覆盖的疾病。"
        "selectedSourceRowIdentity 必须逐字复制自 candidates；selectedIndication 必须是具体疾病，禁止仅返回实体瘤、恶性肿瘤或多瘤种。"
        "只输出JSON数组，不要解释。"
    )
    user = {
        "company": (snapshot.get("company") or {}).get("companyName"),
        "schema": {
            "taskId": "必须原样返回",
            "productKey": "必须原样返回",
            "selectedSourceRowIdentity": "必须来自 candidates",
            "selectedIndication": "具体疾病及必要治疗场景",
            "evidencePriority": "own_pipeline|same_target_clinical|same_target_public",
            "evidenceSummary": "中文，不超过60字",
            "sourceUrls": "仅从 searchResults.url 中选择，最多3条",
        },
        "task": _compact_task_for_screening(task),
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def _selection_messages(
    snapshot: dict[str, Any],
    task: dict[str, Any],
    screened_candidates: list[dict[str, Any]],
    *,
    retry: bool = False,
) -> list[dict[str, str]]:
    system = (
        "你是创新药DCF最终适应症选择器。只从 screenedCandidates 中选择一个；若筛选结果为空，才可根据 ownCandidates "
        "选择其已明确包含的具体疾病。不得新增疾病，不得输出实体瘤/恶性肿瘤/多瘤种等宽泛口径。"
        "selectedSourceRowIdentity必须逐字复制；只返回一个JSON对象，不要分析过程、不要上市年、峰值年、POS或长篇说明。"
    )
    if retry:
        system += "这是格式修复重试：立即输出单个完整JSON对象，控制在300字以内。"
    user = {
        "company": (snapshot.get("company") or {}).get("companyName"),
        "taskId": task.get("taskId"),
        "productKey": task.get("productKey"),
        "drugName": task.get("drugName"),
        "target": task.get("target"),
        "ownCandidates": [
            {
                "sourceRowIdentity": item.get("sourceRowIdentity"),
                "indication": item.get("indication"),
                "phase": item.get("phase"),
            }
            for item in task.get("candidates") or []
        ],
        "screenedCandidates": screened_candidates,
        "schema": {
            "taskId": "原样返回",
            "productKey": "原样返回",
            "selectedSourceRowIdentity": "来自ownCandidates",
            "selectedIndication": "具体疾病及必要治疗场景",
            "evidencePriority": "own_pipeline|same_target_clinical|same_target_public",
            "sourceUrls": "复制所选screenedCandidate中的URL，最多3条",
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def _compact_task_for_screening(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "taskId": task.get("taskId"),
        "productKey": task.get("productKey"),
        "drugName": task.get("drugName"),
        "target": task.get("target"),
        "candidates": [
            {
                "sourceRowIdentity": item.get("sourceRowIdentity"),
                "indication": item.get("indication"),
                "phase": item.get("phase"),
                "trialRegNo": item.get("trialRegNo"),
            }
            for item in task.get("candidates") or []
        ],
        "sameTargetClinicalEvidence": _compact_clinical_evidence(
            task.get("sameTargetClinicalEvidence") or []
        ),
        "searchResults": _compact_search_results(task),
    }


def _compact_clinical_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        indication = str(item.get("indication") or item.get("sheetName") or "").strip()
        if not indication:
            continue
        key = _normalize(indication)
        current = grouped.setdefault(
            key,
            {
                "indication": indication,
                "lineOfTherapy": item.get("lineOfTherapy"),
                "drugNames": [],
                "targets": [],
                "recordCount": 0,
                "maxTargetMatchScore": 0.0,
            },
        )
        current["recordCount"] += 1
        current["maxTargetMatchScore"] = max(
            float(current.get("maxTargetMatchScore") or 0),
            float(item.get("targetMatchScore") or 0),
        )
        drug_name = str(item.get("drugName") or "").strip()
        target = str(item.get("target") or "").strip()
        if drug_name and drug_name not in current["drugNames"] and len(current["drugNames"]) < 3:
            current["drugNames"].append(drug_name)
        if target and target not in current["targets"] and len(current["targets"]) < 3:
            current["targets"].append(target)
    return sorted(
        grouped.values(),
        key=lambda item: (item["maxTargetMatchScore"], item["recordCount"]),
        reverse=True,
    )[:12]


def _compact_search_results(task: dict[str, Any]) -> list[dict[str, str]]:
    drug = _normalize(task.get("drugName"))
    target = _normalize(task.get("target"))
    trial_ids = {
        _normalize(item.get("trialRegNo"))
        for item in task.get("candidates") or []
        if item.get("trialRegNo")
    }
    candidate_families: set[str] = set()
    for item in task.get("candidates") or []:
        candidate_families |= _tumor_families(item.get("indication"))
    ranked: list[tuple[int, int, dict[str, str]]] = []
    for index, item in enumerate(task.get("searchResults") or []):
        combined = _normalize(f"{item.get('title') or ''} {item.get('snippet') or ''}")
        score = 0
        if drug and drug in combined:
            score += 5
        if any(trial_id and trial_id in combined for trial_id in trial_ids):
            score += 5
        if target and target in combined:
            score += 2
        if candidate_families & _tumor_families(combined):
            score += 3
        if score <= 0:
            continue
        ranked.append(
            (
                score,
                -index,
                {
                    "title": _clean_text(item.get("title"))[:160],
                    "url": str(item.get("url") or ""),
                    "snippet": _clean_text(item.get("snippet"))[:260],
                },
            )
        )
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in ranked[:8]]


def _normalize_screened_candidates(
    parsed: list[dict[str, Any]],
    task: dict[str, Any],
) -> list[dict[str, Any]]:
    candidate_by_identity = {
        str(item.get("sourceRowIdentity") or ""): item
        for item in task.get("candidates") or []
    }
    allowed_urls = {
        str(item.get("url") or "")
        for item in task.get("searchResults") or []
        if item.get("url")
    }
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in parsed:
        if str(item.get("taskId") or "") != str(task.get("taskId") or ""):
            continue
        identity = str(item.get("selectedSourceRowIdentity") or "")
        candidate = candidate_by_identity.get(identity)
        selected_indication = str(item.get("selectedIndication") or "").strip()
        if not candidate or not selected_indication or _is_broad_indication(selected_indication):
            continue
        parent_indication = str(candidate.get("indication") or "").strip()
        source_urls = [
            str(url)
            for url in item.get("sourceUrls") or []
            if str(url) in allowed_urls
        ][:3]
        own_support = _indication_supported_by_text(selected_indication, parent_indication)
        if not own_support and not _selection_supported_by_sources(
            selected_indication,
            task.get("searchResults") or [],
            source_urls,
            task.get("sameTargetClinicalEvidence") or [],
        ):
            continue
        key = (_normalize(identity), _normalize(selected_indication))
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "selectedSourceRowIdentity": identity,
                "parentIndication": parent_indication,
                "selectedIndication": selected_indication,
                "evidencePriority": _evidence_priority(
                    selected_indication,
                    parent_indication,
                    task.get("sameTargetClinicalEvidence") or [],
                    task.get("searchResults") or [],
                    source_urls,
                ),
                "evidenceSummary": str(item.get("evidenceSummary") or "").strip()[:120],
                "sourceUrls": source_urls,
            }
        )
        if len(result) >= 3:
            break
    return result


def _validate_selections(parsed: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    task_by_id = {str(task.get("taskId")): task for task in tasks}
    validated: list[dict[str, Any]] = []
    used: set[str] = set()
    for item in parsed:
        task_id = str(item.get("taskId") or "")
        task = task_by_id.get(task_id)
        if not task or task_id in used:
            continue
        candidate_by_identity = {
            str(candidate.get("sourceRowIdentity") or ""): candidate
            for candidate in task.get("candidates") or []
        }
        identity = str(item.get("selectedSourceRowIdentity") or "")
        candidate = candidate_by_identity.get(identity)
        if not candidate:
            continue
        parent_indication = str(candidate.get("indication") or "").strip()
        selected_indication = str(item.get("selectedIndication") or "").strip()
        if not selected_indication or _is_broad_indication(selected_indication):
            continue
        ai_launch_year = _bounded_int(item.get("suggestedLaunchYear"), 2026, 2035)
        launch_year, launch_year_basis = resolve_china_launch_year(
            phase=candidate.get("phase"),
            status_start_date=candidate.get("statusStartDate"),
            indication_text=selected_indication,
            latest_progress=candidate.get("latestProgress"),
            ai_suggested_year=ai_launch_year,
        )
        if launch_year is None:
            launch_year = _fallback_launch_year(candidate.get("phase"))
            launch_year_basis = "缺少可用阶段开始时间与AI建议，采用早期阶段兜底"
        peak_year = _bounded_int(item.get("suggestedPeakYear"), launch_year or 2026, 2040)
        pos = phase_success_probability(candidate.get("phase"))
        if peak_year is None:
            peak_year = min(launch_year + 7, 2040)
        if pos is None:
            pos = _fallback_pos(candidate.get("phase"))
        allowed_urls = {result.get("url") for result in task.get("searchResults") or [] if result.get("url")}
        source_urls = [url for url in item.get("sourceUrls") or [] if url in allowed_urls][:5]
        same_target_clinical_evidence = task.get("sameTargetClinicalEvidence") or []
        own_pipeline_support = _indication_supported_by_text(selected_indication, parent_indication)
        if (
            _is_broad_indication(parent_indication)
            and not own_pipeline_support
            and not source_urls
            and not same_target_clinical_evidence
        ):
            continue
        if (
            _is_broad_indication(parent_indication)
            and not own_pipeline_support
            and not _selection_supported_by_sources(
                selected_indication,
                task.get("searchResults") or [],
                source_urls,
                same_target_clinical_evidence,
            )
        ):
            continue
        basis_comment = str(item.get("basisComment") or "").strip()
        confidence = item.get("confidence") if item.get("confidence") in {"low", "medium", "high"} else "low"
        if _is_broad_indication(parent_indication):
            confidence = "low"
            basis_comment = (
                basis_comment
                + " 系统复核：原始管线仅为宽泛早期篮子试验，具体瘤种仍需人工确认，置信度统一降为 low。"
            ).strip()
        validated.append(
            {
                "taskId": task_id,
                "productKey": task.get("productKey"),
                "drugName": task.get("drugName"),
                "target": task.get("target"),
                "selectedSourceRowIdentity": identity,
                "parentIndication": parent_indication,
                "selectedIndication": selected_indication,
                "selectedPhase": candidate.get("phase"),
                "suggestedLaunchYear": launch_year,
                "launchYearBasis": launch_year_basis,
                "suggestedPeakYear": peak_year,
                "suggestedPos": pos,
                "basisComment": basis_comment[:500],
                "confidence": confidence,
                "needsHumanReview": True,
                "selectionMode": "deepseek_web_research",
                "evidencePriority": _evidence_priority(
                    selected_indication,
                    parent_indication,
                    same_target_clinical_evidence,
                    task.get("searchResults") or [],
                    source_urls,
                ),
                "searchProvider": "brave_html_with_duckduckgo_and_bing_fallback",
                "searchQueries": task.get("searchQueries") or [],
                "sourceUrls": source_urls,
                "searchResults": task.get("searchResults") or [],
                "sameTargetClinicalEvidence": same_target_clinical_evidence,
            }
        )
        used.add(task_id)
    return validated


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    cleaned = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
        if not match:
            return []
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(payload, dict):
        payload = [payload]
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _recover_truncated_selections(
    text: str,
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recover an auditable core selection when model JSON is cut off at the tail.

    Recovery is deliberately narrow: task identity, source-row identity and a
    concrete indication must already be present in the model output.  The
    normal validator still enforces the candidate boundary and evidence rules;
    missing narrative/source fields are downgraded to low-confidence review.
    """

    raw = str(text or "").strip()
    if not raw:
        return []
    task_by_id = {str(task.get("taskId") or ""): task for task in tasks}
    recovered: list[dict[str, Any]] = []
    for task_id, task in task_by_id.items():
        task_marker = f'"taskId": "{task_id}"'
        task_start = raw.find(task_marker)
        if not task_id or task_start < 0:
            continue
        next_task = raw.find('"taskId"', task_start + len(task_marker))
        task_text = raw[task_start : next_task if next_task >= 0 else len(raw)]
        identity = _partial_json_string(task_text, "selectedSourceRowIdentity")
        selected_indication = _partial_json_string(task_text, "selectedIndication")
        candidate_identities = {
            str(item.get("sourceRowIdentity") or "")
            for item in task.get("candidates") or []
        }
        if (
            not identity
            or identity not in candidate_identities
            or not selected_indication
            or _is_broad_indication(selected_indication)
        ):
            continue
        recovered.append(
            {
                "taskId": task_id,
                "productKey": task.get("productKey"),
                "selectedSourceRowIdentity": identity,
                "parentIndication": _partial_json_string(task_text, "parentIndication"),
                "selectedIndication": selected_indication,
                "suggestedLaunchYear": _partial_json_number(task_text, "suggestedLaunchYear"),
                "suggestedPeakYear": _partial_json_number(task_text, "suggestedPeakYear"),
                "basisComment": "DrugAlpha AI已完成核心适应症选择，但返回内容尾部截断；系统仅恢复可校验字段，需人工复核。",
                "confidence": "low",
                "needsHumanReview": True,
                "sourceUrls": [],
                "recoveredFromTruncatedOutput": True,
            }
        )
    return recovered


def _partial_json_string(text: str, field: str) -> str:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    if not match:
        return ""
    try:
        return json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return ""


def _partial_json_number(text: str, field: str) -> int | float | None:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*(-?\d+(?:\.\d+)?)', text)
    if not match:
        return None
    value = float(match.group(1))
    return int(value) if value.is_integer() else value


def _is_mature(phase: Any) -> bool:
    normalized = str(phase or "").strip().upper().replace(" ", "")
    return (
        is_marketed_phase(normalized)
        or is_nda_bla_phase(normalized)
        or bool(
            re.search(r"(^|[^0-9])3期($|[^0-9])", normalized)
            or "III期" in normalized
            or "PHASE3" in normalized
            or "PHASEIII" in normalized
        )
    )


def _is_noncommercial(text: str) -> bool:
    return any(token.lower() in text.lower() for token in NONCOMMERCIAL_TOKENS)


def _is_broad_indication(text: Any) -> bool:
    normalized = re.sub(r"[\s\-_/,，、]+", "", str(text or "")).lower()
    return any(token in normalized for token in BROAD_INDICATION_TOKENS)


def _selection_supported_by_sources(
    selected_indication: str,
    search_results: list[dict[str, Any]],
    source_urls: list[str],
    same_target_clinical_evidence: list[dict[str, Any]] | None = None,
) -> bool:
    selected_sources = [item for item in search_results if item.get("url") in set(source_urls)]
    evidence = _normalize(
        " ".join(
            f"{item.get('title') or ''} {item.get('snippet') or ''}"
            for item in selected_sources
        )
    )
    clinical_evidence = _normalize(
        " ".join(
            f"{item.get('sheetName') or ''} {item.get('indication') or ''}"
            for item in (same_target_clinical_evidence or [])
        )
    )
    combined_evidence = f"{evidence} {clinical_evidence}".strip()
    selected_families = _tumor_families(selected_indication)
    evidence_families = _tumor_families(combined_evidence)
    if selected_families and selected_families & evidence_families:
        return True
    specific_terms = {
        _normalize(term)
        for term in re.findall(r"[\u4e00-\u9fff]{2,12}(?:癌|瘤)|\b[A-Z]{2,8}\b", selected_indication)
        if term
    }
    return bool(specific_terms) and any(term in combined_evidence for term in specific_terms)


def _indication_supported_by_text(selected_indication: str, evidence_text: str) -> bool:
    selected_families = _tumor_families(selected_indication)
    evidence_families = _tumor_families(evidence_text)
    if selected_families:
        return bool(selected_families & evidence_families)
    normalized_selected = _normalize(selected_indication)
    normalized_evidence = _normalize(evidence_text)
    return bool(
        normalized_selected
        and normalized_evidence
        and (
            normalized_selected in normalized_evidence
            or normalized_evidence in normalized_selected
        )
    )


def _evidence_priority(
    selected_indication: str,
    parent_indication: str,
    same_target_clinical_evidence: list[dict[str, Any]],
    search_results: list[dict[str, Any]],
    source_urls: list[str],
) -> str:
    if _indication_supported_by_text(selected_indication, parent_indication):
        return "own_pipeline"
    clinical_text = " ".join(
        f"{item.get('sheetName') or ''} {item.get('indication') or ''}"
        for item in same_target_clinical_evidence
    )
    if _indication_supported_by_text(selected_indication, clinical_text):
        return "same_target_clinical"
    selected_sources = [item for item in search_results if item.get("url") in set(source_urls)]
    public_text = " ".join(
        f"{item.get('title') or ''} {item.get('snippet') or ''}"
        for item in selected_sources
    )
    if _indication_supported_by_text(selected_indication, public_text):
        return "same_target_public"
    return "unresolved"


def _tumor_families(value: Any) -> set[str]:
    normalized = _normalize(value)
    return {
        family
        for family, aliases in TUMOR_FAMILY_ALIASES
        if any(_normalize(alias) in normalized for alias in aliases)
    }


def _phase_rank(phase: Any) -> int:
    normalized = str(phase or "").lower().replace(" ", "")
    if "2/3" in normalized or "2b" in normalized or "2期" in normalized:
        return 4
    if "1/2" in normalized or "1b/2" in normalized:
        return 3
    if "1b" in normalized:
        return 2
    if "1期" in normalized or "phase1" in normalized:
        return 1
    return 0


def _fallback_launch_year(phase: Any) -> int:
    rank = _phase_rank(phase)
    return {4: 2030, 3: 2031, 2: 2032, 1: 2033}.get(rank, 2032)


def _fallback_pos(phase: Any) -> float:
    rank = _phase_rank(phase)
    return {4: 0.30, 3: 0.20, 2: 0.15, 1: 0.10}.get(rank, 0.10)


def _indication_identity(item: dict[str, Any]) -> str:
    return str(item.get("sourceRowIdentity") or "|").strip()


def _dedupe_search_results(items: list[dict[str, str]]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        key = item.get("url") or f"{item.get('title')}|{item.get('snippet')}"
        if not key or key in seen:
            continue
        seen.add(key)
        results.append(item)
    return results


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _strip_html(value: Any) -> str:
    return _clean_text(html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))))


def _decode_js_string(value: str) -> str:
    return html.unescape(json.loads(f'"{value}"'))


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _bounded_int(value: Any, minimum: int, maximum: int) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _bounded_float(value: Any, minimum: float, maximum: float) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _summary(payload: dict[str, Any], output_path: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "outputPath": str(output_path),
        "companyName": payload.get("companyName"),
        "taskCount": payload.get("taskCount"),
        "selectionCount": payload.get("selectionCount"),
        "missingSelectionCount": payload.get("missingSelectionCount"),
        "searchProvider": payload.get("searchProvider"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
