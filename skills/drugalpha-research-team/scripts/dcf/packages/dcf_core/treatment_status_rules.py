"""Simplified T0-T4 treatment-status rules for DCF patient funnels."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


RULE_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "treatment_status_rules.json"
AUTOIMMUNE_RATIO_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "autoimmune_severity_ratios.json"
BIOMARKER_MARKERS = ("阳性", "阴性", "突变", "扩增", "融合", "表达", "缺失", "过表达")
SYMBOLIC_BIOMARKER_PATTERN = re.compile(
    r"(?:\b(?:hr|er|pr|her2|pd-?l1)\s*[+-]|her2\s*(?:low|ihc\s*[23]\+|过表达|扩增)|her2低表达|(?:msi[-\s]?h|dmmr|pmmr|mss)|(?:tnbc|三阴性)|(?:cldn|claudin)\s*18\s*[.．-]?\s*2\s*(?:[+＋]|阳性|高表达|表达))",
    flags=re.IGNORECASE,
)
ONCOLOGY_MARKERS = (
    "癌",
    "恶性肿瘤",
    "实体瘤",
    "淋巴瘤",
    "白血病",
    "骨髓瘤",
    "骨髓纤维化",
    "骨髓增生异常",
    "肉瘤",
)


@lru_cache(maxsize=1)
def load_treatment_status_rule_config() -> dict[str, Any]:
    with RULE_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    rules = payload.get("rules") or []
    if not rules:
        raise ValueError("Treatment-status rule table is empty")
    return payload


def treatment_status_rules() -> list[dict[str, Any]]:
    return [dict(item) for item in load_treatment_status_rule_config().get("rules") or []]


@lru_cache(maxsize=1)
def load_autoimmune_severity_ratio_config() -> dict[str, Any]:
    with AUTOIMMUNE_RATIO_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def autoimmune_severity_ratio_records() -> list[dict[str, Any]]:
    return [dict(item) for item in load_autoimmune_severity_ratio_config().get("records") or []]


def classify_treatment_status(
    indication_text: str | None,
    *,
    disease_key: str | None = None,
    disease_title: str | None = None,
    market: str = "china",
) -> dict[str, Any]:
    text = str(indication_text or "").strip()
    normalized = _normalize(text)
    config = load_treatment_status_rule_config()
    rules = sorted(config.get("rules") or [], key=lambda item: int(item.get("priority") or 0), reverse=True)
    default_rule_id = str(config.get("defaultRuleId") or "T0")

    # Explicit prior-line and prior-treatment restrictions outrank severity words.
    for rule in rules:
        rule_id = str(rule.get("ruleId") or "")
        if rule_id in {default_rule_id, "T1"}:
            continue
        selection = _match_rule(rule, normalized)
        if selection:
            return selection

    severity_selection = _disease_specific_severity_selection(
        indication_text=text,
        disease_key=disease_key,
        disease_title=disease_title,
        market=market,
    )
    if severity_selection:
        return severity_selection

    for rule in rules:
        if str(rule.get("ruleId") or "") != "T1":
            continue
        selection = _match_rule(rule, normalized)
        if selection:
            return selection

    default_rule = next(
        (item for item in rules if str(item.get("ruleId") or "") == default_rule_id),
        rules[-1],
    )
    return _selection(default_rule, "未识别到明确线次或既往治疗限制，采用默认规则")


def infer_biomarker_ratio(
    indication_text: str | None,
    population_filters: list[Any] | None,
    structured_biomarkers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    text = str(indication_text or "").strip()
    normalized_text = _normalize(text)
    structured_signature = _structured_biomarker_signature(structured_biomarkers)
    has_biomarker_constraint = (
        any(marker in normalized_text for marker in BIOMARKER_MARKERS)
        or bool(SYMBOLIC_BIOMARKER_PATTERN.search(text))
        or bool(structured_signature)
    )

    if has_biomarker_constraint:
        for item in population_filters or []:
            data = item if isinstance(item, dict) else vars(item)
            ratio = data.get("ratio")
            if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 <= float(ratio) <= 1:
                continue
            label = str(data.get("label") or data.get("key") or "").strip()
            if (
                _biomarker_filter_matches(text, label, str(data.get("key") or ""))
                or (structured_signature and _biomarker_filter_matches(structured_signature, label, str(data.get("key") or "")))
            ):
                return {
                    "ratio": float(ratio),
                    "source": f"DrugAlpha疾病知识库：{label}",
                    "requiresHumanReview": False,
                    "sourceDetails": list(data.get("source_details") or data.get("sourceDetails") or []),
                }
        return {
            "ratio": 1.0,
            "source": "适应症含Biomarker限定，但DrugAlpha未找到匹配比例；暂按100%占位",
            "requiresHumanReview": True,
            "sourceDetails": [],
        }

    return {
        "ratio": 1.0,
        "source": "适应症未见明确Biomarker限定，规则预填100%",
        "requiresHumanReview": False,
        "sourceDetails": [],
    }


def _biomarker_filter_matches(indication_text: str, label: str, key: str) -> bool:
    """Match exact and symbolic disease-knowledge biomarker labels safely."""

    normalized_text = _normalize_biomarker(indication_text)
    normalized_label = _normalize_biomarker(label)
    if normalized_label and (
        normalized_label in normalized_text or normalized_text in normalized_label
    ):
        return True

    # A key such as ``pdl1`` gives a safe alias for threshold-form indications
    # (for example, "PD-L1 TPS≥50%"). Compound keys are intentionally excluded
    # here; they must match their full biological signature above.
    normalized_key = _normalize_biomarker(key)
    if normalized_key and all(token not in normalized_key for token in ("hr", "her2", "negative", "positive")):
        return normalized_key in normalized_text
    return False


def _normalize_biomarker(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("三阴性", "tnbc")
    text = (
        text.replace("阳性", "+")
        .replace("阴性", "-")
        .replace("＋", "+")
        .replace("－", "-")
        .replace("−", "-")
    )
    text = text.replace("低表达", "low")
    text = text.replace("过表达", "+").replace("扩增", "+")
    text = re.sub(r"pd\s*[-－]?\s*l\s*1", "pdl1", text)
    text = re.sub(r"her\s*2", "her2", text)
    text = re.sub(r"her2\s*(?:ihc\s*)?(?:2\+|3\+|\+)", "her2+", text)
    text = re.sub(r"(?:msi[-\s]?h(?:/dmmr)?|dmmr)", "msi-hdmmr", text)
    text = re.sub(r"(?:雌激素受体|estrogen\s+receptor|(?<!h)\ber)(?:α|a)?", "hr", text)
    # The disease knowledge base uses ``cldn18-2`` while pipeline text may
    # use CLDN18.2, Claudin 18.2, or Claudin18.2+.  Canonicalising the gene
    # signature before the generic key match preserves the knowledge-base
    # ratio without treating unrelated Claudin targets as equivalent.
    text = re.sub(r"(?:claudin|cldn)\s*18\s*[.．-]?\s*2", "cldn182", text)
    text = re.sub(r"cldn182(?:高表达|\+)", "cldn182+", text)
    return re.sub(r"[\s_，,、。；;：:（）()\[\]【】/\\]", "", text)


def _structured_biomarker_signature(markers: list[dict[str, Any]] | None) -> str:
    """Return a compact, source-backed marker signature for knowledge matching.

    The published indication facets are reviewed structural data.  They avoid
    losing valid ratios when the raw indication uses verbose Chinese wording
    (for example ``人表皮生长因子受体2阳性``) rather than the short label
    stored on the disease page.
    """

    fragments: list[str] = []
    for marker in markers or []:
        if not isinstance(marker, dict):
            continue
        name = str(marker.get("name") or "").strip()
        status = str(marker.get("status") or marker.get("subtype") or "").strip()
        if name:
            fragments.append(f"{name}{status}")
    return " ".join(fragments)


def _selection(rule: dict[str, Any], matched_on: str) -> dict[str, Any]:
    return {
        "ruleId": str(rule.get("ruleId") or "T0"),
        "label": str(rule.get("label") or ""),
        "ratio": float(rule.get("ratio") or 0),
        "description": str(rule.get("description") or ""),
        "matchedOn": matched_on,
        "ruleVersion": str(load_treatment_status_rule_config().get("version") or ""),
        "ratioMode": "fixed_rule",
        "ratioSource": f"T0-T4规则表 v{load_treatment_status_rule_config().get('version') or ''}",
        "ratioSourceUrl": "",
        "requiresEvidenceResearch": False,
    }


def _match_rule(rule: dict[str, Any], normalized_text: str) -> dict[str, Any] | None:
    for keyword in rule.get("keywords") or []:
        normalized_keyword = _normalize(keyword)
        if normalized_keyword and normalized_keyword in normalized_text:
            return _selection(rule, f"关键词匹配：{keyword}")
    for pattern in rule.get("regexPatterns") or []:
        if pattern and re.search(pattern, normalized_text, flags=re.IGNORECASE):
            return _selection(rule, f"条件模式匹配：{pattern}")
    return None


def _disease_specific_severity_selection(
    *,
    indication_text: str,
    disease_key: str | None,
    disease_title: str | None,
    market: str,
) -> dict[str, Any] | None:
    config = load_treatment_status_rule_config()
    severity_rule = config.get("diseaseSpecificSeverityRule") or {}
    normalized_text = _normalize(indication_text)
    if any(_normalize(marker) in normalized_text for marker in ONCOLOGY_MARKERS):
        return None
    matched_keyword = next(
        (
            str(keyword)
            for keyword in severity_rule.get("keywords") or []
            if _normalize(keyword) and _normalize(keyword) in normalized_text
        ),
        None,
    )
    if not matched_keyword:
        return None

    severity_scope = _severity_scope(normalized_text)
    evidence = _find_autoimmune_ratio(
        indication_text=indication_text,
        disease_key=disease_key,
        disease_title=disease_title,
        market=market,
        severity_scope=severity_scope,
    )
    if evidence:
        return {
            "ruleId": str(severity_rule.get("ruleId") or "T1"),
            "label": str(severity_rule.get("label") or "非肿瘤严重度限定（疾病特异检索）"),
            "ratio": float(evidence["ratio"]),
            "description": str(severity_rule.get("description") or ""),
            "matchedOn": f"严重度匹配：{matched_keyword}；证据表：{evidence['recordId']}",
            "ruleVersion": str(config.get("version") or ""),
            "ratioMode": "autoimmune_evidence_table",
            "ratioSource": str(evidence.get("source") or ""),
            "ratioSourceUrl": str(evidence.get("sourceUrl") or ""),
            "requiresEvidenceResearch": False,
        }

    fallback = float(severity_rule.get("fallbackRatio") or 0.3)
    return {
        "ruleId": str(severity_rule.get("ruleId") or "T1"),
        "label": str(severity_rule.get("label") or "非肿瘤严重度限定（疾病特异检索）"),
        "ratio": fallback,
        "description": str(severity_rule.get("description") or ""),
        "matchedOn": f"严重度匹配：{matched_keyword}；自免证据表无精确口径，需联网检索",
        "ruleVersion": str(config.get("version") or ""),
        "ratioMode": "web_research_required",
        "ratioSource": f"联网检索前保守占位 {fallback:.1%}",
        "ratioSourceUrl": "",
        "requiresEvidenceResearch": True,
    }


def _find_autoimmune_ratio(
    *,
    indication_text: str,
    disease_key: str | None,
    disease_title: str | None,
    market: str,
    severity_scope: str,
) -> dict[str, Any] | None:
    haystack = _normalize(" ".join([indication_text, disease_key or "", disease_title or ""]))
    for record in load_autoimmune_severity_ratio_config().get("records") or []:
        if str(record.get("market") or "china") != str(market or "china"):
            continue
        aliases = [record.get("diseaseKey"), *(record.get("aliases") or [])]
        if not any(_normalize(alias) and _normalize(alias) in haystack for alias in aliases):
            continue
        ratio = (record.get("ratios") or {}).get(severity_scope)
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
            continue
        return {
            "recordId": record.get("recordId"),
            "ratio": min(max(float(ratio), 0.0), 1.0),
            "source": "｜".join(
                part
                for part in [
                    str(record.get("sourceTitle") or ""),
                    str(record.get("setting") or ""),
                    str(record.get("denominator") or ""),
                    str(record.get("severityDefinition") or ""),
                ]
                if part
            ),
            "sourceUrl": record.get("sourceUrl"),
        }
    return None


def _severity_scope(normalized_text: str) -> str:
    if any(token in normalized_text for token in (_normalize("中重度"), _normalize("中度至重度"))):
        return "moderate_to_severe"
    if _normalize("轻中度") in normalized_text or _normalize("轻度至中度") in normalized_text:
        return "mild_to_moderate"
    if any(token in normalized_text for token in (_normalize("中高危"), _normalize("中高活动度"))):
        return "moderate_to_high"
    if any(token in normalized_text for token in (_normalize("重度"), _normalize("重症"))):
        return "severe"
    if _normalize("中度") in normalized_text:
        return "moderate"
    if any(token in normalized_text for token in (_normalize("活动性"), _normalize("需要积极治疗"), _normalize("系统治疗候选"), _normalize("适合系统治疗"))):
        return "active"
    return "moderate_to_severe"


def _normalize(value: Any) -> str:
    return re.sub(r"[\s\-_，,、。；;：:（）()\[\]【】]+", "", str(value or "").lower())
