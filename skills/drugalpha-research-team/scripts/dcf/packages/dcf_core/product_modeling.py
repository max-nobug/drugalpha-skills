"""Classify DCF products into patient-flow or simplified revenue models."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


PATIENT_FLOW_MODEL = "patient_flow"
SIMPLIFIED_GROWTH_MODEL = "simplified_growth"


# Global product rules requested by the investment-research workflow.  These
# are deliberately company-agnostic aliases; source company data stays read-only.
SIMPLIFIED_GROWTH_PRODUCT_ALIASES = (
    "艾瑞昔布",
    "硫培非格司亭",
    "阿帕替尼",
    "吡咯替尼",
    "氟唑帕利",
    "普贝希",
    "飞尼妥",
    "依维莫司",
    "瑞复美",
    "来那度胺",
    "维达莎",
    "阿扎胞苷",
    "安加维",
    "地舒单抗",
    "泰菲乐",
    "达拉非尼",
    "迈吉宁",
    "曲美替尼",
    "赞可达",
    "赛瑞替尼",
    "百拓维",
    "倍利妥",
    "凯洛斯",
    "凯泽百",
    "sylvant",
    "siltuximab",
    "维全特",
    "帕唑帕尼",
    "司曲替尼",
)


def classify_product_modeling(
    *,
    drug_name: Any,
    target: Any,
    drug_key: Any = None,
    category: Any = None,
) -> tuple[str, str]:
    """Return the model branch and an auditable Chinese reason."""

    identity = _normalize(" ".join(str(value or "") for value in (drug_name, drug_key)))
    for alias in SIMPLIFIED_GROWTH_PRODUCT_ALIASES:
        if _normalize(alias) in identity:
            return SIMPLIFIED_GROWTH_MODEL, f"指定简化收入产品：{alias}"

    normalized_category = _normalize(category)
    if normalized_category in {"biosimilar", "生物类似药", "生物类似"}:
        return SIMPLIFIED_GROWTH_MODEL, "产品分类：生物类似药"

    if "生物类似" in identity or "biosimilar" in identity:
        return SIMPLIFIED_GROWTH_MODEL, "生物类似药：采用产品级简化收入增长法"

    normalized_target = _normalize(target)
    if normalized_target in {
        "pd1",
        "pdl1",
        "pd1单抗",
        "pdl1单抗",
        "pd1antibody",
        "pdl1antibody",
    }:
        return SIMPLIFIED_GROWTH_MODEL, "PD-1/PD-L1单抗：采用产品级简化收入增长法"

    return PATIENT_FLOW_MODEL, "按适应症患者流测算"


def is_simplified_growth_product(product: dict[str, Any]) -> bool:
    return product.get("modeling_mode") == SIMPLIFIED_GROWTH_MODEL


def _normalize(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)
