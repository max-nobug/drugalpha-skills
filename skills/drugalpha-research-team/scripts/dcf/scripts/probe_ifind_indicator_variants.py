"""Probe iFinD QuantAPI financial indicator variants without printing tokens."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.financial_data.ifind_http import IFindHttpClient, IFindError


BASE_FIELDS = {
    "revenue": [
        "ths_operating_revenue_stock",
        "ths_revenue_stock",
        "ths_business_income_stock",
        "ths_total_operating_income_stock",
    ],
    "cost": [
        "ths_operating_cost_stock",
        "ths_business_cost_stock",
        "ths_total_operating_cost_stock",
    ],
    "gross_profit": [
        "ths_gross_profit_stock",
        "ths_operating_profit_margin_stock",
    ],
    "sales_expense": [
        "ths_selling_expense_stock",
        "ths_sales_expense_stock",
        "ths_sale_expense_stock",
    ],
    "rd_expense": [
        "ths_research_development_expense_stock",
        "ths_rd_expense_stock",
        "ths_research_expense_stock",
        "ths_research_and_development_expenses_stock",
    ],
    "admin_expense": [
        "ths_administrative_expense_stock",
        "ths_admin_expense_stock",
        "ths_general_administrative_expense_stock",
    ],
    "income_tax": [
        "ths_income_tax_expense_stock",
        "ths_income_tax_stock",
    ],
    "profit_before_tax": [
        "ths_total_profit_stock",
        "ths_profit_before_tax_stock",
        "ths_pretax_profit_stock",
    ],
    "da": [
        "ths_depreciation_amortization_stock",
        "ths_depreciation_and_amortization_stock",
        "ths_fixed_assets_depreciation_stock",
    ],
    "capex": [
        "ths_cash_paid_for_fixed_assets_intangible_assets_stock",
        "ths_capital_expenditure_stock",
        "ths_capex_stock",
    ],
}


PREFIX_VARIANTS = ("", "ths_hk_", "ths_hks_", "ths_hshare_")
SUFFIX_VARIANTS = ("", "_hk", "_hks", "_hk_stock")
PARAM_TEMPLATES = (
    ["{report_date}", "100"],
    ["{report_date}", "CNY", "100"],
    ["{year}", "100"],
    ["{report_date}"],
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default="2162.HK")
    parser.add_argument("--report-dates", default="20221231,20231231,20241231")
    parser.add_argument("--fields", default="", help="Comma-separated logical fields to probe. Empty means all.")
    parser.add_argument("--max-candidates", type=int, default=24)
    parser.add_argument("--out", required=True)
    parser.add_argument("--sleep", type=float, default=0.12)
    parser.add_argument("--timeout", type=int, default=8)
    args = parser.parse_args()

    report_dates = [item.strip() for item in args.report_dates.split(",") if item.strip()]
    client = IFindHttpClient.from_env()
    client.timeout = args.timeout
    results: dict[str, Any] = {
        "code": args.code,
        "reportDates": report_dates,
        "note": "No credentials or access tokens are stored in this file.",
        "fields": {},
    }

    selected_fields = {item.strip() for item in args.fields.split(",") if item.strip()}
    for field_key, base_indicators in BASE_FIELDS.items():
        if selected_fields and field_key not in selected_fields:
            continue
        field_results = []
        for indicator in _expand_indicators(base_indicators)[: args.max_candidates]:
            for params_template in PARAM_TEMPLATES:
                params_by_date = []
                non_null_count = 0
                error_samples = []
                for report_date in report_dates:
                    params = _render_params(params_template, report_date)
                    try:
                        payload = client.basic_data(args.code, [{"indicator": indicator, "indiparams": params}])
                        value = _extract_indicator_value(payload, indicator)
                        if value is not None:
                            non_null_count += 1
                        params_by_date.append(
                            {
                                "reportDate": report_date,
                                "params": params,
                                "hasValue": value is not None,
                                "sampleValue": value,
                                "errorCode": payload.get("errorcode"),
                            }
                        )
                    except (IFindError, Exception) as exc:  # noqa: BLE001 - probe should continue.
                        msg = str(exc)
                        error_samples.append(msg[:200])
                        params_by_date.append(
                            {
                                "reportDate": report_date,
                                "params": params,
                                "hasValue": False,
                                "error": msg[:200],
                            }
                        )
                    time.sleep(args.sleep)
                field_results.append(
                    {
                        "indicator": indicator,
                        "paramTemplate": params_template,
                        "nonNullCount": non_null_count,
                        "dates": params_by_date,
                        "errorSamples": error_samples[:2],
                    }
                )
        results["fields"][field_key] = sorted(
            field_results,
            key=lambda item: (item["nonNullCount"], -len(item.get("errorSamples", []))),
            reverse=True,
        )

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    best = {
        field: [
            {
                "indicator": item["indicator"],
                "paramTemplate": item["paramTemplate"],
                "nonNullCount": item["nonNullCount"],
            }
            for item in rows[:3]
        ]
        for field, rows in results["fields"].items()
    }
    print(json.dumps({"outputPath": str(output), "best": best}, ensure_ascii=False, indent=2))


def _expand_indicators(base_indicators: list[str]) -> list[str]:
    seen = set()
    expanded = []
    for indicator in base_indicators:
        core = indicator.removeprefix("ths_").removesuffix("_stock")
        variants = [indicator]
        variants.extend(f"{prefix}{core}_stock" for prefix in PREFIX_VARIANTS if prefix)
        variants.extend(f"ths_{core}{suffix}" for suffix in SUFFIX_VARIANTS if suffix)
        variants.extend(f"{prefix}{core}{suffix}" for prefix in PREFIX_VARIANTS if prefix for suffix in SUFFIX_VARIANTS if suffix)
        for item in variants:
            if item not in seen:
                seen.add(item)
                expanded.append(item)
    return expanded


def _render_params(template: list[str], report_date: str) -> list[str]:
    return [item.replace("{report_date}", report_date).replace("{year}", report_date[:4]) for item in template]


def _extract_indicator_value(payload: dict[str, Any], indicator: str) -> Any:
    tables = payload.get("tables") or []
    if not tables:
        return None
    table = (tables[0] or {}).get("table") or {}
    values = table.get(indicator)
    if isinstance(values, list):
        return next((value for value in values if value not in (None, "")), None)
    return values if values not in (None, "") else None


if __name__ == "__main__":
    main()
