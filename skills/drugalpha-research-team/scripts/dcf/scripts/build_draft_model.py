"""Build an editable patient-flow DCF draft model from a source snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.dcf_core import build_draft_model_from_snapshot
from packages.source_adapters.io_utils import write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, help="Path to source snapshot JSON")
    parser.add_argument("--output", help="Optional output path under DCF project")
    parser.add_argument(
        "--valuation-indication-selection",
        help="Optional DeepSeek web-research selection JSON for valuation assets without mature indications",
    )
    parser.add_argument(
        "--semantic-indication-merges",
        help="Optional DrugAlpha AI semantic indication merge JSON",
    )
    parser.add_argument("--independent-revenue", action="store_true")
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if args.valuation_indication_selection:
        selection_path = Path(args.valuation_indication_selection)
        if selection_path.exists():
            _apply_valuation_indication_selections(
                snapshot,
                json.loads(selection_path.read_text(encoding="utf-8")),
            )
    if args.semantic_indication_merges:
        merge_path = Path(args.semantic_indication_merges)
        if merge_path.exists():
            _apply_semantic_indication_merges(
                snapshot,
                json.loads(merge_path.read_text(encoding="utf-8")),
            )
    draft = build_draft_model_from_snapshot(snapshot, independent_revenue=args.independent_revenue)

    output_path = Path(args.output) if args.output else _default_output_path(draft)
    write_json(output_path, draft)
    print(json.dumps(_summary(draft, output_path), ensure_ascii=False, indent=2))
    return 0


def _apply_valuation_indication_selections(
    snapshot: dict,
    payload: dict,
) -> None:
    selection_by_product_key = {
        str(item.get("productKey") or "").strip().lower(): item
        for item in payload.get("selections") or []
        if item.get("productKey")
    }
    for product in snapshot.get("pipeline", {}).get("products") or []:
        key = str(product.get("productKey") or product.get("drugName") or "").strip().lower()
        selection = selection_by_product_key.get(key)
        if selection:
            product["valuationIndicationSelection"] = selection


def _apply_semantic_indication_merges(snapshot: dict, payload: dict) -> None:
    products_by_key = {
        str(product.get("productKey") or product.get("drugName") or "").strip().lower(): product
        for product in snapshot.get("pipeline", {}).get("products") or []
    }
    for index, group in enumerate(payload.get("mergeGroups") or [], start=1):
        product = products_by_key.get(str(group.get("productKey") or "").strip().lower())
        if not product:
            continue
        member_ids = {
            str(item).strip()
            for item in group.get("memberSourceRowIdentities") or []
            if str(item).strip()
        }
        if len(member_ids) < 2:
            continue
        group_id = str(group.get("mergeGroupId") or f"semantic-{index}")
        for indication in product.get("indications") or []:
            if str(indication.get("sourceRowIdentity") or "") not in member_ids:
                continue
            indication["semanticMergeGroup"] = group_id
            indication["semanticCanonicalIndication"] = group.get("canonicalIndication")
            indication["semanticMergeReason"] = group.get("basisComment")
            indication["semanticMergeConfidence"] = group.get("confidence")


def _default_output_path(draft: dict) -> Path:
    return Path("data") / "models" / f"{draft['source_snapshot_id']}_draft.json"


def _summary(draft: dict, output_path: Path) -> dict:
    products = draft.get("products") or []
    indications = [
        indication
        for product in products
        for indication in product.get("indications") or []
    ]
    warnings = draft.get("warnings") or []
    merged_indication_group_count = sum(
        int(indication.get("merged_trial_count") or 1) > 1
        for indication in indications
    )
    merged_duplicate_row_count = sum(
        max(int(indication.get("merged_trial_count") or 1) - 1, 0)
        for indication in indications
    )
    warning_counts_by_code = {}
    for warning in warnings:
        code = warning.get("code") or "UNCLASSIFIED"
        warning_counts_by_code[code] = warning_counts_by_code.get(code, 0) + 1
    return {
        "outputPath": str(output_path),
        "sourceSnapshotId": draft.get("source_snapshot_id"),
        "companyName": draft.get("company_name"),
        "productCount": len(products),
        "indicationCount": len(indications),
        "patientFlowIndicationCount": sum(
            len(product.get("indications") or [])
            for product in products
            if product.get("modeling_mode") != "simplified_growth"
        ),
        "simplifiedGrowthProductCount": sum(
            product.get("modeling_mode") == "simplified_growth"
            for product in products
        ),
        "mergedIndicationGroupCount": merged_indication_group_count,
        "mergedDuplicateRowCount": merged_duplicate_row_count,
        "scenarioNames": [scenario.get("name") for scenario in draft.get("scenarios") or []],
        "warningCount": len(warnings),
        "warningCountsByCode": warning_counts_by_code,
        "diseaseConfirmationCount": warning_counts_by_code.get("LOW_DISEASE_MATCH_CONFIDENCE", 0),
        "valuationTrackingProductCount": sum(
            "valuation_tracking" in (product.get("source_kinds") or []) for product in products
        ),
        "maturePhaseProductCount": sum(
            "mature_pipeline" in (product.get("source_kinds") or []) for product in products
        ),
        "valuationOnlyProductCount": sum(bool(product.get("valuation_only")) for product in products),
    }


if __name__ == "__main__":
    raise SystemExit(main())
