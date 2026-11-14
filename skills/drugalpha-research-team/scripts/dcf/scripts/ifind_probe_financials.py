"""Probe iFinD HTTP access for a security code without printing tokens.

This script is intentionally conservative: it verifies code recognition and
tests configured indicator names. Accurate statement indicator codes should be
managed outside source code when a terminal Super Command export is available.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.financial_data.ifind_http import IFindHttpClient


DEFAULT_INDICATORS = [
    "prime_oi",
    "gross_profit",
    "sales_fee",
    "manage_fee",
    "sales_ad_and_ga",
    "rad_cost",
    "financial_expense",
    "ni_attr_to_cs",
    "p_depreciation_and_amortization",
    "capital_cost",
    "operating_capital_change",
    "operating_capital",
    "cce",
    "income_tax",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True)
    parser.add_argument("--date", default="20241231")
    parser.add_argument("--out")
    args = parser.parse_args()

    client = IFindHttpClient.from_env()
    indicators = [
        {
            "indicator": item,
            "indiparams": [args.date, "OC"] if item == "operating_capital" else [args.date, "100", "OC"],
        }
        for item in DEFAULT_INDICATORS
    ]
    payload = client.basic_data(args.code, indicators)
    table = ((payload.get("tables") or [{}])[0].get("table") or {})
    result = {
        "code": args.code,
        "date": args.date,
        "availableIndicators": {key: value for key, value in table.items()},
        "note": "Null values usually mean indicator parameter mismatch, permission gap, or the need for HK-specific statement indicator codes.",
    }
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"code": args.code, "indicatorCount": len(table), "out": args.out}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
