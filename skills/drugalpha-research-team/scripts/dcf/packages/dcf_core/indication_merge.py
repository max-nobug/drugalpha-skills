"""Merge duplicate pipeline rows into one DCF indication without losing evidence."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .phase_rules import is_marketed_phase, is_nda_bla_phase


def merge_duplicate_indications(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group rows by normalized indication text and retain all trial evidence."""

    groups: dict[str, list[dict[str, Any]]] = {}
    for index, record in enumerate(records):
        semantic_group = str(record.get("semanticMergeGroup") or "").strip()
        key = (
            f"__semantic__::{semantic_group}"
            if semantic_group
            else normalized_indication_key(record.get("indicationText"))
        )
        if not key:
            key = f"__row__::{record.get('sourceRowIdentity') or index}"
        groups.setdefault(key, []).append(record)
    return [_merge_group(group) for group in groups.values()]


def normalized_indication_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\s\u3000,，、;；:：/\\()（）\[\]【】{}<>《》\-—_·.。]+", "", text)


def _merge_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    representative = _select_representative(records)
    merged = dict(representative)
    best_match = max(
        (record.get("diseaseMatch") or {} for record in records),
        key=lambda item: float(item.get("confidence") or 0),
        default={},
    )
    if best_match:
        merged["diseaseMatch"] = dict(best_match)

    trial_evidence = [_trial_evidence(record) for record in records]
    merged["trialEvidence"] = trial_evidence
    merged["trialRegNos"] = _unique_values(record.get("trialRegNo") for record in records)
    merged["sponsors"] = _unique_values(record.get("sponsor") for record in records)
    merged["mergedTrialCount"] = len(records)
    merged["mergedSourceRowIdentities"] = _unique_values(
        record.get("sourceRowIdentity") for record in records
    )
    merged["mergedIndicationVariants"] = _unique_values(
        record.get("indicationText") for record in records
    )
    merged["representativeSelectionBasis"] = (
        "DCF按药物+标准化适应症合并；代表记录按获批上市>NDA/BLA>3期/注册性临床>2期>早期阶段选择，"
        "同阶段优先采用最早阶段开始时间。"
    )
    semantic_canonical = next(
        (
            str(record.get("semanticCanonicalIndication") or "").strip()
            for record in records
            if str(record.get("semanticCanonicalIndication") or "").strip()
        ),
        "",
    )
    if semantic_canonical:
        merged["indicationText"] = semantic_canonical
        merged["semanticMergeGroup"] = representative.get("semanticMergeGroup")
        merged["semanticMergeReason"] = representative.get("semanticMergeReason")
        merged["semanticMergeConfidence"] = representative.get("semanticMergeConfidence")
    return merged


def _select_representative(records: list[dict[str, Any]]) -> dict[str, Any]:
    highest_rank = max((_phase_rank(record) for record in records), default=0)
    candidates = [record for record in records if _phase_rank(record) == highest_rank]
    return min(candidates, key=_representative_sort_key)


def _representative_sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    date = str(record.get("statusStartDate") or "").strip()
    date_key = date if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", date) else "9999-99-99"
    row_order = record.get("rowOrder")
    try:
        row_order_key = int(row_order)
    except (TypeError, ValueError):
        row_order_key = 10**9
    return (
        date_key,
        0 if record.get("trialRegNo") else 1,
        row_order_key,
        str(record.get("sourceRowIdentity") or ""),
    )


def _phase_rank(record: dict[str, Any]) -> int:
    phase = _normalize(record.get("phase"))
    context = _normalize(
        " ".join(
            str(value)
            for value in (
                record.get("phase"),
                record.get("indicationText"),
                _progress_text(record.get("latestProgress")),
            )
            if value
        )
    )
    if is_marketed_phase(phase):
        return 60
    if is_nda_bla_phase(phase):
        return 50
    if (
        "2/3期" in phase
        or "II/III期" in phase
        or "PHASE2/3" in phase
        or "PHASEII/III" in phase
        or re.search(r"(^|[^0-9])3期($|[^0-9])", phase)
        or "III期" in phase
        or "PHASE3" in phase
        or "PHASEIII" in phase
    ):
        return 40
    is_phase2 = bool(
        re.search(r"(^|[^0-9])2期($|[^0-9])", phase)
        or "II期" in phase
        or "PHASE2" in phase
        or "PHASEII" in phase
    )
    if is_phase2 and any(token in context for token in ("注册性", "关键性", "关键临床", "附条件批准")):
        return 35
    if is_phase2:
        return 30
    if "1/2" in phase or "IB/II" in phase:
        return 20
    if "1期" in phase or "I期" in phase or "PHASE1" in phase or "PHASEI" in phase:
        return 10
    return 0


def _trial_evidence(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_row_identity": record.get("sourceRowIdentity"),
        "indication_text": record.get("indicationText"),
        "phase": record.get("phase"),
        "status_start_date": record.get("statusStartDate"),
        "trial_reg_no": record.get("trialRegNo"),
        "sponsor": record.get("sponsor"),
        "comparator": record.get("comparator"),
        "primary_endpoint": record.get("primaryEndpoint"),
        "enrollment_text": record.get("enrollmentText"),
        "latest_progress": record.get("latestProgress"),
        "source_links": record.get("sourceLinks") or {},
    }


def _unique_values(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _progress_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(str(item) for item in value.values() if item is not None)
    return str(value or "")


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")).upper())
