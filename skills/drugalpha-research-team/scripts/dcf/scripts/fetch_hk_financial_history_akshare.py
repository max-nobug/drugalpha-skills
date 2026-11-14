"""Fetch HK financial history through AkShare as a temporary non-iFinD fallback."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import akshare as ak


INCOME_ITEMS = {
    "revenue": "营业额",
    "cost": "销售成本",
    "sales_expense": "销售及分销费用",
    "rd_expense": "研发费用",
    "admin_expense": "行政开支",
    "income_tax": "税项",
    "profit_before_tax": "除税前溢利",
}

CASH_FLOW_ITEMS = {
    "depreciation_amortization": "加:折旧及摊销",
    "capex_fixed_assets": "购建固定资产",
    "capex_intangible_assets": "购建无形资产及其他资产",
    "operating_cash_flow": "经营业务现金净额",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", default="02162", help="HK code without suffix, e.g. 02162.")
    parser.add_argument("--years", default="2023,2024,2025")
    parser.add_argument("--scale", type=float, default=100000000.0, help="Divide statement amounts by this scale. Default converts raw yuan to 100m unit.")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    years = [int(item.strip()) for item in args.years.split(",") if item.strip()]
    income = ak.stock_financial_hk_report_em(stock=args.stock, symbol="利润表", indicator="年度")
    cash_flow = ak.stock_financial_hk_report_em(stock=args.stock, symbol="现金流量表", indicator="年度")
    analysis = ak.stock_financial_hk_analysis_indicator_em(symbol=args.stock, indicator="年度")

    rows = []
    for year in years:
        report_date = f"{year}-12-31 00:00:00"
        row = {
            "year": year,
            "report_date": f"{year}1231",
            "code": f"{args.stock}.HK",
            "source": "akshare_eastmoney_hk_fallback",
            "currency": _analysis_value(analysis, report_date, "CURRENCY") or "CNY",
            "amount_scale": f"raw_div_{args.scale:g}",
        }
        for key, item_name in INCOME_ITEMS.items():
            row[key] = _scale(_statement_value(income, report_date, item_name), args.scale)
        for key, item_name in CASH_FLOW_ITEMS.items():
            row[key] = _scale(_statement_value(cash_flow, report_date, item_name), args.scale)

        fixed_capex = _safe_float(row.pop("capex_fixed_assets", None)) or 0.0
        intangible_capex = _safe_float(row.pop("capex_intangible_assets", None)) or 0.0
        row["capex"] = fixed_capex + intangible_capex
        row["gross_margin_rate"] = _ratio((_safe_float(row.get("revenue")) or 0) - (_safe_float(row.get("cost")) or 0), row.get("revenue"))
        row["sales_expense_rate"] = _ratio(row.get("sales_expense"), row.get("revenue"))
        row["rd_expense_rate"] = _ratio(row.get("rd_expense"), row.get("revenue"))
        row["admin_expense_rate"] = _ratio(row.get("admin_expense"), row.get("revenue"))
        row["da_rate"] = _ratio(row.get("depreciation_amortization"), row.get("revenue"))
        row["capex_rate"] = _ratio(row.get("capex"), row.get("revenue"))
        rows.append(row)

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"outputPath": str(output), "rowCount": len(rows), "source": "akshare_eastmoney_hk_fallback"}, ensure_ascii=False, indent=2))


def _statement_value(df: Any, report_date: str, item_name: str) -> float | None:
    rows = df[(df["REPORT_DATE"] == report_date) & (df["STD_ITEM_NAME"] == item_name)]
    if rows.empty:
        return None
    return _safe_float(rows.iloc[0]["AMOUNT"])


def _analysis_value(df: Any, report_date: str, column: str) -> Any:
    rows = df[df["REPORT_DATE"] == report_date]
    if rows.empty or column not in rows.columns:
        return None
    value = rows.iloc[0][column]
    return None if value != value else value


def _ratio(numerator: Any, denominator: Any) -> float | None:
    num = _safe_float(numerator)
    den = _safe_float(denominator)
    if num is None or den in (None, 0):
        return None
    return round(num / den, 4)


def _scale(value: float | None, scale: float) -> float | None:
    if value is None:
        return None
    return round(value / scale, 6)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
