"""Use published indication facets plus DrugAlpha AI to merge overlapping DCF populations."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.ai_assumptions.deepseek_client import chat_completion, extract_text
from packages.source_adapters.io_utils import write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-tasks", type=int, default=100)
    args = parser.parse_args()

    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    tasks = build_tasks(snapshot)[: max(0, args.max_tasks)]
    groups: list[dict[str, Any]] = []
    raw_batches: list[dict[str, Any]] = []
    for task in tasks:
        try:
            response = chat_completion(_messages(task), temperature=0.0, max_tokens=1400)
            raw_text = extract_text(response)
            parsed = _parse_json_object(raw_text)
            accepted = validate_response(task, parsed)
            groups.extend(accepted)
            raw_batches.append(
                {
                    "taskId": task["taskId"],
                    "model": response.get("model"),
                    "usage": response.get("usage"),
                    "rawText": raw_text,
                    "acceptedGroupCount": len(accepted),
                }
            )
        except Exception as exc:
            raw_batches.append(
                {
                    "taskId": task["taskId"],
                    "error": str(exc)[:500],
                    "acceptedGroupCount": 0,
                }
            )

    payload = {
        "companyName": (snapshot.get("company") or {}).get("companyName"),
        "sourceSnapshotId": snapshot.get("snapshotId"),
        "taskCount": len(tasks),
        "mergeGroupCount": len(groups),
        "mergedDuplicateRowCount": sum(
            max(len(group.get("memberSourceRowIdentities") or []) - 1, 0)
            for group in groups
        ),
        "mergeGroups": groups,
        "rawBatches": raw_batches,
        "basis": "DrugAlpha已发布适应症结构化索引圈定候选，DrugAlpha AI判断DCF患者群是否实质重叠。",
    }
    write_json(Path(args.out), payload)
    print(json.dumps({
        "ok": True,
        "outputPath": str(Path(args.out)),
        "taskCount": payload["taskCount"],
        "mergeGroupCount": payload["mergeGroupCount"],
        "mergedDuplicateRowCount": payload["mergedDuplicateRowCount"],
    }, ensure_ascii=False, indent=2))
    return 0


def build_tasks(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for product in snapshot.get("pipeline", {}).get("products") or []:
        if not product.get("isDcfSourceProduct") or product.get("modelingMode") == "simplified_growth":
            continue
        clusters: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for indication in product.get("indications") or []:
            if not indication.get("isDcfMaturePhase"):
                continue
            facets = indication.get("indicationFacets") or {}
            if facets.get("parseStatus") not in {"parsed", "partial"}:
                continue
            base_disease = _normalize(facets.get("baseDisease"))
            if not base_disease:
                continue
            treatment_line = _normalize(facets.get("treatmentLine"))
            biomarker_key = _biomarker_key(facets.get("biomarkers") or [])
            clusters.setdefault((base_disease, treatment_line, biomarker_key), []).append(indication)

        for cluster in clusters.values():
            if len(cluster) < 2 or len({
                _normalize(item.get("indicationText")) for item in cluster
            }) < 2:
                continue
            tasks.append(
                {
                    "taskId": f"semantic-merge-{len(tasks) + 1}",
                    "productKey": product.get("productKey") or product.get("drugName"),
                    "drugName": product.get("drugName"),
                    "target": product.get("target"),
                    "candidates": [_compact_candidate(item) for item in cluster],
                }
            )
    return tasks


def validate_response(task: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = {
        str(item.get("sourceRowIdentity") or ""): item
        for item in task.get("candidates") or []
    }
    result: list[dict[str, Any]] = []
    claimed: set[str] = set()
    for index, group in enumerate(payload.get("mergeGroups") or [], start=1):
        members = list(dict.fromkeys(
            str(item).strip()
            for item in group.get("memberSourceRowIdentities") or []
            if str(item).strip() in allowed
        ))
        confidence = _confidence(group.get("confidence"))
        if (
            len(members) < 2
            or confidence < 0.8
            or bool(group.get("needsHumanReview"))
            or claimed.intersection(members)
        ):
            continue
        if not _facets_compatible([allowed[item] for item in members]):
            continue
        canonical = str(group.get("canonicalIndication") or "").strip()
        allowed_names = {
            str(allowed[item].get("indicationText") or "").strip()
            for item in members
        }
        if canonical not in allowed_names:
            canonical = _preferred_canonical([allowed[item] for item in members])
        claimed.update(members)
        result.append(
            {
                "mergeGroupId": f"{task['taskId']}-{index}",
                "productKey": task.get("productKey"),
                "memberSourceRowIdentities": members,
                "canonicalIndication": canonical,
                "confidence": confidence,
                "basisComment": str(group.get("basisComment") or "").strip()[:240],
                "needsHumanReview": False,
            }
        )
    return result


def _messages(task: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "你是创新药DCF适应症去重判断器。候选已由DrugAlpha已发布indication_facets按同药、同基础疾病、"
        "同治疗线次和兼容Biomarker圈定。判断哪些记录对应同一个DCF患者群，而不是仅文字相似。"
        "同一患者群的概括名称与带既往治疗限定的临床描述可以合并；不同Biomarker、不同线次、不同治疗场景"
        "或互斥患者群禁止合并。canonicalIndication必须逐字选择候选中的一个，优先更成熟记录对应的简洁规范名称。"
        "只有高度确定时才返回合并组；不确定则不合并并返回空数组。只输出一个JSON对象，不展示思维过程。"
    )
    user = {
        "schema": {
            "taskId": "原样返回",
            "mergeGroups": [{
                "memberSourceRowIdentities": "至少2项，逐字复制",
                "canonicalIndication": "逐字选择一个候选适应症",
                "confidence": "0到1",
                "basisComment": "不超过80字的可审计结论",
                "needsHumanReview": "boolean；不确定时不要输出该组",
            }],
        },
        "task": task,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def _compact_candidate(indication: dict[str, Any]) -> dict[str, Any]:
    facets = indication.get("indicationFacets") or {}
    return {
        "sourceRowIdentity": indication.get("sourceRowIdentity"),
        "indicationText": indication.get("indicationText"),
        "phase": indication.get("phase"),
        "trialRegNo": indication.get("trialRegNo"),
        "sponsor": indication.get("sponsor"),
        "facets": {
            key: facets.get(key)
            for key in (
                "baseDisease", "diseaseFamily", "diseaseStages", "biomarkers",
                "treatmentLine", "treatmentLines", "treatmentSetting",
                "treatmentSettings", "priorTherapies", "populations", "confidence",
            )
        },
    }


def _facets_compatible(candidates: list[dict[str, Any]]) -> bool:
    diseases = {_normalize((item.get("facets") or {}).get("baseDisease")) for item in candidates}
    lines = {
        _normalize((item.get("facets") or {}).get("treatmentLine"))
        for item in candidates
        if (item.get("facets") or {}).get("treatmentLine")
    }
    biomarkers = {
        _biomarker_key((item.get("facets") or {}).get("biomarkers") or [])
        for item in candidates
        if (item.get("facets") or {}).get("biomarkers")
    }
    settings = {
        _normalize((item.get("facets") or {}).get("treatmentSetting"))
        for item in candidates
        if (item.get("facets") or {}).get("treatmentSetting")
    }
    return len(diseases) == 1 and len(lines) <= 1 and len(biomarkers) <= 1 and len(settings) <= 1


def _preferred_canonical(candidates: list[dict[str, Any]]) -> str:
    return min(
        candidates,
        key=lambda item: (
            -_phase_rank(item.get("phase")),
            len(str(item.get("indicationText") or "")),
        ),
    ).get("indicationText") or ""


def _phase_rank(value: Any) -> int:
    text = _normalize(value)
    if any(token in text for token in ("上市", "批准", "获批")):
        return 4
    if "nda" in text or "bla" in text:
        return 3
    if "3期" in text or "iii期" in text or "phase3" in text:
        return 2
    return 1


def _biomarker_key(items: list[dict[str, Any]]) -> str:
    return "|".join(sorted(
        ":".join(_normalize(item.get(key)) for key in ("name", "status", "subtype"))
        for item in items
        if isinstance(item, dict)
    ))


def _confidence(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return {"high": 0.9, "medium": 0.6, "low": 0.3}.get(_normalize(value), 0.0)


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}


def _normalize(value: Any) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "").lower())


if __name__ == "__main__":
    raise SystemExit(main())
