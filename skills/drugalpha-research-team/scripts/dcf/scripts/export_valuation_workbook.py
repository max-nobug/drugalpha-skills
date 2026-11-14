"""CLI wrapper for exporting DCF valuation results to Excel."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.excel_export.workbook import export_valuation_workbook


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", required=True, help="Path to draft model JSON.")
    parser.add_argument("--valuation", required=True, help="Path to valuation result JSON.")
    parser.add_argument("--out", help="Output .xlsx path. Defaults to data/exports/<company>_patient_flow_dcf.xlsx.")
    parser.add_argument("--financial-assumptions", help="Optional historical-data-driven assumption curves JSON.")
    parser.add_argument(
        "--ai-assumptions",
        action="append",
        help="Optional DeepSeek assumption JSON; may be repeated to merge financial review and patient proposals.",
    )
    parser.add_argument("--apply-ai-assumptions", action="store_true", help="Write AI proposal values into editable input cells.")
    parser.add_argument("--official-independent", action="store_true")
    args = parser.parse_args()

    draft_path = Path(args.draft)
    valuation_path = Path(args.valuation)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    valuation = json.loads(valuation_path.read_text(encoding="utf-8"))
    financial_assumptions = None
    if args.financial_assumptions:
        financial_assumptions = json.loads(Path(args.financial_assumptions).read_text(encoding="utf-8"))
    ai_assumptions = None
    if args.ai_assumptions:
        ai_assumptions = {}
        merged_proposals = []
        for assumption_path in args.ai_assumptions:
            payload = json.loads(Path(assumption_path).read_text(encoding="utf-8"))
            merged_proposals.extend(payload.get("proposals") or [])
            ai_assumptions.update(payload)
        if merged_proposals:
            ai_assumptions["proposals"] = merged_proposals

    company_name = valuation.get("companyName") or draft.get("company_name") or "dcf"
    output_path = Path(args.out) if args.out else Path("data") / "exports" / f"{_safe_name(company_name)}_patient_flow_dcf.xlsx"
    output = export_valuation_workbook(
        draft,
        valuation,
        output_path,
        financial_assumptions=financial_assumptions,
        ai_assumptions=ai_assumptions,
        apply_ai_assumptions=args.apply_ai_assumptions,
        official_independent=args.official_independent,
    )

    print(
        json.dumps(
            {
                "outputPath": str(output),
                "companyName": company_name,
                "sheetCount": _sheet_count(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _safe_name(value: str) -> str:
    for char in '\\/:*?"<>|':
        value = value.replace(char, "_")
    return value


def _sheet_count(path: Path) -> int:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True)
    return len(workbook.sheetnames)


if __name__ == "__main__":
    main()
