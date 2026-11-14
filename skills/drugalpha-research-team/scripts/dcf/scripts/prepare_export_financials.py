"""Prepare company-specific financial assumptions for the web export path."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.financial_data.assumptions import build_assumption_curves


SUPPLEMENT_FIELDS = (
    "cost",
    "sales_expense",
    "admin_expense",
    "income_tax",
    "profit_before_tax",
    "depreciation_amortization",
    "operating_cash_flow",
    "capex",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--valuation", required=True)
    parser.add_argument("--stock-codes", default="")
    parser.add_argument("--company-name", required=True)
    parser.add_argument(
        "--forecast-start-year",
        type=int,
        help="First DCF forecast year; fetch the three complete annual periods immediately before it.",
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    valuation_path = Path(args.valuation)
    valuation = json.loads(valuation_path.read_text(encoding="utf-8"))
    security = _pick_security(args.stock_codes)
    warnings: list[str] = []
    if not security:
        print(json.dumps({"ok": False, "reason": "NO_SUPPORTED_STOCK_CODE", "warnings": warnings}, ensure_ascii=False))
        return

    forecast_start_year = args.forecast_start_year or datetime.now().year
    report_years = list(range(forecast_start_year - 3, forecast_start_year))
    stem = security["code"].replace(".", "")
    # DCF jobs run as the API service user. Keep refreshable financial caches in
    # the shared runtime directory instead of the read-only deployed engine tree.
    financial_dir = Path(os.getenv("DCF_DATA_DIR") or "data") / "financials"
    financial_dir.mkdir(parents=True, exist_ok=True)
    history_path = financial_dir / f"{stem}_history.json"
    ifind_path = financial_dir / f"{stem}_ifind_history.json"
    fallback_path = financial_dir / f"{stem}_akshare_history.json"
    bridge_path = financial_dir / f"{stem}_balance_sheet_bridge.json"

    # QuantAPI is always the first choice. A current AkShare cache must not prevent
    # a later export from upgrading to iFinD when a refresh token becomes available.
    ifind_history = _sanitize_sales_admin_conflicts(_read_history(ifind_path), warnings)
    as_of_date = datetime.now().date().isoformat()
    ifind_token_configured = _can_use_ifind()
    ifind_refresh_needed = (
        not _history_is_current(ifind_history, report_years[-1])
        or not _equity_snapshot_is_current(ifind_history, as_of_date)
        or not _depreciation_field_set_is_current(ifind_history)
        or not _working_capital_field_set_is_current(ifind_history)
    )
    ifind_refresh_attempted = False
    ifind_refresh_succeeded = False
    if ifind_token_configured and ifind_refresh_needed:
        ifind_refresh_attempted = True
        ifind_refresh_succeeded = _run([
            sys.executable,
            "scripts/fetch_ifind_financial_history.py",
            "--code", security["code"],
            "--report-dates", ",".join(f"{item}1231" for item in report_years),
            "--as-of-date", as_of_date,
            "--out", str(ifind_path),
        ], warnings, "iFinD QuantAPI historical financials")
        if ifind_refresh_succeeded:
            ifind_history = _sanitize_sales_admin_conflicts(_read_history(ifind_path), warnings)

    ifind_current = (
        _history_is_current(ifind_history, report_years[-1])
        and _has_financial_data(ifind_history)
    )
    ifind_equity_current = _equity_snapshot_is_current(ifind_history, as_of_date)
    latest_ifind_row = max(ifind_history, key=lambda row: row.get("year") or 0, default={})
    ifind_missing_core_fields = _missing_ifind_core_fields(latest_ifind_row)
    ifind_core_complete = (
        ifind_current
        and ifind_equity_current
        and not ifind_missing_core_fields
    )
    if ifind_refresh_attempted and ifind_refresh_succeeded and ifind_current:
        ifind_status = "live_refresh_succeeded"
    elif ifind_current:
        ifind_status = "current_cache_used"
    elif ifind_refresh_attempted:
        ifind_status = "live_refresh_failed"
    elif not ifind_token_configured:
        ifind_status = "refresh_token_missing"
    else:
        ifind_status = "current_data_unavailable"

    fallback_history = _read_history(fallback_path)
    if not _history_is_current(fallback_history, report_years[-1]):
        legacy_history = _read_history(history_path)
        if _history_is_current(legacy_history, report_years[-1]) and not _is_ifind_history(legacy_history):
            fallback_history = legacy_history

    has_ifind_data = _has_financial_data(ifind_history)
    need_supplement = has_ifind_data and _needs_supplement(ifind_history)
    if (not _history_is_current(ifind_history, report_years[-1]) or need_supplement) and not _history_is_current(fallback_history, report_years[-1]):
        fallback_history = _fetch_akshare_history(
            security=security,
            report_years=report_years,
            history_path=fallback_path,
            bridge_path=bridge_path,
            warnings=warnings,
        )

    if has_ifind_data:
        history = _merge_histories(ifind_history, fallback_history)
    else:
        history = fallback_history

    if history:
        _write_json(history_path, history)

    if not ifind_current:
        warnings.append(
            f"iFinD QuantAPI未取得{report_years[-1]}年营业收入；"
            "如备选数据可用将明确标记为AkShare，否则使用缺失回退规则。"
        )

    if not history:
        print(json.dumps({
            "ok": False,
            "reason": "FINANCIAL_HISTORY_UNAVAILABLE",
            "security": security,
            "ifindStatus": ifind_status,
            "warnings": warnings,
        }, ensure_ascii=False))
        return

    scenario = next((item for item in valuation.get("scenarioResults") or [] if item.get("scenario") == "base"), None)
    if not scenario:
        raise SystemExit("Base valuation scenario is unavailable.")
    assumptions = build_assumption_curves(
        history=history,
        forecast_revenue=scenario.get("companyRevenueCny100m") or [],
        years=valuation.get("forecastYears") or [],
    )
    latest_history = max(history, key=lambda row: row.get("year") or 0)
    assumptions["shareCount100m"] = _safe_float(latest_history.get("total_shares"))
    assumptions["shareCountAsOf"] = latest_history.get("total_shares_as_of")
    assumptions["shareCountSource"] = "ifind_quantapi_http:total_shares"
    assumptions["netCashAdjustmentMode"] = "excluded"
    source = _history_provider(history)
    assumptions["sourceStatus"] = {
        "companyName": args.company_name,
        "securityCode": security["code"],
        "historyProvider": source,
        "historyPath": str(history_path),
        "isFallback": bool(source and not str(source).startswith("ifind_")),
        "ifindStatus": ifind_status,
        "ifindTokenConfigured": ifind_token_configured,
        "ifindRefreshNeeded": ifind_refresh_needed,
        "ifindRefreshAttempted": ifind_refresh_attempted,
        "ifindRefreshSucceeded": ifind_refresh_succeeded,
        "ifindCurrent": ifind_current,
        "ifindEquityCurrent": ifind_equity_current,
        "ifindCoreComplete": ifind_core_complete,
        "ifindMissingCoreFields": ifind_missing_core_fields,
        "ifindRevenueYear": max(
            (row.get("year") or 0 for row in ifind_history if row.get("revenue") is not None),
            default=None,
        ),
        "latestHistoricalYear": max((row.get("year") or 0) for row in history),
        "warnings": warnings,
    }

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(assumptions, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "financialAssumptionsPath": str(output),
        "historyProvider": source,
        "ifindStatus": ifind_status,
        "ifindCurrent": ifind_current,
        "ifindEquityCurrent": ifind_equity_current,
        "ifindCoreComplete": ifind_core_complete,
        "ifindMissingCoreFields": ifind_missing_core_fields,
        "security": security,
        "warningCount": len(warnings),
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))


def _pick_security(stock_codes: str) -> dict[str, str] | None:
    text = stock_codes or ""
    # A/H dual-listed companies must keep the displayed code order.  The
    # previous two-pass matching always selected HK first even when the
    # published company record began with its A-share code (for example,
    # "A: 688062.SH / H: 02493.HK").
    candidates: list[tuple[int, dict[str, str]]] = []
    for match in re.finditer(r"(?<!\d)(\d{1,5})\.HK\b", text, flags=re.IGNORECASE):
        digits = match.group(1).lstrip("0") or "0"
        candidates.append((match.start(), {"code": f"{digits}.HK", "digits": digits.zfill(5), "market": "HK"}))
    for match in re.finditer(r"(?<!\d)(\d{6})\.(SH|SZ|BJ)\b", text, flags=re.IGNORECASE):
        candidates.append((match.start(), {"code": f"{match.group(1)}.{match.group(2).upper()}", "digits": match.group(1), "market": match.group(2).upper()}))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _can_use_ifind() -> bool:
    token = os.getenv("THS_QUANTAPI_REFRESH_TOKEN") or os.getenv("THS_REFRESH_TOKEN") or os.getenv("IFIND_REFRESH_TOKEN")
    return bool(token)


def _missing_ifind_core_fields(row: dict[str, Any]) -> list[str]:
    """Return missing fields needed by the company-level DCF and first-year YoY."""

    missing = []
    if row.get("revenue") is None:
        missing.append("营业收入")
    if row.get("gross_profit") is None and row.get("cost") is None:
        missing.append("毛利/营业成本")
    if row.get("sales_expense") is None and row.get("sales_admin_expense") is None:
        missing.append("销售费用或销售及管理费用合计")
    if row.get("admin_expense") is None and row.get("sales_admin_expense") is None:
        missing.append("管理费用或销售及管理费用合计")
    if row.get("rd_expense") is None:
        missing.append("研发费用")
    if row.get("total_shares") is None:
        missing.append("总股本")
    return missing


def _sanitize_sales_admin_conflicts(
    history: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Reject impossible iFinD sales/admin combinations before building curves.

    Some cached QuantAPI responses have returned ``sales_ad_and_ga`` for both
    ``manage_fee`` and ``sales_ad_and_ga``.  If a positive sales fee is also
    present, the combined line cannot equal the administrative line: it must
    include sales fee on top.  Keeping that value would overstate the
    management-fee base materially, so leave both ambiguous fields empty and
    allow the field-level fallback to supply the separate administrative fee.
    """

    sanitized: list[dict[str, Any]] = []
    for source_row in history:
        row = dict(source_row)
        sales = _safe_float(row.get("sales_expense"))
        admin = _safe_float(row.get("admin_expense"))
        combined = _safe_float(row.get("sales_admin_expense"))
        has_impossible_overlap = (
            sales is not None
            and sales > 0
            and admin is not None
            and admin > 0
            and combined is not None
            and combined > 0
            and abs(combined - admin) <= max(0.01, abs(combined) * 0.001)
        )
        if has_impossible_overlap:
            row["admin_expense"] = None
            row["sales_admin_expense"] = None
            field_sources = dict(row.get("field_sources") or {})
            field_sources.pop("admin_expense", None)
            field_sources.pop("sales_admin_expense", None)
            row["field_sources"] = field_sources
            warnings.append(
                f"iFinD {row.get('year') or ''}年管理费用与销售及管理费用合计出现冲突；"
                "已忽略该两项歧义值并优先使用独立管理费用补充口径。"
            )
        sanitized.append(row)
    return sanitized


def _fetch_akshare_history(
    security: dict[str, str],
    report_years: list[int],
    history_path: Path,
    bridge_path: Path,
    warnings: list[str],
) -> list[dict[str, Any]]:
    years = ",".join(str(item) for item in report_years)
    if security["market"] == "HK":
        command = [
            sys.executable,
            "scripts/fetch_hk_financial_history_akshare.py",
            "--stock", security["digits"],
            "--years", years,
            "--out", str(history_path),
        ]
        label = "HK AkShare financial fallback"
    else:
        command = [
            sys.executable,
            "scripts/fetch_a_financials_akshare.py",
            "--stock", security["digits"],
            "--market", security["market"],
            "--years", years,
            "--out", str(history_path),
            "--balance-out", str(bridge_path),
        ]
        label = "A-share AkShare financial fallback"
    if _run(command, warnings, label):
        return _read_history(history_path)
    return []


def _fetch_balance_sheet_bridge(
    security: dict[str, str],
    report_years: list[int],
    fallback_path: Path,
    bridge_path: Path,
    warnings: list[str],
) -> None:
    if security["market"] == "HK":
        _run([
            sys.executable,
            "scripts/fetch_hk_balance_sheet_bridge_akshare.py",
            "--stock", security["digits"],
            "--year", str(report_years[-1]),
            "--out", str(bridge_path),
        ], warnings, "HK balance-sheet bridge")
        return
    _run([
        sys.executable,
        "scripts/fetch_a_financials_akshare.py",
        "--stock", security["digits"],
        "--market", security["market"],
        "--years", ",".join(str(item) for item in report_years),
        "--out", str(fallback_path),
        "--balance-out", str(bridge_path),
    ], warnings, "A-share balance-sheet bridge")


def _needs_supplement(history: list[dict[str, Any]]) -> bool:
    return any(any(row.get(field) is None for field in SUPPLEMENT_FIELDS) for row in history)


def _has_financial_data(history: list[dict[str, Any]]) -> bool:
    return any(row.get("revenue") is not None for row in history)


def _depreciation_field_set_is_current(history: list[dict[str, Any]]) -> bool:
    """Force one refresh for caches created before alternative D&A fields were added."""
    if not history:
        return False
    return all(
        "depreciation_amortization_alt" in row
        and "depreciation_only" in row
        and "capital_cost_to_daa_reported" in row
        for row in history
    )


def _working_capital_field_set_is_current(history: list[dict[str, Any]]) -> bool:
    """Force one refresh for caches created before iFinD working-capital fields were added."""
    if not history:
        return False
    return all(
        "operating_capital_change" in row and "operating_capital" in row
        for row in history
    )


def _is_ifind_history(history: list[dict[str, Any]]) -> bool:
    return any(str(row.get("source") or "").startswith("ifind_") for row in history)


def _merge_histories(
    primary: list[dict[str, Any]],
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    primary_by_year = {row.get("year"): row for row in primary if row.get("year")}
    fallback_by_year = {row.get("year"): row for row in fallback if row.get("year")}
    rows: list[dict[str, Any]] = []
    for year in sorted(set(primary_by_year) | set(fallback_by_year)):
        primary_row = primary_by_year.get(year) or {}
        fallback_row = fallback_by_year.get(year) or {}
        if not any(primary_row.get(key) is not None for key in ("revenue", "net_income_parent", "rd_expense")):
            if fallback_row:
                rows.append(dict(fallback_row))
            continue
        row = dict(primary_row)
        field_sources = dict(row.get("field_sources") or {})
        primary_source = str(row.get("source") or "ifind_quantapi_http")
        fallback_source = str(fallback_row.get("source") or "")
        combined_sales_admin_locked = (
            row.get("sales_admin_expense") is not None
            and (row.get("sales_expense") is None or row.get("admin_expense") is None)
        )
        supplemented = False
        for key, value in fallback_row.items():
            if key in {"year", "report_date", "code", "source", "currency", "amount_scale", "field_sources"}:
                continue
            if combined_sales_admin_locked and key in {"sales_expense", "admin_expense"}:
                continue
            if row.get(key) is None and value is not None:
                row[key] = value
                field_sources[key] = fallback_source
                supplemented = True
        for key, value in row.items():
            if key not in {"source", "field_sources"} and value is not None and key not in field_sources:
                field_sources[key] = primary_source
        row["source"] = f"{primary_source}+akshare_supplement" if supplemented else primary_source
        row["field_sources"] = field_sources
        _recalculate_ratios(row)
        rows.append(row)
    return sorted(rows, key=lambda item: item.get("year") or 0)


def _recalculate_ratios(row: dict[str, Any]) -> None:
    revenue = _safe_float(row.get("revenue"))
    cost = _safe_float(row.get("cost"))
    row["gross_margin_rate"] = _ratio((revenue - cost) if revenue is not None and cost is not None else None, revenue)
    field_sources = row.setdefault("field_sources", {})
    field_sources["gross_margin_rate"] = "calculated_from_merged_history"
    for amount_key, rate_key in (
        ("sales_expense", "sales_expense_rate"),
        ("rd_expense", "rd_expense_rate"),
        ("admin_expense", "admin_expense_rate"),
        ("sales_admin_expense", "sales_admin_expense_rate"),
        ("depreciation_amortization", "da_rate"),
        ("capex", "capex_rate"),
    ):
        row[rate_key] = _ratio(row.get(amount_key), revenue)
        field_sources[rate_key] = "calculated_from_merged_history"


def _ratio(numerator: Any, denominator: Any) -> float | None:
    num = _safe_float(numerator)
    den = _safe_float(denominator)
    if num is None or den in (None, 0):
        return None
    return round(num / den, 4)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _overlay_ifind_equity_bridge(
    bridge: dict[str, Any] | None,
    latest_history: dict[str, Any],
    security: dict[str, str],
) -> dict[str, Any] | None:
    cash_equivalents = _safe_float(latest_history.get("cash_equivalents"))
    share_count = _safe_float(latest_history.get("total_shares"))
    if cash_equivalents is None and share_count is None:
        return bridge

    had_balance_bridge = isinstance(bridge, dict) and bool(bridge)
    result = dict(bridge or {})
    result.setdefault("stock", security["code"])
    result.setdefault("year", latest_history.get("year"))
    result.setdefault("reportDate", latest_history.get("report_date"))
    field_sources = dict(result.get("fieldSources") or {})
    if cash_equivalents is not None:
        cash_items = dict(result.get("cashItems") or {})
        cash_items["cash_equivalents"] = cash_equivalents
        result["cashItems"] = cash_items
        field_sources["cash_equivalents"] = "ifind_quantapi_http:cce"
    if share_count is not None:
        result["shareCount100m"] = share_count
        result["shareCountAsOf"] = latest_history.get("total_shares_as_of")
        field_sources["share_count"] = "ifind_quantapi_http:total_shares"
    result["fieldSources"] = field_sources

    if cash_equivalents is not None and had_balance_bridge:
        conservative_cash = round(sum(_safe_float(value) or 0 for value in (result.get("cashItems") or {}).values()), 4)
        interest_bearing_debt = round(sum(_safe_float(value) or 0 for value in (result.get("debtItems") or {}).values()), 4)
        conservative_net_cash = round(conservative_cash - interest_bearing_debt, 4)
        quasi_cash = round(sum(_safe_float(value) or 0 for value in (result.get("optionalQuasiCashItems") or {}).values()), 4)
        result["conservativeCashCny100m"] = conservative_cash
        result["interestBearingDebtCny100m"] = interest_bearing_debt
        result["conservativeNetCashCny100m"] = conservative_net_cash
        result["broadNetCashIncludingFvtplCny100m"] = round(conservative_net_cash + quasi_cash, 4)
        result["recommendedNetCashCny100m"] = conservative_net_cash
    elif not had_balance_bridge:
        result["recommendedNetCashCny100m"] = None

    prior_source = str(result.get("source") or "")
    result["source"] = f"ifind_quantapi_http+{prior_source}" if prior_source else "ifind_quantapi_http_partial_equity_bridge"
    prior_note = str(result.get("methodNote") or "").strip()
    overlay_note = "现金及等价物采用iFinD cce；总股本采用估值日total_shares；债务项仍采用资产负债表补充来源。"
    result["methodNote"] = f"{prior_note} {overlay_note}".strip()
    return result


def _run(command: list[str], warnings: list[str], label: str) -> bool:
    try:
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=90)
    except subprocess.TimeoutExpired:
        warnings.append(f"{label} unavailable: request timed out")
        return False
    if result.returncode == 0:
        return True
    detail = (result.stderr or result.stdout or "unknown error").strip().replace("\n", " ")[:240]
    warnings.append(f"{label} unavailable: {detail}")
    return False


def _history_is_current(history: list[dict[str, Any]], expected_year: int) -> bool:
    return any((row.get("year") or 0) >= expected_year and row.get("revenue") is not None for row in history)


def _equity_snapshot_is_current(history: list[dict[str, Any]], as_of_date: str) -> bool:
    return any(row.get("total_shares_as_of") == as_of_date for row in history)


def _history_provider(history: list[dict[str, Any]]) -> str | None:
    sources = {str(row.get("source") or "") for row in history}
    has_ifind = any(source.startswith("ifind_") for source in sources)
    has_akshare = any("akshare" in source for source in sources)
    if has_ifind and has_akshare:
        return "ifind_quantapi_http+akshare_supplement"
    if has_ifind:
        return "ifind_quantapi_http"
    return next((source for source in sources if source), None)


def _read_history(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    if not isinstance(payload, list):
        return []
    return sorted((row for row in payload if isinstance(row, dict)), key=lambda row: row.get("year") or 0)


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


if __name__ == "__main__":
    main()
