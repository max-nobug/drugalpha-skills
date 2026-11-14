"""Build a DCF-owned source snapshot from read-only DrugAlpha data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.source_adapters import build_company_source_snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True, help="DrugAlpha companyKey, e.g. 百利天恒")
    parser.add_argument("--write-cache", action="store_true", help="Write snapshot to DCF data/cache only")
    args = parser.parse_args()

    snapshot = build_company_source_snapshot(args.company, write_cache=args.write_cache)
    print(json.dumps(_summary(snapshot), ensure_ascii=False, indent=2))
    return 0


def _summary(snapshot: dict) -> dict:
    products = snapshot.get("pipeline", {}).get("products", [])
    indications = sum(len(product.get("indications") or []) for product in products)
    valuation_tracking_products = sum(
        1 for product in products if product.get("isValuationTrackingProduct")
    )
    dcf_source_products = sum(
        1 for product in products if product.get("isDcfSourceProduct")
    )
    return {
        "snapshotId": snapshot.get("snapshotId"),
        "cachePath": snapshot.get("cachePath"),
        "company": snapshot.get("company"),
        "productCount": len(products),
        "indicationCount": indications,
        "valuationTrackingProductCount": valuation_tracking_products,
        "dcfSourceProductCount": dcf_source_products,
        "matchedDiseaseCount": len(snapshot.get("diseaseKnowledge", {}).get("matchedDiseases") or []),
        "warningCount": len(snapshot.get("warnings") or []),
        "firstWarnings": (snapshot.get("warnings") or [])[:5],
    }


if __name__ == "__main__":
    raise SystemExit(main())
