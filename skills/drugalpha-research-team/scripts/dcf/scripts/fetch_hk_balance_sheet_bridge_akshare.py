"""Fetch HK balance-sheet bridge items for equity value from AkShare fallback."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import akshare as ak


CASH_ITEMS = {
    "cash_equivalents": "现金及等价物",
    "short_term_deposits": "短期存款",
    "restricted_cash": "受限制存款及现金",
}

DEBT_ITEMS = {
    "short_term_loans": "短期贷款",
    "long_term_loans": "长期贷款",
    "current_lease_liabilities": "融资租赁负债(流动)",
    "non_current_lease_liabilities": "融资租赁负债(非流动)",
}

OPTIONAL_QUASI_CASH_ITEMS = {
    "financial_assets_fvtpl_non_current": "指定以公允价值记账之金融资产",
    "financial_assets_fvtpl_current": "指定以公允价值记账之金融资产(流动)",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", default="02162")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--scale", type=float, default=100000000.0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    balance_sheet = ak.stock_financial_hk_report_em(stock=args.stock, symbol="资产负债表", indicator="年度")
    report_date = f"{args.year}-12-31 00:00:00"

    cash = {key: _scale(_statement_value(balance_sheet, report_date, item), args.scale) for key, item in CASH_ITEMS.items()}
    debt = {key: _scale(_statement_value(balance_sheet, report_date, item), args.scale) for key, item in DEBT_ITEMS.items()}
    quasi_cash = {
        key: _scale(_statement_value(balance_sheet, report_date, item), args.scale)
        for key, item in OPTIONAL_QUASI_CASH_ITEMS.items()
    }

    conservative_cash = _sum_values(cash)
    interest_bearing_debt = _sum_values(debt)
    conservative_net_cash = round(conservative_cash - interest_bearing_debt, 4)
    broad_net_cash = round(conservative_net_cash + _sum_values(quasi_cash), 4)

    payload = {
        "stock": f"{args.stock}.HK",
        "year": args.year,
        "reportDate": f"{args.year}1231",
        "source": "akshare_eastmoney_hk_balance_sheet_fallback",
        "currency": "CNY",
        "amountUnit": "CNY_100m",
        "cashItems": cash,
        "debtItems": debt,
        "optionalQuasiCashItems": quasi_cash,
        "conservativeCashCny100m": conservative_cash,
        "interestBearingDebtCny100m": interest_bearing_debt,
        "conservativeNetCashCny100m": conservative_net_cash,
        "broadNetCashIncludingFvtplCny100m": broad_net_cash,
        "recommendedNetCashCny100m": conservative_net_cash,
        "methodNote": "保守口径=现金及等价物+短期存款+受限制现金-短期贷款-长期贷款-融资租赁负债；未计入FVTPL金融资产。",
    }

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"outputPath": str(output), "recommendedNetCashCny100m": conservative_net_cash}, ensure_ascii=False, indent=2))


def _statement_value(df: Any, report_date: str, item_name: str) -> float | None:
    rows = df[(df["REPORT_DATE"] == report_date) & (df["STD_ITEM_NAME"] == item_name)]
    if rows.empty:
        return None
    return _safe_float(rows.iloc[0]["AMOUNT"])


def _scale(value: float | None, scale: float) -> float | None:
    if value is None:
        return None
    return round(value / scale, 6)


def _sum_values(values: dict[str, float | None]) -> float:
    return round(sum(value or 0 for value in values.values()), 4)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
