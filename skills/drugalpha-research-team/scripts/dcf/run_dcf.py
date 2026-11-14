"""Run the Skill's bundled DrugAlpha DCF generation workflow."""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

from packages.dcf_core.independent_revenue import apply_independent_revenue, revenue_input_template
from packages.financial_data.assumptions import build_assumption_curves
from packages.financial_data.mcp_history import build_mcp_curves
from packages.source_adapters.mcp_valuation import build_mcp_valuation_snapshot

ENGINE = Path(__file__).resolve().parent


def run_step(script: str, arguments: list[str], env: dict[str, str]) -> dict:
    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / script), *arguments],
        cwd=ENGINE,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        raise SystemExit(f"{script} failed:\n{result.stderr or result.stdout}")
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--company", help="Company name from authorized DrugAlpha sources")
    source.add_argument("--snapshot", type=Path, help="Native DrugAlpha source snapshot JSON")
    source.add_argument("--valuation-mcp", type=Path, help="Complete DrugAlpha valuation MCP response JSON (or paginated array)")
    parser.add_argument("--research-indications", type=Path, help="Evidence-backed drug/market indications for --valuation-mcp")
    parser.add_argument("--source-root", type=Path, help="Read-only root for local DrugAlpha sources")
    parser.add_argument("--indication-selection", type=Path)
    parser.add_argument("--semantic-merges", type=Path)
    parser.add_argument("--ai-assumptions", type=Path, action="append", default=[])
    parser.add_argument("--apply-ai-assumptions", action="store_true")
    financial = parser.add_mutually_exclusive_group()
    financial.add_argument("--mcp-financials", type=Path, help="Validated financial statement payload from a connected MCP")
    financial.add_argument("--history", type=Path, help="Native historical financial rows JSON")
    financial.add_argument("--financial-assumptions", type=Path, help="Prepared native curves JSON")
    financial.add_argument("--prepare-financials", action="store_true", help="Use native iFinD/fallback retrieval")
    parser.add_argument("--stock-codes", default="", help="Override the draft's code for financial retrieval")
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--out", type=Path)
    output.add_argument("--output-dir", type=Path, help="Export the independent DCF using the standard delivery filename")
    output.add_argument("--revenue-scope-out", type=Path, help="Write the input template for the selected native scope")
    parser.add_argument("--report-date", type=date.fromisoformat, help="Delivery date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--revenue-inputs", type=Path, help="Independently sourced drug revenue assumptions")
    args = parser.parse_args()
    if args.revenue_inputs and (args.revenue_scope_out or args.ai_assumptions or args.apply_ai_assumptions):
        parser.error("--revenue-inputs requires --out or --output-dir and cannot be combined with --ai-assumptions")
    if args.research_indications and not args.valuation_mcp:
        parser.error("--research-indications requires --valuation-mcp")
    if args.valuation_mcp and not (args.revenue_inputs or args.revenue_scope_out):
        parser.error("--valuation-mcp requires independent revenue inputs or --revenue-scope-out")
    if args.valuation_mcp and (args.indication_selection or args.semantic_merges):
        parser.error("--valuation-mcp uses --research-indications for indication selection")
    if args.output_dir and not args.revenue_inputs:
        parser.error("--output-dir requires --revenue-inputs for the independent DCF template")
    if args.report_date and not args.output_dir:
        parser.error("--report-date applies to --output-dir")
    if args.apply_ai_assumptions and not args.ai_assumptions:
        parser.error("--apply-ai-assumptions requires --ai-assumptions")
    if args.company and not (args.source_root or os.getenv("DRUGALPHA_SOURCE_ROOT") or all(
        os.getenv(name) for name in ("DRUGALPHA_PIPELINE_DIR", "DRUGALPHA_DISEASE_KNOWLEDGE_DIR", "DRUGALPHA_VALUATION_DIR")
    )):
        parser.error("--company requires --source-root or configured DrugAlpha source directories")

    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    if args.source_root:
        env["DRUGALPHA_SOURCE_ROOT"] = str(args.source_root.resolve())

    with TemporaryDirectory(prefix="drugalpha-skill-dcf-") as run_dir:
        work = Path(run_dir)
        env["DCF_DATA_DIR"] = run_dir
        if args.snapshot:
            snapshot = args.snapshot.resolve()
        elif args.valuation_mcp:
            try:
                pages = json.loads(args.valuation_mcp.read_text(encoding="utf-8-sig"))
                research = (json.loads(args.research_indications.read_text(encoding="utf-8-sig"))
                            if args.research_indications else [])
                mcp_snapshot = build_mcp_valuation_snapshot(pages, research)
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                parser.error(f"DrugAlpha valuation MCP scope: {exc}")
            snapshot = work / "mcp-snapshot.json"
            snapshot.write_text(json.dumps(mcp_snapshot, ensure_ascii=False), encoding="utf-8")
        else:
            info = run_step("build_source_snapshot.py", ["--company", args.company, "--write-cache"], env)
            snapshot = Path(info["cachePath"])

        draft = work / "draft.json"
        draft_args = ["--snapshot", str(snapshot), "--output", str(draft)]
        for flag, path in (
            ("--valuation-indication-selection", args.indication_selection),
            ("--semantic-indication-merges", args.semantic_merges),
        ):
            if path:
                draft_args.extend([flag, str(path.resolve(strict=True))])
        if args.revenue_inputs or args.revenue_scope_out:
            draft_args.append("--independent-revenue")
        summary = run_step("build_draft_model.py", draft_args, env)
        if args.revenue_inputs or args.revenue_scope_out:
            model = json.loads(draft.read_text(encoding="utf-8"))
            if args.revenue_scope_out:
                args.revenue_scope_out.parent.mkdir(parents=True, exist_ok=True)
                args.revenue_scope_out.write_text(
                    json.dumps(revenue_input_template(model), ensure_ascii=False, indent=2), encoding="utf-8",
                )
                print(json.dumps({"revenueScopePath": str(args.revenue_scope_out.resolve())}, ensure_ascii=False))
                return
            try:
                payload = json.loads(args.revenue_inputs.read_text(encoding="utf-8-sig"))
                apply_independent_revenue(model, payload)
                if args.output_dir and not all(
                    any(value is not None for value in (market.get("patient_basis") or {}).values())
                    for market in payload["markets"] if market.get("status") != "unquantified"
                ):
                    raise ValueError("Standard delivery uses the confirmed template and requires patient_basis for each market")
            except (ValueError, KeyError, TypeError) as exc:
                parser.error(f"Independent revenue inputs: {exc}")
            draft.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
        result = work / "valuation.json"
        run_step("calculate_draft_model.py", ["--draft", str(draft), "--output", str(result)], env)

        curves = args.financial_assumptions
        provider = "native_default_rules"
        warnings = []
        if args.mcp_financials:
            model = json.loads(draft.read_text(encoding="utf-8"))
            valuation = json.loads(result.read_text(encoding="utf-8"))
            payload = json.loads(args.mcp_financials.read_text(encoding="utf-8"))
            prepared = build_mcp_curves(payload, model, valuation, build_assumption_curves)
            status = prepared["sourceStatus"]
            provider = status["historyProvider"]
            warnings = status["missingHistoricalYears"] + status["missingCoreFields"]
            curves = work / "financial.json"
            curves.write_text(json.dumps(prepared, ensure_ascii=False), encoding="utf-8")
        elif args.history:
            curves = work / "financial.json"
            financial_summary = run_step("build_financial_assumptions.py", [
                "--history", str(args.history.resolve(strict=True)), "--valuation", str(result), "--out", str(curves),
            ], env)
            provider = financial_summary["source"]
        elif args.prepare_financials:
            model = json.loads(draft.read_text(encoding="utf-8"))
            curves = work / "financial.json"
            financial_summary = run_step("prepare_export_financials.py", [
                "--company-name", summary["companyName"],
                "--stock-codes", args.stock_codes or model.get("stock_codes") or "",
                "--forecast-start-year", str(model["forecast_years"][0]),
                "--valuation", str(result), "--out", str(curves),
            ], env)
            warnings = financial_summary.get("warnings", [])
            if financial_summary.get("ok"):
                provider = financial_summary.get("historyProvider", "native_financial_data")
            else:
                curves = None
        elif curves:
            provider = "supplied_financial_assumptions"

        output_path = args.out
        if args.output_dir:
            company = re.sub(r'[<>:"/\\|?*]', '', summary["companyName"]).strip()
            report_date = args.report_date or date.today()
            output_path = args.output_dir / f"{company}DCF估值模型-{report_date:%Y%m%d}-DrugAlpha Skill生成.xlsx"
        export_args = ["--draft", str(draft), "--valuation", str(result), "--out", str(output_path.resolve())]
        if args.output_dir:
            export_args.append("--official-independent")
        if curves:
            export_args.extend(["--financial-assumptions", str(curves.resolve(strict=True))])
        for path in args.ai_assumptions:
            export_args.extend(["--ai-assumptions", str(path.resolve(strict=True))])
        if args.apply_ai_assumptions:
            export_args.append("--apply-ai-assumptions")
        export = run_step("export_valuation_workbook.py", export_args, env)

    print(json.dumps({
        **export,
        "products": summary["productCount"],
        "indications": summary["indicationCount"],
        "financialInput": provider,
        "revenueMode": "independent_patient_flow" if args.revenue_inputs else "native_baseline",
        "financialWarningCount": len(warnings),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
