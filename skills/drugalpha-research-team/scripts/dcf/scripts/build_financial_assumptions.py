"""Build DCF financial assumption curves from historical financial rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.financial_data.assumptions import build_assumption_curves


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", required=True, help="JSON list of historical financial rows.")
    parser.add_argument("--valuation", required=True, help="Valuation JSON used for forecast revenue years.")
    parser.add_argument("--scenario", default="base")
    parser.add_argument("--balance-sheet-bridge", help="Optional legacy bridge; only its share-count source is retained.")
    parser.add_argument("--out", help="Output assumptions JSON.")
    args = parser.parse_args()

    history_path = Path(args.history)
    valuation_path = Path(args.valuation)
    history = json.loads(history_path.read_text(encoding="utf-8"))
    valuation = json.loads(valuation_path.read_text(encoding="utf-8"))
    scenario = next(item for item in valuation["scenarioResults"] if item["scenario"] == args.scenario)
    assumptions = build_assumption_curves(
        history=history,
        forecast_revenue=scenario["companyRevenueCny100m"],
        years=valuation["forecastYears"],
    )
    if args.balance_sheet_bridge:
        bridge = json.loads(Path(args.balance_sheet_bridge).read_text(encoding="utf-8"))
        assumptions["shareCount100m"] = bridge.get("shareCount100m")
        assumptions["shareCountAsOf"] = bridge.get("shareCountAsOf") or bridge.get("reportDate")
        assumptions["shareCountSource"] = (bridge.get("fieldSources") or {}).get("share_count") or bridge.get("source")
    assumptions["netCashAdjustmentMode"] = "excluded"

    output = Path(args.out) if args.out else history_path.with_name(f"{history_path.stem}_assumptions.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(assumptions, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"outputPath": str(output), "source": assumptions["source"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
