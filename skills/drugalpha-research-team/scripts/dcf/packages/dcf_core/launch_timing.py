"""Deterministic China launch-year assumptions for DCF drafts."""

from __future__ import annotations

import re
from typing import Any

from .defaults import FORECAST_YEARS
from .phase_rules import is_marketed_phase, is_nda_bla_phase


NDA_REVIEW_YEARS = 1
PHASE3_TO_APPROVAL_YEARS = 3
PHASE2_TO_APPROVAL_YEARS = 5
STALE_CLINICAL_FLOOR_YEARS = 2


def extract_status_start_date_from_identity(value: Any) -> str | None:
    """Recover a trailing pipeline date without treating unrelated numbers as dates.

    Historical published rows sometimes carried the phase date only in the
    stable ``rowIdentity`` string.  Keep this as a read-only compatibility
    fallback so newly generated DCF snapshots do not silently treat an old
    marketed asset as launching in the forecast base year.
    """

    text = str(value or "").strip()
    match = re.search(
        r"(?:^|\|)((?:19|20|21)\d{2}[-/.](?:0?[1-9]|1[0-2])(?:[-/.](?:0?[1-9]|[12]\d|3[01]))?)$",
        text,
    )
    return match.group(1).replace("/", "-").replace(".", "-") if match else None


def resolve_china_launch_year(
    *,
    phase: Any,
    status_start_date: Any = None,
    indication_text: Any = None,
    latest_progress: Any = None,
    ai_suggested_year: Any = None,
    model_base_year: int | None = None,
) -> tuple[int | None, str]:
    """Return the China launch year and an auditable basis comment.

    ``statusStartDate`` is the start date of the currently published pipeline
    phase. It therefore represents the NDA/BLA filing-stage start date for an
    NDA/BLA row and the relevant clinical-stage start date for a phase 2/3 row.
    """

    base_year = int(model_base_year or FORECAST_YEARS[0])
    start_year = _extract_year(status_start_date)
    phase_kind = _phase_kind(phase, indication_text, latest_progress)
    start_label = str(status_start_date or "").strip()

    if phase_kind == "approved":
        if start_year:
            return start_year, f"DrugAlpha管线获批上市阶段开始时间 {start_label}；按实际获批年份计"
        return base_year, f"已上市但缺少获批日期；按DCF预测起始年 {base_year} 计"

    if phase_kind == "nda_bla":
        if start_year:
            calculated = start_year + NDA_REVIEW_YEARS
            if calculated < base_year:
                delayed = base_year + NDA_REVIEW_YEARS
                return delayed, (
                    f"DrugAlpha管线NDA/BLA阶段开始时间 {start_label} + {NDA_REVIEW_YEARS}年；"
                    f"原推算年份已过且仍未获批，延期至 {delayed} 年"
                )
            return calculated, (
                f"DrugAlpha管线NDA/BLA阶段开始时间 {start_label} + {NDA_REVIEW_YEARS}年"
            )
        fallback = base_year + NDA_REVIEW_YEARS
        return fallback, f"NDA/BLA缺少阶段开始时间；按预测起始年 + {NDA_REVIEW_YEARS}年兜底"

    if phase_kind in {"phase3", "registrational_phase2"}:
        if start_year:
            calculated = start_year + PHASE3_TO_APPROVAL_YEARS
            if calculated < base_year:
                delayed = base_year + STALE_CLINICAL_FLOOR_YEARS
                return delayed, (
                    f"DrugAlpha管线{_phase_label(phase_kind)}开始时间 {start_label} + "
                    f"{PHASE3_TO_APPROVAL_YEARS}年；原推算年份已过且尚未申报，延期至 {delayed} 年"
                )
            return calculated, (
                f"DrugAlpha管线{_phase_label(phase_kind)}开始时间 {start_label} + "
                f"{PHASE3_TO_APPROVAL_YEARS}年"
            )
        fallback = base_year + PHASE3_TO_APPROVAL_YEARS
        return fallback, (
            f"{_phase_label(phase_kind)}缺少开始时间；按预测起始年 + "
            f"{PHASE3_TO_APPROVAL_YEARS}年兜底"
        )

    if phase_kind == "phase2":
        if start_year:
            calculated = start_year + PHASE2_TO_APPROVAL_YEARS
            if calculated < base_year:
                delayed = base_year + STALE_CLINICAL_FLOOR_YEARS
                return delayed, (
                    f"DrugAlpha管线2期开始时间 {start_label} + {PHASE2_TO_APPROVAL_YEARS}年；"
                    f"原推算年份已过且尚未申报，延期至 {delayed} 年"
                )
            return calculated, (
                f"DrugAlpha管线2期开始时间 {start_label} + {PHASE2_TO_APPROVAL_YEARS}年"
            )
        fallback = _bounded_year(ai_suggested_year)
        if fallback is not None:
            return fallback, "DrugAlpha管线缺少2期开始时间，采用DrugAlpha AI可编辑建议"
        fallback = base_year + PHASE2_TO_APPROVAL_YEARS
        return fallback, f"2期缺少开始时间；按预测起始年 + {PHASE2_TO_APPROVAL_YEARS}年兜底"

    fallback = _bounded_year(ai_suggested_year)
    if fallback is not None:
        return fallback, "DrugAlpha管线阶段/开始时间不足，采用DrugAlpha AI可编辑建议"
    return None, "缺少可用于推算上市时间的管线阶段和开始时间"


def _phase_kind(phase: Any, indication_text: Any, latest_progress: Any) -> str | None:
    phase_text = _normalize(phase)
    context = _normalize(
        " ".join(
            part
            for part in (
                str(phase or ""),
                str(indication_text or ""),
                _progress_text(latest_progress),
            )
            if part
        )
    )
    if is_marketed_phase(phase_text):
        return "approved"
    if is_nda_bla_phase(phase_text):
        return "nda_bla"
    if (
        "2/3期" in phase_text
        or "II/III期" in phase_text
        or "PHASE2/3" in phase_text
        or "PHASEII/III" in phase_text
        or re.search(r"(^|[^0-9])3期($|[^0-9])", phase_text)
        or "III期" in phase_text
        or "PHASE3" in phase_text
        or "PHASEIII" in phase_text
    ):
        return "phase3"
    is_phase2 = bool(
        re.search(r"(^|[^0-9])2期($|[^0-9])", phase_text)
        or "II期" in phase_text
        or "PHASE2" in phase_text
        or "PHASEII" in phase_text
    )
    if is_phase2 and any(token in context for token in ("注册性", "关键性", "关键临床", "附条件批准")):
        return "registrational_phase2"
    if is_phase2:
        return "phase2"
    return None


def _extract_year(value: Any) -> int | None:
    if isinstance(value, (int, float)) and 1900 <= int(value) <= 2200:
        return int(value)
    match = re.search(r"(?:19|20|21)\d{2}", str(value or ""))
    return int(match.group(0)) if match else None


def _bounded_year(value: Any) -> int | None:
    try:
        year = int(float(value))
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= 2200 else None


def _progress_text(latest_progress: Any) -> str:
    if isinstance(latest_progress, dict):
        return " ".join(str(value) for value in latest_progress.values() if value is not None)
    return str(latest_progress or "")


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").upper())


def _phase_label(phase_kind: str) -> str:
    return "注册性2期" if phase_kind == "registrational_phase2" else "3期/注册性临床"
