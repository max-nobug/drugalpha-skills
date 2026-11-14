"""Reusable treatment-mode and pricing defaults for DCF patient-flow models.

The rules are deliberately indication-agnostic: they classify the cost category
and price unit instead of hard-coding individual companies.
All values are editable downstream in the exported workbook.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


COST_CATEGORY_LABELS = {
    "oncology": "肿瘤药物",
    "non_oncology_oral_small_molecule": "非肿瘤口服小分子",
    "non_oncology_injectable_biologic": "非肿瘤注射生物药",
    "car_t_gene_therapy": "CAR-T/基因治疗",
}

# Units are 10k local-currency per patient-month, except the one-time category,
# which is 10k local-currency per treatment. China values are post-NRDL year-2
# reference levels; US values are broad public-price reference levels.
BASE_PRICES = {
    "china": {
        "oncology": 1.0,
        "non_oncology_oral_small_molecule": 0.2,
        "non_oncology_injectable_biologic": 0.3,
        "car_t_gene_therapy": 120.0,
    },
    "us": {
        "oncology": 1.7,
        "non_oncology_oral_small_molecule": 0.6,
        "non_oncology_injectable_biologic": 0.7,
        "car_t_gene_therapy": 58.5,
    },
}

ONCOLOGY_MARKERS = (
    "癌", "瘤", "白血病", "肉瘤", "carcinoma", "cancer", "tumor", "tumour",
    "lymphoma", "leukemia", "myeloma", "nsclc", "sclc",
)
ONE_TIME_MARKERS = (
    "car-t", "cart", "car t", "嵌合抗原受体", "细胞治疗", "基因治疗",
    "gene therapy", "基因编辑", "溶瘤病毒",
)
INJECTABLE_BIOLOGIC_MARKERS = (
    "单抗", "双抗", "三抗", "抗体", "融合蛋白", "生物制剂", "蛋白", "重组",
    "疫苗", "mab", "antibody", "fusion protein", "biologic",
)
SUPPORTIVE_PROCEDURE_MARKERS = (
    "辅助诊断", "全身显像", "清甲", "放射性碘", "手术", "术中", "止血", "创面",
)


def classify_cost_profile(
    *,
    drug_name: str | None,
    target: str | None,
    indication_text: str | None,
) -> dict[str, Any]:
    text = _normalize(" ".join(str(item or "") for item in (drug_name, target, indication_text)))
    if _contains_any(text, ONE_TIME_MARKERS):
        category = "car_t_gene_therapy"
    else:
        oncology = _contains_any(text, ONCOLOGY_MARKERS) and not _contains_any(text, SUPPORTIVE_PROCEDURE_MARKERS)
        if oncology:
            category = "oncology"
        elif _contains_any(text, INJECTABLE_BIOLOGIC_MARKERS):
            category = "non_oncology_injectable_biologic"
        else:
            category = "non_oncology_oral_small_molecule"

    return {
        "drugCostCategory": category,
        "drugCostCategoryLabel": COST_CATEGORY_LABELS[category],
        "priceBasis": "per_treatment" if category == "car_t_gene_therapy" else "per_month",
    }


def default_price_curve(
    *,
    market: str,
    category: str,
    launch_year: int | None,
    years: list[int],
) -> list[float]:
    market_key = "china" if market == "china" else "us"
    base_price = float(BASE_PRICES[market_key].get(category) or BASE_PRICES[market_key]["oncology"])
    if market_key != "china" or category == "car_t_gene_therapy":
        return [round(base_price, 4) for _ in years]
    return [
        round(base_price * _china_price_multiplier(year, launch_year), 4)
        for year in years
    ]


def price_source_note(market: str, category: str) -> str:
    category_label = COST_CATEGORY_LABELS.get(category, category)
    if category == "car_t_gene_therapy":
        if market == "china":
            return f"规则预填：{category_label}国内参考120万元/次；基因治疗须按具体产品复核"
        return f"规则预填：{category_label}海外参考58.5万美元/次；基因治疗须按具体产品复核"
    if market == "china":
        return (
            f"规则预填：{category_label}医保后第2年基准费用；上市首年为基准/60%，"
            "第2-3年保持，第4-5/6-7/第8年起分别累计降价5%/9.75%/14.26%"
        )
    return f"规则预填：{category_label}美国市场月治疗费用参考；需按具体药物净价复核"


def _china_price_multiplier(year: int, launch_year: int | None) -> float:
    if launch_year is None:
        return 1.0
    launch_age = max(year - int(launch_year) + 1, 1)
    if launch_age == 1:
        return 1 / 0.60
    if launch_age <= 3:
        return 1.0
    if launch_age <= 5:
        return 0.95
    if launch_age <= 7:
        return 0.95**2
    return 0.95**3


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(_normalize(marker) in text for marker in markers)
