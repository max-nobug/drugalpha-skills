"""Read-only adapter for DrugAlpha disease knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io_utils import parse_count_to_10k, parse_ratio_midpoint, read_json


DISEASE_KEYWORDS = [
    (
        "wet-amd",
        [
            "湿性年龄相关性黄斑变性",
            "新生血管性年龄相关性黄斑变性",
            "湿性AMD",
            "wAMD",
            "nAMD",
        ],
    ),
    ("atopic-dermatitis", ["特应性皮炎", "AD", "中重度特应性皮炎", "青少年中重度特应性皮炎", "儿童特应性皮炎"]),
    ("crswnp", ["慢性鼻窦炎伴有鼻息肉", "慢性鼻窦炎伴鼻息肉", "CRSwNP", "鼻息肉"]),
    ("severe-asthma", ["中重度哮喘", "重症哮喘", "哮喘", "Asthma"]),
    ("bullous-pemphigoid", ["大疱性类天疱疮", "BP"]),
    ("multiple-myeloma", ["多发性骨髓瘤", "复发或难治性多发性骨髓瘤", "MM"]),
    ("urothelial-carcinoma", ["尿路上皮癌", "膀胱癌"]),
    ("ovarian-cancer", ["卵巢癌", "输卵管癌", "原发性腹膜癌", "原发腹膜癌"]),
    ("nsclc", ["NSCLC", "非小细胞肺癌", "非小细胞肺腺癌", "肺腺癌", "肺鳞癌", "非鳞", "鳞状NSCLC", "EGFR突变型III期非小细胞肺癌"]),
    ("sclc", ["SCLC", "小细胞肺癌"]),
    ("breast-cancer", ["乳腺癌", "TNBC", "三阴", "HR+/HER2-", "HER2 阳性乳腺癌", "HER2阳性乳腺癌"]),
    ("esophageal-cancer", ["食管", "食管鳞癌"]),
    ("biliary-tract-cancer", ["胆道癌", "胆管癌", "胆囊癌"]),
    ("nasopharyngeal-carcinoma", ["鼻咽癌", "鼻炎癌"]),
    ("gastric-cancer", ["胃癌", "胃腺癌"]),
    ("gastroesophageal-junction-cancer", ["胃食管结合部", "GEJ"]),
    ("colorectal-cancer", ["结直肠癌", "结肠癌", "直肠癌"]),
    ("hcc", ["肝细胞癌", "肝癌", "HCC"]),
    ("pancreatic-cancer", ["胰腺癌"]),
    ("renal-cell-carcinoma", ["肾细胞癌", "肾癌"]),
    ("cervical-cancer", ["宫颈癌"]),
    ("endometrial-cancer", ["子宫内膜癌"]),
    ("head-and-neck-cancer", ["头颈癌"]),
    ("melanoma", ["黑色素瘤"]),
]


@dataclass(frozen=True)
class DiseaseKnowledgeAdapter:
    disease_knowledge_dir: Path

    @property
    def groups_dir(self) -> Path:
        return self.disease_knowledge_dir / "content" / "knowledge" / "groups"

    def list_diseases(self) -> list[dict[str, Any]]:
        diseases = []
        for path in sorted(self.groups_dir.glob("*.json")):
            payload = read_json(path)
            scope = payload.get("scope") or {}
            meta = payload.get("meta") or {}
            diseases.append(
                {
                    "fileName": path.name,
                    "areaKey": scope.get("areaKey"),
                    "knowledgeGroupKey": scope.get("knowledgeGroupKey"),
                    "title": meta.get("title"),
                    "updatedAt": meta.get("updatedAt"),
                    "status": meta.get("status"),
                }
            )
        return diseases

    def match_indication(self, indication_text: str | None) -> dict[str, Any]:
        text = indication_text or ""
        matches = [
            (disease_key, keyword)
            for disease_key, keywords in DISEASE_KEYWORDS
            for keyword in keywords
            if keyword.lower() in text.lower()
        ]
        if matches:
            # Prefer the most specific phrase.  For example, “胃食管结合部”
            # must not be captured by the shorter nested keyword “食管”.
            disease_key, keyword = max(matches, key=lambda item: len(item[1]))
            return {
                "diseaseKey": disease_key,
                "confidence": 0.85,
                "matchedBy": "longest_keyword",
                "matchedKeyword": keyword,
            }
        return {
            "diseaseKey": None,
            "confidence": 0,
            "matchedBy": None,
            "matchedKeyword": None,
        }

    def get_epidemiology(self, disease_key: str) -> dict[str, Any] | None:
        path = self.groups_dir / f"oncology-solid.{disease_key}.json"
        if not path.exists():
            matches = list(self.groups_dir.glob(f"*.{disease_key}.json"))
            if not matches:
                return None
            path = matches[0]

        payload = read_json(path)
        scope = payload.get("scope") or {}
        meta = payload.get("meta") or {}
        epi = ((payload.get("modules") or {}).get("epidemiology") or {})
        regional_stats = []
        for item in epi.get("regionalStats") or []:
            regional_stats.append(
                {
                    "key": item.get("key"),
                    "label": item.get("label"),
                    "incidenceCountRaw": item.get("incidenceCount"),
                    "incidenceCount10k": parse_count_to_10k(
                        item.get("incidenceCount"), reference_year=2026
                    ),
                    "prevalenceCountRaw": item.get("prevalenceCount"),
                    "prevalenceCount10k": parse_count_to_10k(
                        item.get("prevalenceCount"), reference_year=2026
                    ),
                    "sourceDetails": item.get("sourceDetails") or {},
                }
            )

        subgroups = []
        for item in epi.get("subgroupCounts") or []:
            subgroups.append(
                {
                    "key": item.get("key"),
                    "label": item.get("label"),
                    "countRaw": item.get("count"),
                    "ratioMidpoint": parse_ratio_midpoint(item.get("count")),
                    "note": item.get("note"),
                    "sourceDetails": item.get("sourceDetails") or [],
                }
            )

        return {
            "diseaseKey": disease_key,
            "areaKey": scope.get("areaKey"),
            "title": meta.get("title"),
            "updatedAt": meta.get("updatedAt"),
            "summary": epi.get("summary"),
            "regionalStats": regional_stats,
            "subgroupCounts": subgroups,
        }
