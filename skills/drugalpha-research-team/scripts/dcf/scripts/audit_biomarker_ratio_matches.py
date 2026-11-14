"""Audit DCF biomarker-ratio matching against published disease knowledge.

This is deliberately read-only.  It cross-checks every published pipeline
indication against the disease page matched by the DCF snapshot builder and
reports two review queues:

* ``TEXT_MARKER_UNMATCHED``: a biomarker is explicit in the indication text,
  but no disease-knowledge ratio was found;
* ``FACET_MARKER_NOT_APPLIED``: the structured published indication facets
  contain a biomarker and that disease has ratio filters, but the current DCF
  matcher still returned 100%.

The second queue is evidence for an alias/mapping review, not permission to
apply a ratio automatically.  A disease page can contain several unrelated
biomarker filters, so every candidate retains its structured marker evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.dcf_core.draft_builder import _population_filters
from packages.dcf_core.treatment_status_rules import infer_biomarker_ratio
from packages.source_adapters.config import SourcePaths
from packages.source_adapters.disease_knowledge import DiseaseKnowledgeAdapter
from packages.source_adapters.indication_facets import PublishedIndicationFacetsAdapter
from packages.source_adapters.pipeline import PipelineAdapter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="Optional path for the JSON audit report.")
    args = parser.parse_args()

    report = audit_biomarker_ratio_matches()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered)


def audit_biomarker_ratio_matches(paths: SourcePaths | None = None) -> dict[str, Any]:
    paths = paths or SourcePaths.from_env()
    paths.validate_readonly_sources()
    pipeline = PipelineAdapter(paths.pipeline_dir)
    knowledge = DiseaseKnowledgeAdapter(paths.disease_knowledge_dir)
    facets = PublishedIndicationFacetsAdapter(paths.pipeline_dir)

    summary: Counter[str] = Counter()
    findings: list[dict[str, Any]] = []
    diseases_seen: set[str] = set()
    for company in pipeline.list_companies():
        company_key = str(company.get("companyKey") or "").strip()
        if not company_key:
            continue
        payload = pipeline.get_company_payload(company_key)
        facet_by_row = facets.get_company_facets(company_key)
        for row in payload.get("publishedRows") or []:
            indication_text = str(row.get("indication") or "").strip()
            if not indication_text:
                continue
            match = knowledge.match_indication(indication_text)
            disease_key = str(match.get("diseaseKey") or "")
            if not disease_key:
                summary["unmatched_disease"] += 1
                continue
            disease = knowledge.get_epidemiology(disease_key)
            if not disease:
                summary["missing_epidemiology"] += 1
                continue
            filters = _population_filters(disease)
            ratio_filters = [item for item in filters if isinstance(item.ratio, (int, float))]
            if not ratio_filters:
                continue
            diseases_seen.add(disease_key)
            row_identity = str(row.get("rowIdentity") or "")
            marker_facets = list((facet_by_row.get(row_identity) or {}).get("biomarkers") or [])
            inferred = infer_biomarker_ratio(indication_text, filters, marker_facets)
            summary["reviewed_indications"] += 1
            if float(inferred.get("ratio") or 1) < 1:
                summary["knowledge_ratio_applied"] += 1
                continue

            kind = None
            if inferred.get("requiresHumanReview"):
                kind = "TEXT_MARKER_UNMATCHED"
            elif marker_facets:
                kind = "FACET_MARKER_NOT_APPLIED"
            if not kind:
                continue

            summary[kind] += 1
            findings.append(
                {
                    "kind": kind,
                    "company": company_key,
                    "drugName": row.get("drugName"),
                    "target": row.get("target"),
                    "indication": indication_text,
                    "phase": row.get("phase"),
                    "diseaseKey": disease_key,
                    "structuredBiomarkers": marker_facets,
                    "availableRatioFilters": [
                        {"key": item.key, "label": item.label, "ratio": item.ratio}
                        for item in ratio_filters
                    ],
                    "currentResult": {
                        "ratio": inferred.get("ratio"),
                        "source": inferred.get("source"),
                        "requiresHumanReview": inferred.get("requiresHumanReview"),
                    },
                }
            )

    findings.sort(key=lambda item: (item["kind"], item["diseaseKey"], item["company"], item["drugName"] or ""))
    return {
        "scope": "all published pipeline indications with a matched disease page and ratio-bearing subgroup filters",
        "diseasesReviewed": sorted(diseases_seen),
        "summary": dict(summary),
        "findings": findings,
    }


if __name__ == "__main__":
    main()
