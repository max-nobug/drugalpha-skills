"""Validate an iFinD QuantAPI financial mapping against a security code."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True)
    parser.add_argument("--report-dates", default="20221231,20231231,20241231")
    parser.add_argument("--mapping", default="config/ifind_financial_indicators.local.json")
    parser.add_argument("--mode", choices=["basic_data", "date_sequence", "data_pool"], default="basic_data")
    args = parser.parse_args()

    output = Path("data/financials") / f"{args.code.replace('.', '')}_mapping_validation.json"
    command = [
        sys.executable,
        "scripts/fetch_ifind_financial_history.py",
        "--code",
        args.code,
        "--report-dates",
        args.report_dates,
        "--mapping",
        args.mapping,
        "--mode",
        args.mode,
        "--out",
        str(output),
    ]
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True)
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip())
        raise SystemExit(result.returncode)

    rows = json.loads(output.read_text(encoding="utf-8"))
    fields = _field_coverage(rows)
    print(
        json.dumps(
            {
                "code": args.code,
                "mode": args.mode,
                "outputPath": str(output),
                "rowCount": len(rows),
                "fieldCoverage": fields,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _field_coverage(rows: list[dict]) -> dict[str, dict[str, int]]:
    excluded = {"year", "report_date", "code", "source", "currency", "amount_scale"}
    keys = sorted({key for row in rows for key in row if key not in excluded})
    return {
        key: {
            "nonNullCount": sum(1 for row in rows if row.get(key) not in (None, "")),
            "rowCount": len(rows),
        }
        for key in keys
    }


if __name__ == "__main__":
    main()
