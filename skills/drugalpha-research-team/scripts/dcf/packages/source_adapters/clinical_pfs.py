"""Read published clinical mPFS values for DCF treatment-duration prefill.

The adapter opens the clinical databases in SQLite read-only mode and only reads
``published_metric_values``.  It never reads candidate/review-layer values and
never writes back to the clinical platform.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import statistics
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any


ONCOLOGY_AREAS = ("oncology-solid", "hematology")
ONCOLOGY_MARKERS = (
    "癌",
    "瘤",
    "白血病",
    "肉瘤",
    "carcinoma",
    "cancer",
    "tumor",
    "tumour",
    "lymphoma",
    "leukemia",
    "myeloma",
    "nsclc",
    "sclc",
)
TUMOR_FAMILY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("nsclc", ("非小细胞肺癌", "nsclc")),
    ("sclc", ("小细胞肺癌", "sclc")),
    ("hcc", ("肝细胞癌", "肝癌", "hcc")),
    ("thyroid", ("甲状腺癌", "thyroidcancer")),
    ("neuroendocrine", ("神经内分泌癌", "神经内分泌肿瘤", "neuroendocrine")),
    ("gastric", ("胃癌", "gastriccancer")),
    ("esophageal", ("食管癌", "esophagealcancer")),
    ("colorectal", ("结直肠癌", "直肠癌", "结肠癌", "crc")),
    ("breast", ("乳腺癌", "breastcancer")),
    ("ovarian", ("卵巢癌", "ovariancancer")),
    ("pancreatic", ("胰腺癌", "胰腺导管腺癌", "pancreaticcancer", "pdac")),
    ("prostate", ("前列腺癌", "prostatecancer")),
    ("renal", ("肾细胞癌", "肾癌", "rcc")),
    ("urothelial", ("尿路上皮癌", "膀胱癌", "urothelial")),
    ("melanoma", ("黑色素瘤", "melanoma")),
    ("cervical", ("宫颈癌", "cervicalcancer")),
    ("endometrial", ("子宫内膜癌", "endometrial")),
    ("head_neck", ("头颈癌", "头颈鳞癌", "hnscc")),
    ("nasopharyngeal", ("鼻咽癌", "nasopharyngeal")),
    ("biliary", ("胆管癌", "胆道癌", "biliary")),
    ("mesothelioma", ("间皮瘤", "mesothelioma")),
    ("sarcoma", ("肉瘤", "sarcoma")),
    ("glioma", ("胶质母细胞瘤", "胶质瘤", "gbm", "glioma")),
    ("lymphoma", ("淋巴瘤", "lymphoma")),
    ("myeloma", ("多发性骨髓瘤", "骨髓瘤", "myeloma")),
    ("leukemia", ("白血病", "leukemia")),
    ("mds", ("骨髓增生异常综合征", "mds")),
)
COMPARATOR_MARKERS = (
    "placebo",
    "安慰剂",
    "chemotherapy",
    "化疗",
    "对照",
    "standardofcare",
    "soc",
)
NON_VALUE_LABEL_MARKERS = ("hr", "hazard", "风险比", "95%ci", "p值", "p=")
SHORT_COURSE_NON_ONCOLOGY_MARKERS = (
    "有限疗程",
    "短疗程",
    "固定疗程",
    "完整疗程",
    "根除治疗",
    "丙型肝炎",
    "丙肝",
    "hepatitis c",
    "hcv",
    "流感",
    "influenza",
    "新型冠状病毒",
    "新冠",
    "covid-19",
    "辅助诊断",
    "全身显像",
    "清甲",
    "放射性碘",
    "手术",
    "术中",
    "止血",
    "创面",
    "一次性",
    "单次",
)
TARGET_NOISE = (
    "adc",
    "单抗",
    "双抗",
    "三抗",
    "抗体",
    "bite",
    "抑制剂",
    "激动剂",
    "融合蛋白",
)


def resolve_tumor_treatment_duration(
    *,
    drug_name: str | None,
    target: str | None,
    indication_text: str | None,
    line_of_therapy: str | None,
    default_months: float,
    clinical_data_dir: Path | None = None,
) -> dict[str, Any]:
    """Resolve exact-line annual treatment months from published DrugAlpha mPFS.

    The main DCF path is entirely local: exact published evidence -> adjacent
    published evidence -> fixed line rule.  Public-web research is not allowed
    to block model generation.
    """

    fallback = {
        "mode": "line_rule_fallback",
        "treatmentMonths": round(float(default_months), 1),
        "referencePfsMonths": None,
        "source": "DCF线次规则兜底",
        "sourceDetail": _fallback_detail(line_of_therapy, default_months),
        "requiresHumanReview": True,
        "references": [],
    }
    normalized_indication = _normalize(indication_text)
    is_short_course = _contains_any(normalized_indication, SHORT_COURSE_NON_ONCOLOGY_MARKERS)
    if is_short_course or not _is_oncology_indication(indication_text):
        fallback["mode"] = "non_oncology_short_course" if is_short_course else "non_oncology_continuous"
        fallback["treatmentMonths"] = 1.0 if is_short_course else 12.0
        fallback["source"] = "DCF非肿瘤疗程规则"
        fallback["sourceDetail"] = (
            "非肿瘤一次性/短疗程场景，暂按1.0个疗程月当量预填，需结合单疗程价格复核"
            if is_short_course
            else "非肿瘤长期维持治疗，按全年持续治疗12.0个月预填"
        )
        return fallback

    data_dir = clinical_data_dir or _clinical_data_dir()
    rows = _published_pfs_rows(str(data_dir.resolve()))
    if not rows:
        fallback["sourceDetail"] += "；未读取到DrugAlpha已发布临床mPFS"
        return fallback

    drug_matches = _rank_matches(
        rows,
        mode="same_drug_same_indication",
        drug_name=drug_name,
        target=target,
        indication_text=indication_text,
        line_of_therapy=line_of_therapy,
        line_mode="exact",
    )
    selected = drug_matches or _rank_matches(
        rows,
        mode="same_target_same_indication",
        drug_name=drug_name,
        target=target,
        indication_text=indication_text,
        line_of_therapy=line_of_therapy,
        line_mode="exact",
    )
    if not selected:
        adjacent = resolve_adjacent_line_treatment_duration(
            drug_name=drug_name,
            target=target,
            indication_text=indication_text,
            line_of_therapy=line_of_therapy,
            clinical_data_dir=data_dir,
        )
        if adjacent:
            return adjacent
        fallback["sourceDetail"] += "；未找到同药/同靶点同线次或相邻线次的已发布mPFS，采用固定线次规则"
        return fallback

    mode = str(selected[0]["mode"])
    top_score = selected[0]["score"]
    comparable = [item for item in selected if item["score"] == top_score][:8]
    pfs_months = round(float(statistics.median(item["pfsMonths"] for item in comparable)), 1)
    annual_months = pfs_months
    mode_label = "同药+同适应症" if mode == "same_drug_same_indication" else "同靶点+同适应症"
    references = [
        {
            "area": item["area"],
            "entryId": item["entryId"],
            "drugName": item["drugName"],
            "target": item["target"],
            "sheetName": item["sheetName"],
            "lineOfTherapy": item["sheetLineOfTherapy"],
            "pfsMonths": item["pfsMonths"],
            "displayValue": item["displayValue"],
            "publishedAt": item["publishedAt"],
        }
        for item in comparable
    ]
    return {
        "mode": mode,
        "treatmentMonths": annual_months,
        "referencePfsMonths": pfs_months,
        "source": f"DrugAlpha已发布临床mPFS（{mode_label}）",
        "sourceDetail": (
            f"{mode_label}：取{len(comparable)}条最高相关已发布记录的mPFS中位数"
            f"{pfs_months:.1f}个月；各预测年度直接采用该mPFS，不设置12个月上限"
        ),
        "requiresHumanReview": mode != "same_drug_same_indication",
        "references": references,
    }


def resolve_adjacent_line_treatment_duration(
    *,
    drug_name: str | None,
    target: str | None,
    indication_text: str | None,
    line_of_therapy: str | None,
    clinical_data_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Return the closest published adjacent-line mPFS after exact local matching fails."""

    if not _is_oncology_indication(indication_text):
        return None
    data_dir = clinical_data_dir or _clinical_data_dir()
    rows = _published_pfs_rows(str(data_dir.resolve()))
    if not rows:
        return None
    selected = _rank_matches(
        rows,
        mode="same_drug_adjacent_line",
        drug_name=drug_name,
        target=target,
        indication_text=indication_text,
        line_of_therapy=line_of_therapy,
        line_mode="adjacent",
    ) or _rank_matches(
        rows,
        mode="same_target_adjacent_line",
        drug_name=drug_name,
        target=target,
        indication_text=indication_text,
        line_of_therapy=line_of_therapy,
        line_mode="adjacent",
    )
    if not selected:
        return None

    mode = str(selected[0]["mode"])
    top_score = selected[0]["score"]
    comparable = [item for item in selected if item["score"] == top_score][:8]
    pfs_months = round(float(statistics.median(item["pfsMonths"] for item in comparable)), 1)
    mode_label = "同药+同适应症相邻线次" if mode == "same_drug_adjacent_line" else "同靶点+同适应症相邻线次"
    query_line = _line_bucket(line_of_therapy) or str(line_of_therapy or "未识别")
    reference_lines = sorted(
        {
            _line_bucket(item.get("sheetLineOfTherapy") or item.get("sheetName"))
            or str(item.get("sheetLineOfTherapy") or "未标注")
            for item in comparable
        }
    )
    references = [
        {
            "area": item["area"],
            "entryId": item["entryId"],
            "drugName": item["drugName"],
            "target": item["target"],
            "sheetName": item["sheetName"],
            "lineOfTherapy": item["sheetLineOfTherapy"],
            "pfsMonths": item["pfsMonths"],
            "displayValue": item["displayValue"],
            "publishedAt": item["publishedAt"],
            "matchLevel": "adjacent_line",
        }
        for item in comparable
    ]
    return {
        "mode": mode,
        "treatmentMonths": pfs_months,
        "referencePfsMonths": pfs_months,
        "source": f"DrugAlpha已发布临床mPFS（{mode_label}）",
        "sourceDetail": (
            f"目标线次{query_line}，参考线次{'/'.join(reference_lines)}；取{len(comparable)}条最高相关记录的"
            f"mPFS中位数{pfs_months:.1f}个月；不做主观折价，各预测年度直接采用并要求人工复核"
        ),
        "requiresHumanReview": True,
        "references": references,
    }


def find_same_target_indication_evidence(
    *,
    target: str | None,
    clinical_data_dir: Path | None = None,
    limit: int = 24,
) -> list[dict[str, Any]]:
    """Return published DrugAlpha indication evidence for drugs sharing a target.

    This is a read-only research aid for DCF indication selection.  An entry is
    visible only when it already has at least one ``published_metric_values``
    row, preserving the clinical platform's publish boundary.
    """

    query_tokens = _target_tokens(target)
    if not query_tokens:
        return []
    data_dir = clinical_data_dir or _clinical_data_dir()
    ranked: list[dict[str, Any]] = []
    for row in _published_indication_rows(str(data_dir.resolve())):
        score = _target_overlap_score(query_tokens, _target_tokens(row.get("target")))
        if score <= 0:
            continue
        ranked.append({**row, "targetMatchScore": round(score, 3)})
    ranked.sort(
        key=lambda item: (
            item.get("targetMatchScore") or 0,
            str(item.get("publishedAt") or ""),
        ),
        reverse=True,
    )
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in ranked:
        key = (
            _normalize(item.get("drugName")),
            _normalize(item.get("target")),
            _normalize(item.get("indication") or item.get("sheetName")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= max(1, limit):
            break
    return result


def _rank_matches(
    rows: tuple[dict[str, Any], ...],
    *,
    mode: str,
    drug_name: str | None,
    target: str | None,
    indication_text: str | None,
    line_of_therapy: str | None,
    line_mode: str = "any",
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    query_drugs = _identity_aliases(drug_name)
    query_targets = _target_tokens(target)
    for row in rows:
        indication_score = _indication_score(
            indication_text,
            " ".join(
                str(row.get(key) or "")
                for key in ("sheetName", "indication", "sheetLineOfTherapy")
            ),
        )
        if indication_score <= 0:
            continue

        source_drugs = _identity_aliases(row.get("drugName"))
        if mode.startswith("same_drug"):
            identity_score = _identity_overlap_score(query_drugs, source_drugs)
            if identity_score <= 0:
                continue
        else:
            source_targets = _target_tokens(row.get("target"))
            identity_score = _target_overlap_score(query_targets, source_targets)
            if identity_score <= 0:
                continue

        pfs_months = _representative_pfs(row)
        if pfs_months is None:
            continue
        source_line = row.get("sheetLineOfTherapy") or row.get("sheetName")
        query_bucket = _line_bucket(line_of_therapy)
        source_bucket = _line_bucket(source_line)
        if line_mode == "exact" and query_bucket and source_bucket != query_bucket:
            continue
        if line_mode == "adjacent" and not _is_adjacent_line(query_bucket, source_bucket):
            continue
        line_score = _line_match_score(line_of_therapy, source_line)
        if line_mode == "adjacent":
            line_score = _adjacent_line_score(query_bucket, source_bucket)
        ranked.append(
            {
                **row,
                "mode": mode,
                "pfsMonths": pfs_months,
                "score": (round(identity_score, 3), indication_score, line_score),
            }
        )
    return sorted(
        ranked,
        key=lambda item: (item["score"], str(item.get("publishedAt") or "")),
        reverse=True,
    )


@lru_cache(maxsize=4)
def _published_pfs_rows(clinical_data_dir: str) -> tuple[dict[str, Any], ...]:
    data_dir = Path(clinical_data_dir)
    result: list[dict[str, Any]] = []
    for area in ONCOLOGY_AREAS:
        db_path = data_dir / area / "clinical_trials.db"
        if not db_path.exists():
            continue
        try:
            connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            query = """
                SELECT
                    re.entry_id,
                    re.drug_name,
                    re.regimen_name,
                    re.target_summary,
                    sd.sheet_name,
                    sd.indication,
                    sd.line_of_therapy AS sheet_line_of_therapy,
                    pmv.value_num,
                    pmv.value_json,
                    pmv.display_value,
                    pmv.published_at
                FROM published_metric_values pmv
                JOIN regimen_entries re ON re.entry_id = pmv.entry_id
                JOIN metric_definitions md ON md.metric_id = pmv.metric_id
                JOIN sheet_definitions sd ON sd.sheet_id = re.sheet_id
                WHERE md.metric_code = 'median_pfs_months'
            """
            for record in connection.execute(query):
                result.append(
                    {
                        "area": area,
                        "entryId": record["entry_id"],
                        "drugName": record["drug_name"],
                        "regimenName": record["regimen_name"],
                        "target": record["target_summary"],
                        "sheetName": record["sheet_name"],
                        "indication": record["indication"],
                        "sheetLineOfTherapy": record["sheet_line_of_therapy"],
                        "valueNum": record["value_num"],
                        "valueJson": record["value_json"],
                        "displayValue": record["display_value"],
                        "publishedAt": record["published_at"],
                    }
                )
        except sqlite3.Error:
            continue
        finally:
            if "connection" in locals():
                connection.close()
                del connection
    return tuple(result)


@lru_cache(maxsize=4)
def _published_indication_rows(clinical_data_dir: str) -> tuple[dict[str, Any], ...]:
    data_dir = Path(clinical_data_dir)
    result: list[dict[str, Any]] = []
    for area in ONCOLOGY_AREAS:
        db_path = data_dir / area / "clinical_trials.db"
        if not db_path.exists():
            continue
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            query = """
                SELECT
                    re.entry_id,
                    re.drug_name,
                    re.target_summary,
                    sd.sheet_name,
                    sd.indication,
                    sd.line_of_therapy AS sheet_line_of_therapy,
                    MAX(pmv.published_at) AS published_at
                FROM regimen_entries re
                JOIN sheet_definitions sd ON sd.sheet_id = re.sheet_id
                JOIN published_metric_values pmv ON pmv.entry_id = re.entry_id
                GROUP BY
                    re.entry_id, re.drug_name, re.target_summary,
                    sd.sheet_name, sd.indication, sd.line_of_therapy
            """
            for record in connection.execute(query):
                result.append(
                    {
                        "area": area,
                        "entryId": record["entry_id"],
                        "drugName": record["drug_name"],
                        "target": record["target_summary"],
                        "sheetName": record["sheet_name"],
                        "indication": record["indication"],
                        "lineOfTherapy": record["sheet_line_of_therapy"],
                        "publishedAt": record["published_at"],
                    }
                )
        except sqlite3.Error:
            continue
        finally:
            if connection is not None:
                connection.close()
    return tuple(result)


def _representative_pfs(row: dict[str, Any]) -> float | None:
    display = str(row.get("displayValue") or "").strip()
    if not display or display in {"/", "-", "--", "NR", "NA", "N/A"}:
        return None
    candidates = _structured_values(row.get("valueJson"))
    if not candidates:
        direct = _valid_month_value(row.get("valueNum"), display)
        if direct is not None:
            return direct
        candidates = _display_values(display)
    if not candidates:
        return None

    overall = [value for label, value in candidates if _normalize(label) in {"overall", "all", "整体", "总体"}]
    if overall:
        return round(float(statistics.median(overall)), 1)

    source_aliases = _identity_aliases(row.get("drugName")) | _identity_aliases(row.get("regimenName"))
    treatment_arm = [
        value
        for label, value in candidates
        if _identity_overlap_score(source_aliases, _identity_aliases(label)) > 0
        and not _contains_any(_normalize(label), COMPARATOR_MARKERS)
    ]
    if treatment_arm:
        return round(float(statistics.median(treatment_arm)), 1)

    if any(_contains_any(_normalize(label), COMPARATOR_MARKERS) for label, _value in candidates):
        return None
    return round(float(statistics.median(value for _label, value in candidates)), 1)


def _structured_values(raw_json: Any) -> list[tuple[str, float]]:
    if not raw_json:
        return []
    try:
        payload = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
    except (TypeError, ValueError):
        return []
    result: list[tuple[str, float]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        label = next(
            (str(value.get(key) or "").strip() for key in ("arm_label", "subgroup_label", "label") if value.get(key)),
            "",
        )
        raw_value = value.get("value_num")
        if raw_value is None:
            raw_value = value.get("value_text")
        parsed = _valid_month_value(raw_value, str(raw_value or ""), label=label)
        if parsed is not None:
            result.append((label, parsed))
        for child in value.values():
            if isinstance(child, (dict, list)):
                visit(child)

    visit(payload)
    unique: list[tuple[str, float]] = []
    seen: set[tuple[str, float]] = set()
    for item in result:
        key = (_normalize(item[0]), item[1])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _display_values(display: str) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    for line in re.split(r"[\r\n；;]+", display):
        line = line.strip()
        if not line or "%" in line:
            continue
        parts = re.split(r"[:：]", line, maxsplit=1)
        label = parts[0].strip() if len(parts) == 2 else ""
        raw_value = parts[1].strip() if len(parts) == 2 else line
        parsed = _valid_month_value(raw_value, raw_value, label=label)
        if parsed is not None:
            result.append((label, parsed))
    return result


def _valid_month_value(value: Any, raw_text: str, *, label: str = "") -> float | None:
    normalized_label = _normalize(label)
    if _contains_any(normalized_label, NON_VALUE_LABEL_MARKERS) or "%" in raw_text:
        return None
    if re.search(r"\b(?:NR|NE|NA|N/A)\b", raw_text, re.IGNORECASE):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", raw_text)
        if not match:
            return None
        number = float(match.group(1))
    if not 0.5 <= number <= 120:
        return None
    return round(number, 3)


def _indication_score(query: str | None, source: str | None) -> int:
    query_normalized = _normalize(query)
    source_normalized = _normalize(source)
    if not query_normalized or not source_normalized:
        return 0
    query_families = _tumor_families(query_normalized)
    source_families = _tumor_families(source_normalized)
    shared = query_families & source_families
    if shared:
        return 3 + len(shared)
    if len(query_normalized) >= 4 and (
        query_normalized in source_normalized or source_normalized in query_normalized
    ):
        return 2
    return 0


def _tumor_families(normalized_text: str) -> set[str]:
    return {
        family
        for family, aliases in TUMOR_FAMILY_ALIASES
        if any(_normalize(alias) in normalized_text for alias in aliases)
    }


def _line_match_score(query_line: str | None, source_line: str | None) -> int:
    query = _line_bucket(query_line)
    source = _line_bucket(source_line)
    if query and source and query == source:
        return 2
    later_lines = {"二线", "三线", "后线"}
    if query in later_lines and source in later_lines:
        return 1
    if not query or not source:
        return 1
    return 0


def _is_adjacent_line(query: str, source: str) -> bool:
    if not query or not source or query == source:
        return False
    ordered = {"一线": 1, "二线": 2, "三线": 3, "后线": 3}
    if query not in ordered or source not in ordered:
        return False
    if abs(ordered[query] - ordered[source]) <= 1:
        return True
    return query in {"二线", "三线", "后线"} and source in {"二线", "三线", "后线"}


def _adjacent_line_score(query: str, source: str) -> int:
    ordered = {"一线": 1, "二线": 2, "三线": 3, "后线": 3}
    distance = abs(ordered.get(query, 99) - ordered.get(source, -99))
    return 2 if distance <= 1 else 1


def _line_bucket(value: str | None) -> str:
    text = str(value or "")
    if "一线" in text or "初治" in text:
        return "一线"
    if "二线" in text:
        return "二线"
    if "三线" in text:
        return "三线"
    if "后线" in text or "经治" in text or "治疗失败" in text:
        return "后线"
    return ""


def _identity_aliases(value: Any) -> set[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    parts = re.split(r"[/／、,，;；+＋()（）\s]+", text)
    aliases = {_normalize(part) for part in parts if _normalize(part)}
    whole = _normalize(text)
    if whole:
        aliases.add(whole)
    return aliases


def _identity_overlap_score(left: set[str], right: set[str]) -> float:
    score = 0.0
    for lhs in left:
        for rhs in right:
            if lhs == rhs:
                score = max(score, 1.0)
            elif min(len(lhs), len(rhs)) >= 4 and (lhs in rhs or rhs in lhs):
                score = max(score, min(len(lhs), len(rhs)) / max(len(lhs), len(rhs)))
    return score


def _target_tokens(value: Any) -> set[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    for noise in TARGET_NOISE:
        text = text.replace(noise, " ")
    tokens = {
        _normalize(item)
        for item in re.split(r"[/／、,，;；+＋×x\s()（）]+", text)
        if len(_normalize(item)) >= 2
    }
    # Keep mechanism-specific tokens, but also add stable target-family tokens
    # so ``pan-RAS``, ``RAS(ON)`` and ``KRAS G12D`` can support one another.
    compact = _normalize(text)
    for family in ("ras", "kras", "nras", "hras"):
        if family in compact:
            tokens.add("ras")
            if family != "ras":
                tokens.add(family)
    return tokens


def _target_overlap_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    shared = left & right
    if not shared:
        return 0.0
    return len(shared) / max(len(left), len(right))


def _is_oncology_indication(value: str | None) -> bool:
    normalized = _normalize(value)
    return any(marker in normalized for marker in ONCOLOGY_MARKERS)


def _fallback_detail(line_of_therapy: str | None, months: float) -> str:
    line = line_of_therapy or "未识别线次"
    return f"按当前线次规则预填：{line}={float(months):.1f}个月"


def _clinical_data_dir() -> Path:
    if os.getenv('INNOVEST_CLINICAL_DATA_ROOT') or os.getenv('NODE_ENV') == 'production':
        from .io_utils import published_data_root
        return published_data_root('INNOVEST_CLINICAL_DATA_ROOT', '')
    configured = os.getenv("DRUGALPHA_CLINICAL_DATA_DIR")
    if configured:
        return Path(configured)
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "data"
        if (candidate / "oncology-solid" / "clinical_trials.db").exists():
            return candidate
    return Path(os.getenv("DRUGALPHA_SOURCE_ROOT") or Path.cwd()) / "clinical-platform" / "data"


def _normalize(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff%]+", "", normalized)


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)
