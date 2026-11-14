"""Resolve market-specific launch timing for domestic and overseas DCF flows."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .defaults import FORECAST_YEARS
from .launch_timing import resolve_china_launch_year
from .phase_rules import is_marketed_phase, is_nda_bla_phase


OVERSEAS_FALLBACK_YEARS_FROM_CHINA_STAGE_START = 4
OVERSEAS_REGION_MARKERS = (
    "美国",
    "欧盟",
    "欧洲",
    "英国",
    "日本",
    "加拿大",
    "澳大利亚",
    "韩国",
    "新加坡",
    "瑞士",
    "海外",
    "境外",
    "全球",
    "国际",
)
NON_FINAL_MARKET_STATUS_MARKERS = (
    "预计",
    "预期",
    "计划",
    "准备",
    "拟",
    "有望",
    "将于",
    "申请",
    "申报",
    "递交",
    "受理",
    "NDA",
    "BLA",
    "MAA",
)


def resolve_overseas_launch_year(
    *,
    indication: dict[str, Any],
    product_indications: list[dict[str, Any]],
    domestic_launch_year: int | None,
) -> tuple[int | None, str]:
    """Prefer NCT trial timing; otherwise use the earliest top China-stage start +4y."""

    overseas_marketed_evidence = [
        item
        for item in _indication_evidence(indication)
        if is_explicit_overseas_marketed_status(item)
        and _extract_year(item.get("status_start_date")) is not None
    ]
    if overseas_marketed_evidence:
        selected = min(overseas_marketed_evidence, key=lambda item: _date_key(item.get("status_start_date")))
        launch_year = _extract_year(selected.get("status_start_date"))
        return launch_year, (
            f"读取境外上市标签 {selected.get('phase') or ''} 的实际日期 "
            f"{selected.get('status_start_date')}"
        )

    nct_evidence = [item for item in _indication_evidence(indication) if _has_nct(item)]
    if nct_evidence:
        selected = min(nct_evidence, key=_stage_then_date_sort_key)
        launch_year, basis = resolve_china_launch_year(
            phase=selected.get("phase"),
            status_start_date=selected.get("status_start_date"),
            indication_text=selected.get("indication_text") or indication.get("indicationText"),
            latest_progress=selected.get("latest_progress"),
        )
        if launch_year is not None:
            return launch_year, (
                f"海外优先采用NCT临床 {selected.get('trial_reg_no') or ''}；"
                f"{basis.replace('DrugAlpha管线', 'NCT海外临床')}"
            )

    domestic_reference = _highest_domestic_stage_earliest_start(product_indications)
    if domestic_reference is not None:
        start_year = _extract_year(domestic_reference.get("status_start_date"))
        if start_year is not None:
            stage_based_year = start_year + OVERSEAS_FALLBACK_YEARS_FROM_CHINA_STAGE_START
            launch_year = max(
                stage_based_year,
                domestic_launch_year if isinstance(domestic_launch_year, int) else stage_based_year,
            )
            return launch_year, (
                "未找到该药可用NCT海外临床；按国内最高开发阶段"
                f"{domestic_reference.get('phase') or '阶段未提供'}最早开始时间 "
                f"{domestic_reference.get('status_start_date')} + "
                f"{OVERSEAS_FALLBACK_YEARS_FROM_CHINA_STAGE_START}年，并确保不早于国内预计上市年"
            )

    if isinstance(domestic_launch_year, int):
        launch_year = domestic_launch_year + OVERSEAS_FALLBACK_YEARS_FROM_CHINA_STAGE_START
        return launch_year, (
            "未找到NCT海外临床及可用国内阶段开始时间；按国内预计上市年 + "
            f"{OVERSEAS_FALLBACK_YEARS_FROM_CHINA_STAGE_START}年兜底"
        )

    fallback = FORECAST_YEARS[0] + OVERSEAS_FALLBACK_YEARS_FROM_CHINA_STAGE_START
    return fallback, (
        "海外及国内开发时间均缺失；按DCF预测起始年 + "
        f"{OVERSEAS_FALLBACK_YEARS_FROM_CHINA_STAGE_START}年占位，需人工复核"
    )


def is_explicit_overseas_marketed_status(item: dict[str, Any]) -> bool:
    """Recognize explicit non-China marketed/approved labels from pipeline evidence."""

    return any(
        _is_final_overseas_market_text(value)
        for value in (
            item.get("phase"),
            item.get("latest_progress"),
            item.get("latestProgress"),
        )
        if value
    )


def is_explicit_overseas_market_status(item: dict[str, Any]) -> bool:
    """Recognize any explicitly overseas development or commercial status.

    This broader predicate selects eligible indications after the product has
    passed the overseas peak-sales-anchor gate. It intentionally includes
    statuses such as US NDA and expected EU approval.
    The narrower ``is_explicit_overseas_marketed_status`` predicate is still used
    when deciding whether a date can be treated as an actual launch date.
    """

    text = re.sub(
        r"\s+",
        "",
        " ".join(
            str(value)
            for value in (
                item.get("phase"),
                item.get("latest_progress"),
                item.get("latestProgress"),
            )
            if value
        ),
    ).upper()
    if not text:
        return False
    has_overseas_region = any(marker.upper() in text for marker in OVERSEAS_REGION_MARKERS)
    has_overseas_regulator = any(marker in text for marker in ("FDA", "EMA", "MHRA", "PMDA"))
    return has_overseas_region or has_overseas_regulator


def _is_final_overseas_market_text(value: Any) -> bool:
    text = re.sub(r"\s+", "", str(value or "")).upper()
    if not text:
        return False
    if any(marker in text for marker in NON_FINAL_MARKET_STATUS_MARKERS):
        return False
    has_market_action = "上市" in text or "获批" in text or "批准" in text
    has_overseas_region = any(marker.upper() in text for marker in OVERSEAS_REGION_MARKERS)
    has_overseas_regulator = any(marker in text for marker in ("FDA", "EMA", "MHRA", "PMDA"))
    return has_market_action and (has_overseas_region or has_overseas_regulator)


def _indication_evidence(indication: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in indication.get("trialEvidence") or []:
        rows.append(
            {
                "indication_text": item.get("indication_text") or item.get("indicationText"),
                "phase": item.get("phase"),
                "status_start_date": item.get("status_start_date") or item.get("statusStartDate"),
                "trial_reg_no": item.get("trial_reg_no") or item.get("trialRegNo"),
                "latest_progress": item.get("latest_progress") or item.get("latestProgress"),
            }
        )
    rows.append(
        {
            "indication_text": indication.get("indicationText"),
            "phase": indication.get("phase"),
            "status_start_date": indication.get("statusStartDate"),
            "trial_reg_no": indication.get("trialRegNo"),
            "latest_progress": indication.get("latestProgress"),
        }
    )
    return _dedupe_evidence(rows)


def _has_nct(item: dict[str, Any]) -> bool:
    return bool(re.search(r"(?:^|[^A-Z0-9])NCT\d+", str(item.get("trial_reg_no") or "").upper()))


def _highest_domestic_stage_earliest_start(
    indications: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = [
        {
            "phase": item.get("phase"),
            "status_start_date": item.get("statusStartDate"),
        }
        for item in indications
        if _phase_rank(item.get("phase")) > 0 and _extract_year(item.get("statusStartDate")) is not None
    ]
    return min(candidates, key=_stage_then_date_sort_key) if candidates else None


def _stage_then_date_sort_key(item: dict[str, Any]) -> tuple[int, date]:
    return (-_phase_rank(item.get("phase")), _date_key(item.get("status_start_date")))


def _phase_rank(phase: Any) -> int:
    value = re.sub(r"\s+", "", str(phase or "").upper())
    if is_marketed_phase(value):
        return 60
    if is_nda_bla_phase(value):
        return 50
    if (
        "2/3期" in value
        or "II/III期" in value
        or "PHASE2/3" in value
        or re.search(r"(^|[^0-9])3期($|[^0-9])", value)
        or "III期" in value
        or "PHASE3" in value
    ):
        return 40
    if (
        re.search(r"(^|[^0-9])2期($|[^0-9])", value)
        or "II期" in value
        or "PHASE2" in value
        or "1/2期" in value
        or "I/II期" in value
    ):
        return 30
    if "1期" in value or "I期" in value or "PHASE1" in value:
        return 10
    return 0


def _extract_year(value: Any) -> int | None:
    match = re.search(r"(?:19|20|21)\d{2}", str(value or ""))
    return int(match.group(0)) if match else None


def _date_key(value: Any) -> date:
    text = str(value or "")
    match = re.search(r"((?:19|20|21)\d{2})[-/.]?(\d{1,2})?[-/.]?(\d{1,2})?", text)
    if not match:
        return date.max
    year = int(match.group(1))
    month = int(match.group(2) or 1)
    day = int(match.group(3) or 1)
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, 1, 1)


def _dedupe_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in rows:
        key = (
            str(item.get("trial_reg_no") or ""),
            str(item.get("phase") or ""),
            str(item.get("status_start_date") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
