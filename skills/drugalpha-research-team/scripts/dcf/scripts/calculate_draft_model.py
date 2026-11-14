"""Calculate MVP sales, cash flow, and DCF outputs for a draft model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.dcf_core import calculate_draft_model
from packages.source_adapters.io_utils import write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", required=True, help="Path to draft model JSON")
    parser.add_argument("--output", help="Optional output path under DCF project")
    args = parser.parse_args()

    draft_path = Path(args.draft)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    valuation = calculate_draft_model(draft)
    output_path = Path(args.output) if args.output else _default_output_path(draft_path)
    write_json(output_path, valuation)
    print(json.dumps(_summary(valuation, output_path), ensure_ascii=False, indent=2))
    return 0


def _default_output_path(draft_path: Path) -> Path:
    return draft_path.with_name(draft_path.stem.replace("_draft", "_valuation") + ".json")


def _summary(valuation: dict, output_path: Path) -> dict:
    scenarios = valuation.get("scenarioResults") or []
    return {
        "outputPath": str(output_path),
        "companyName": valuation.get("companyName"),
        "scenarios": [
            {
                "scenario": item.get("scenario"),
                "enterpriseValueCny100m": item.get("companyDcf", {}).get("enterpriseValueCny100m"),
                "pvForecastFcfCny100m": item.get("companyDcf", {}).get("pvForecastFcfCny100m"),
                "pvTerminalValueCny100m": item.get("companyDcf", {}).get("pvTerminalValueCny100m"),
            }
            for item in scenarios
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
