"""Generate indicator-level DeepSeek proposals for editable DCF assumptions."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.ai_assumptions.proposal_engine import generate_indicator_proposals
from packages.source_adapters.io_utils import write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", required=True, help="Path to DCF draft JSON.")
    parser.add_argument("--out", help="Output JSON path under DCF project.")
    parser.add_argument("--fields", help="Comma-separated fields to generate, defaults to core patient-flow fields.")
    parser.add_argument("--max-tasks", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--match-drug", help="Only generate tasks for this exact normalized drug name.")
    parser.add_argument("--match-indication", help="Only generate tasks for this exact normalized indication text.")
    parser.add_argument("--match-market", choices=["china", "us"], help="Only generate tasks for one market.")
    parser.add_argument("--merge-with", help="Merge generated proposals with an existing proposal payload.")
    args = parser.parse_args()

    draft_path = Path(args.draft)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    draft = _filtered_draft(
        draft,
        drug_name=args.match_drug,
        indication_text=args.match_indication,
        market=args.match_market,
    )
    fields = [item.strip() for item in args.fields.split(",") if item.strip()] if args.fields else None
    proposals = generate_indicator_proposals(
        draft,
        fields=fields,
        max_tasks=args.max_tasks,
        batch_size=args.batch_size,
    )
    proposals["sourceDraft"] = str(draft_path)
    if args.merge_with:
        existing = json.loads(Path(args.merge_with).read_text(encoding="utf-8"))
        proposals = _merge_payloads(existing, proposals)

    output_path = Path(args.out) if args.out else Path("data") / "ai_assumptions" / f"{draft_path.stem}_indicator_proposals.json"
    write_json(output_path, proposals)
    print(
        json.dumps(
            {
                "outputPath": str(output_path),
                "taskCount": proposals.get("taskCount"),
                "proposalCount": proposals.get("proposalCount"),
                "missingTaskCount": proposals.get("missingTaskCount") or 0,
                "batchCount": len(proposals.get("rawBatches") or []),
                "batchErrorCount": proposals.get("batchErrorCount") or 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _filtered_draft(
    draft: dict,
    *,
    drug_name: str | None,
    indication_text: str | None,
    market: str | None,
) -> dict:
    if not any((drug_name, indication_text, market)):
        return draft
    filtered = deepcopy(draft)
    products = []
    for product in filtered.get("products") or []:
        if drug_name and _normalize(product.get("drug_name")) != _normalize(drug_name):
            continue
        indications = []
        for indication in product.get("indications") or []:
            if indication_text and _normalize(indication.get("indication_text")) != _normalize(indication_text):
                continue
            if market:
                indication["markets"] = [
                    item for item in indication.get("markets") or [] if item.get("market") == market
                ]
            if indication.get("markets"):
                indications.append(indication)
        if indications:
            product["indications"] = indications
            products.append(product)
    filtered["products"] = products
    return filtered


def _merge_payloads(existing: dict, generated: dict) -> dict:
    merged = dict(existing)
    proposal_by_key = {
        _proposal_key(item): item
        for item in existing.get("proposals") or []
        if _proposal_key(item)
    }
    for item in generated.get("proposals") or []:
        key = _proposal_key(item)
        if key:
            proposal_by_key[key] = item
    merged["proposals"] = list(proposal_by_key.values())
    merged["proposalCount"] = len(merged["proposals"])
    merged["rawBatches"] = [
        *(existing.get("rawBatches") or []),
        *(generated.get("rawBatches") or []),
    ]
    merged["targetedRetry"] = {
        "taskCount": generated.get("taskCount") or 0,
        "proposalCount": generated.get("proposalCount") or 0,
        "missingTaskCount": generated.get("missingTaskCount") or 0,
    }
    merged["batchErrorCount"] = sum(1 for item in merged["rawBatches"] if item.get("error"))
    return merged


def _proposal_key(item: dict) -> tuple[str, str, str, str] | None:
    key = tuple(
        _normalize(item.get(field))
        for field in ("drugName", "indication", "market", "field")
    )
    return key if all(key) else None


def _normalize(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


if __name__ == "__main__":
    raise SystemExit(main())
