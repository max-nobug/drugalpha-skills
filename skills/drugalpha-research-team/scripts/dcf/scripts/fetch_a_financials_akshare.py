"""Fetch A-share annual financial history and equity bridge via AkShare fallback."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import akshare as ak


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", required=True, help="Six-digit A-share code.")
    parser.add_argument("--market", choices=["SH", "SZ", "BJ"], required=True)
    parser.add_argument("--years", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--balance-out")
    args = parser.parse_args()

    years = {int(item.strip()) for item in args.years.split(",") if item.strip()}
    stock = f"{args.market.lower()}{args.stock}"
    income = ak.stock_financial_report_sina(stock=stock, symbol="利润表")
    cash_flow = ak.stock_financial_report_sina(stock=stock, symbol="现金流量表")
    balance_sheet = ak.stock_financial_report_sina(stock=stock, symbol="资产负债表")

    rows = []
    for year in sorted(years):
        report_date = f"{year}1231"
        income_row = _report_row(income, report_date)
        cash_row = _report_row(cash_flow, report_date)
        row = {
            "year": year,
            "report_date": report_date,
            "code": f"{args.stock}.{args.market}",
            "source": "akshare_sina_a_share_fallback",
            "currency": "CNY",
            "amount_scale": "CNY_100m",
            "revenue": _amount(income_row, "营业总收入", "营业收入"),
            "cost": _amount(income_row, "营业成本"),
            "sales_expense": _amount(income_row, "销售费用"),
            "rd_expense": _amount(income_row, "研发费用"),
            "admin_expense": _amount(income_row, "管理费用"),
            "income_tax": _amount(income_row, "所得税费用"),
            "profit_before_tax": _amount(income_row, "利润总额"),
            "depreciation_amortization": _sum_amounts(
                cash_row,
                "固定资产折旧、油气资产折耗、生产性生物资产折旧",
                "无形资产摊销",
                "长期待摊费用摊销",
            ),
            "capex": _amount(cash_row, "购建固定资产、无形资产和其他长期资产所支付的现金"),
            "operating_cash_flow": _amount(cash_row, "经营活动产生的现金流量净额"),
        }
        _add_rates(row)
        rows.append(row)

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    balance_output = None
    if args.balance_out:
        latest_year = max(years)
        balance_row = _report_row(balance_sheet, f"{latest_year}1231")
        bridge = _balance_bridge(args.stock, args.market, latest_year, balance_row)
        balance_output = Path(args.balance_out)
        balance_output.parent.mkdir(parents=True, exist_ok=True)
        balance_output.write_text(json.dumps(bridge, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "outputPath": str(output),
        "balanceOutputPath": str(balance_output) if balance_output else None,
        "rowCount": len(rows),
        "source": "akshare_sina_a_share_fallback",
    }, ensure_ascii=False, indent=2))


def _report_row(frame: Any, report_date: str) -> dict[str, Any]:
    if frame is None or frame.empty or "报告日" not in frame.columns:
        return {}
    rows = frame[frame["报告日"].astype(str).str.replace("-", "", regex=False) == report_date]
    if rows.empty:
        return {}
    return rows.iloc[0].to_dict()


def _amount(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _safe_float(row.get(key))
        if value is not None:
            return round(value / 100_000_000, 6)
    return None


def _sum_amounts(row: dict[str, Any], *keys: str) -> float | None:
    values = [_amount(row, key) for key in keys]
    available = [value for value in values if value is not None]
    return round(sum(available), 6) if available else None


def _add_rates(row: dict[str, Any]) -> None:
    revenue = _safe_float(row.get("revenue"))
    if not revenue:
        return
    row["gross_margin_rate"] = _ratio(revenue - (_safe_float(row.get("cost")) or 0), revenue)
    for amount_key, rate_key in (
        ("sales_expense", "sales_expense_rate"),
        ("rd_expense", "rd_expense_rate"),
        ("admin_expense", "admin_expense_rate"),
        ("depreciation_amortization", "da_rate"),
        ("capex", "capex_rate"),
    ):
        row[rate_key] = _ratio(row.get(amount_key), revenue)


def _balance_bridge(stock: str, market: str, year: int, row: dict[str, Any]) -> dict[str, Any]:
    cash = {"cash_equivalents": _amount(row, "货币资金")}
    debt = {
        "short_term_loans": _amount(row, "短期借款"),
        "current_non_current_debt": _amount(row, "一年内到期的非流动负债"),
        "long_term_loans": _amount(row, "长期借款"),
        "lease_liabilities": _amount(row, "租赁负债"),
    }
    conservative_cash = round(sum(value or 0 for value in cash.values()), 4)
    interest_bearing_debt = round(sum(value or 0 for value in debt.values()), 4)
    net_cash = round(conservative_cash - interest_bearing_debt, 4)
    share_count = _amount(row, "实收资本(或股本)", "股本")
    return {
        "stock": f"{stock}.{market}",
        "year": year,
        "reportDate": f"{year}1231",
        "source": "akshare_sina_a_share_balance_sheet_fallback",
        "currency": "CNY",
        "amountUnit": "CNY_100m",
        "cashItems": cash,
        "debtItems": debt,
        "optionalQuasiCashItems": {},
        "conservativeCashCny100m": conservative_cash,
        "interestBearingDebtCny100m": interest_bearing_debt,
        "conservativeNetCashCny100m": net_cash,
        "broadNetCashIncludingFvtplCny100m": net_cash,
        "recommendedNetCashCny100m": net_cash,
        "shareCount100m": share_count,
        "methodNote": "保守口径=货币资金-短期借款-一年内到期非流动负债-长期借款-租赁负债。",
    }


def _ratio(numerator: Any, denominator: Any) -> float | None:
    num = _safe_float(numerator)
    den = _safe_float(denominator)
    if num is None or den in (None, 0):
        return None
    return round(num / den, 4)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "" or value is False or value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
