"""Validate financial statement data supplied by a connected MCP through the agent."""

from datetime import date
import math
import re

AMOUNTS = {
    "revenue", "cost", "gross_profit", "sales_expense", "rd_expense",
    "admin_expense", "sales_admin_expense", "profit_before_tax", "income_tax",
    "financial_expense", "net_income_parent", "depreciation_amortization",
    "capex", "operating_capital", "operating_capital_change",
    "operating_cash_flow", "cash_equivalents",
}
SCALES = {"元": 1e-8, "万元": 1e-4, "亿元": 1.0}
CORE = {"revenue", "cost", "rd_expense", "depreciation_amortization", "capex"}


def _security_code(text):
    matches = re.findall(r"(\d{1,6})\.(HK|SH|SZ|BJ)\b", text.upper())
    return {f"{int(code)}.{market}" for code, market in matches}


def _normalize_expense_scope(row, scope):
    audit = []
    for key in ("admin_expense", "sales_admin_expense"):
        if key not in row:
            continue
        treatment = scope.get(key)
        if treatment not in ("excludes_rd", "includes_rd"):
            raise ValueError(f"{row['year']} {key} needs a verified R&D scope")
        if treatment == "excludes_rd":
            continue
        if "rd_expense" not in row:
            raise ValueError(f"{row['year']} {key} includes R&D but the matching R&D amount is missing")
        reported = row[key]
        rd = row["rd_expense"]
        if rd < 0 or reported < rd:
            raise ValueError(f"{row['year']} {key} cannot reconcile to the reported R&D expense")
        row[key] = round(reported - rd, 10)
        source = row["field_sources"][key]
        row["field_sources"][key] = f"{source}；已扣除其中的研发费用"
        audit.append({"year": row["year"], "field": key, "reported": reported,
                      "rd": rd, "normalized": row[key], "source": source})
    return audit


def _equity_bridge(payload, first_forecast_year):
    bridge = payload.get("equity_bridge")
    if bridge is None:
        return None
    as_of = date.fromisoformat(bridge["as_of"])
    if as_of != date(first_forecast_year - 1, 12, 31):
        raise ValueError("The bridge date must match the opening of the annual DCF forecast")
    values = {}
    for key in ("cash_equivalents", "eligible_deposits", "interest_bearing_debt", "other_debt_like"):
        value = bridge.get(key, 0 if key == "other_debt_like" else None)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid equity bridge amount: {key}")
        values[key] = value * SCALES[payload["unit"]]
    shares = bridge["shares_outstanding"]
    if type(shares) is not int or shares <= 0:
        raise ValueError("Shares outstanding must be a positive count in shares, excluding treasury shares")
    sources = bridge["sources"]
    required = set(values) - ({"other_debt_like"} if "other_debt_like" not in bridge else set())
    if not all(isinstance(sources.get(k), str) and sources[k].strip() for k in required | {"shares_outstanding"}):
        raise ValueError("An equity bridge source is required for each supplied input")
    return {**values, "shares_outstanding": shares, "as_of": as_of.isoformat(), "sources": sources, "note": str(bridge.get("note") or "")}


def build_mcp_curves(payload, model, valuation, build_assumption_curves):
    provider = payload["provider"].strip()
    code = payload["securityCode"].strip()
    codes = _security_code(code)
    if not provider or len(codes) != 1 or not codes <= _security_code(model.get("stock_codes") or ""):
        raise ValueError("Financial provider and security code must match the draft")
    if payload["currency"] != "CNY" or payload["unit"] not in SCALES:
        raise ValueError("Financial statement amounts must use a supported CNY unit")
    if payload.get("scope") != "consolidated_annual":
        raise ValueError("Financial input requires consolidated annual statements")
    years = valuation["forecastYears"]
    expected = set(range(years[0] - 3, years[0]))
    history = []
    seen = set()
    expense_audit = []
    for item in payload["rows"]:
        year = item["year"]
        if type(year) is not int or year not in expected or year in seen:
            raise ValueError(f"Unexpected or duplicate report year: {year}")
        report_date = date.fromisoformat(item["report_date"])
        if report_date.year != year:
            raise ValueError(f"Report date must fall in its stated fiscal year: {report_date}")
        seen.add(year)
        values = item["values"]
        if values.keys() - AMOUNTS:
            raise ValueError(f"Unsupported financial fields: {sorted(values.keys() - AMOUNTS)}")
        sources = item.get("field_sources", {})
        row = {"year": year, "report_date": report_date.isoformat(), "source": provider, "field_sources": {}}
        for key, value in values.items():
            if value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{year} {key} must be a numeric amount or null")
            row[key] = value * SCALES[payload["unit"]]
            row["field_sources"][key] = sources.get(key) or provider
        expense_audit.extend(_normalize_expense_scope(row, item.get("expense_scope", {})))
        if row.get("revenue"):
            if "cost" in row:
                row.setdefault("gross_profit", row["revenue"] - row["cost"])
                row["gross_margin_rate"] = row["gross_profit"] / row["revenue"]
                row["field_sources"].setdefault("gross_profit", f"收入减成本（{row['field_sources']['cost']}）")
            for key, rate in (
                ("sales_expense", "sales_expense_rate"), ("rd_expense", "rd_expense_rate"),
                ("admin_expense", "admin_expense_rate"), ("depreciation_amortization", "da_rate"),
            ):
                if key in row:
                    row[rate] = row[key] / row["revenue"]
        history.append(row)

    history.sort(key=lambda row: row["year"])
    if not history or history[-1]["year"] != years[0] - 1 or history[-1].get("revenue") is None:
        raise ValueError("Latest full-year reported revenue is required for MCP financial input")
    latest = history[-1]
    gaps = sorted(CORE - latest.keys())
    if latest.get("sales_admin_expense") is None:
        gaps.extend(key for key in ("sales_expense", "admin_expense") if key not in latest)
    if not ({"operating_capital", "operating_capital_change"} & latest.keys()):
        gaps.append("operating_capital / operating_capital_change")

    base = next(item for item in valuation["scenarioResults"] if item["scenario"] == "base")
    curves = build_assumption_curves(history=history, forecast_revenue=base["companyRevenueCny100m"], years=years)
    curves["sourceStatus"] = {
        "mcpHistory": True,
        "historyProvider": provider,
        "securityCode": code,
        "latestHistoricalYear": latest["year"],
        "missingHistoricalYears": sorted(expected - seen),
        "missingCoreFields": gaps,
    }
    curves["expenseScopeAudit"] = expense_audit
    bridge = _equity_bridge(payload, years[0])
    if bridge:
        curves.update(
            equityBridge=bridge, netCashAdjustmentMode="included",
            shareCount100m=bridge["shares_outstanding"] / 100_000_000,
            shareCountAsOf=bridge["as_of"],
            shareCountSource=bridge["sources"]["shares_outstanding"],
        )
    else:
        curves["netCashAdjustmentMode"] = "excluded"
    return curves
