"""Fetch historical financial statement fields from iFinD QuantAPI/THS_BD."""

from __future__ import annotations

import argparse
from datetime import date
import json
import sys
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.financial_data.ifind_http import IFindError, IFindHttpClient

REQUIRED_FIELDS = ("revenue",)

DEFAULT_THS_BD_MAPPING = {
    "description": "Built-in iFinD QuantAPI/THS_BD core financial mapping.",
    "provider": "ifind_quantapi_http",
    "currency": "CNY",
    "amountScale": "raw",
    "fields": {
        "revenue": {
            "label": "营业收入",
            "required": True,
            "indicator": "prime_oi",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "gross_profit": {
            "label": "毛利",
            "required": False,
            "indicator": "gross_profit",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "sales_expense": {
            "label": "销售费用",
            "required": False,
            "indicator": "sales_fee",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "admin_expense": {
            "label": "管理费用",
            "required": False,
            "indicator": "manage_fee",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "sales_admin_expense": {
            "label": "销售及管理费用合计",
            "required": False,
            "indicator": "sales_ad_and_ga",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "net_income_parent": {
            "label": "归母净利润",
            "required": False,
            "indicator": "ni_attr_to_cs",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "rd_expense": {
            "label": "研发费用/研发开支",
            "required": False,
            "indicator": "rad_cost",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "financial_expense": {
            "label": "财务费用",
            "required": False,
            "indicator": "financial_expense",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "depreciation_amortization": {
            "label": "折旧和摊销",
            "required": False,
            "indicator": "p_depreciation_and_amortization",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "depreciation_amortization_alt": {
            "label": "折旧与摊销（备选口径）",
            "required": False,
            "indicator": "deprec_and_amorti",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "depreciation_only": {
            "label": "固定资产折旧（末级备选）",
            "required": False,
            "indicator": "depreciation",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "accumulated_amortization": {
            "label": "累计摊销（审计项）",
            "required": False,
            "indicator": "accum_amortized",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "capital_cost_to_daa_reported": {
            "label": "资本支出/折旧和摊销（iFinD原始值，仅校验）",
            "required": False,
            "indicator": "capital_cost_to_daa",
            "indiparams": ["{report_date}"],
            "valueType": "raw",
        },
        "capex": {
            "label": "资本性支出",
            "required": False,
            "indicator": "capital_cost",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "operating_capital_change": {
            "label": "营运资本变动",
            "required": False,
            "indicator": "operating_capital_change",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "operating_capital": {
            "label": "营运资本",
            "required": False,
            "indicator": "operating_capital",
            "indiparams": ["{report_date}", "OC"],
        },
        "cash_equivalents": {
            "label": "现金及现金等价物",
            "required": False,
            "indicator": "cce",
            "indiparams": ["{report_date}", "100", "OC"],
        },
        "income_tax": {
            "label": "所得税费用",
            "required": False,
            "indicator": "income_tax",
            "indiparams": ["{report_date}", "100", "OC"],
        },
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True, help="iFinD security code, e.g. 2595.HK (no leading zero).")
    parser.add_argument("--report-dates", required=True, help="Comma-separated report dates, e.g. 20211231,20221231.")
    parser.add_argument("--mapping", default="config/ifind_financial_indicators.local.json")
    parser.add_argument("--mode", choices=["basic_data", "date_sequence", "data_pool"], default="basic_data")
    parser.add_argument("--out", required=True)
    parser.add_argument("--allow-empty-required", action="store_true")
    parser.add_argument("--as-of-date", default=date.today().isoformat(), help="Point-in-time date for total_shares, e.g. 2026-08-03.")
    args = parser.parse_args()

    mapping_path = Path(args.mapping)
    mapping = _load_mapping(mapping_path)
    _validate_mapping(mapping, mapping_path)
    client = IFindHttpClient.from_env()
    rows = []
    report_dates = [item.strip() for item in args.report_dates.split(",") if item.strip()]
    if args.mode == "data_pool":
        rows = _fetch_data_pool_rows(client, args.code, mapping, report_dates)
    elif args.mode == "date_sequence":
        rows = _fetch_date_sequence_rows(client, args.code, mapping, report_dates)
    else:
        rows = _fetch_basic_data_rows(client, args.code, mapping, report_dates)

    _attach_total_shares(client, args.code, rows, args.as_of_date)

    if not args.allow_empty_required:
        _fail_if_required_fields_empty(rows)

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"outputPath": str(output), "rowCount": len(rows)}, ensure_ascii=False, indent=2))


def _fetch_basic_data_rows(
    client: IFindHttpClient,
    code: str,
    mapping: dict[str, Any],
    report_dates: list[str],
) -> list[dict[str, Any]]:
    rows = []
    for report_date in report_dates:
        indicators = _build_indicators(mapping, report_date)
        payload = client.basic_data(code, indicators)
        table = ((payload.get("tables") or [{}])[0].get("table") or {})
        rows.append(_build_row(code, mapping, report_date, table))
    return rows


def _fetch_date_sequence_rows(
    client: IFindHttpClient,
    code: str,
    mapping: dict[str, Any],
    report_dates: list[str],
) -> list[dict[str, Any]]:
    if not report_dates:
        return []
    start_date = min(report_dates)
    end_date = max(report_dates)
    indicators = _build_indicators(mapping, "")
    payload = client.date_sequence(code, start_date, end_date, indicators)
    table = ((payload.get("tables") or [{}])[0].get("table") or {})
    rows = []
    dates = _extract_dates(table, report_dates)
    for index, report_date in enumerate(dates):
        value_table = {}
        for field in mapping.get("fields", {}).values():
            indicator = field.get("indicator")
            if indicator:
                value_table[indicator] = _nth(table.get(indicator), index)
        rows.append(_build_row(code, mapping, report_date, value_table))
    return rows


def _fetch_data_pool_rows(
    client: IFindHttpClient,
    code: str,
    mapping: dict[str, Any],
    report_dates: list[str],
) -> list[dict[str, Any]]:
    data_pool = mapping.get("dataPool") or {}
    reportname = data_pool.get("reportname")
    if not reportname:
        raise SystemExit("Missing mapping.dataPool.reportname for data_pool mode.")
    outputpara = _data_pool_outputpara(mapping)
    rows = []
    for report_date in report_dates:
        functionpara = _render_data_pool_functionpara(data_pool.get("functionpara", {}), code, report_date)
        payload = client.data_pool(reportname=reportname, functionpara=functionpara, outputpara=outputpara)
        table = _first_table_row(payload)
        rows.append(_build_row(code, mapping, report_date, table))
    return rows


def _build_row(code: str, mapping: dict[str, Any], report_date: str, table: dict[str, Any]) -> dict[str, Any]:
    amount_scale = mapping.get("amountScale")
    row = {
        "year": int(report_date[:4]),
        "report_date": report_date,
        "code": code,
        "source": "ifind_quantapi_http",
        "currency": mapping.get("currency") or mapping.get("unit"),
        "amount_scale": "CNY_100m" if amount_scale else None,
    }
    for field_key, field in mapping.get("fields", {}).items():
        indicator = field.get("indicator")
        values = table.get(indicator) if indicator else None
        raw_value = _first(values)
        row[field_key] = (
            _normalize_scalar(raw_value)
            if field.get("valueType") == "raw"
            else _normalize_amount(raw_value, amount_scale)
        )
    _select_depreciation_amortization(row, mapping)
    revenue = _safe_float(row.get("revenue"))
    gross_profit = _safe_float(row.get("gross_profit"))
    if row.get("cost") is None and revenue is not None and gross_profit is not None:
        row["cost"] = round(revenue - gross_profit, 6)
        row["cost_calculation"] = "prime_oi - gross_profit"
    capex = _safe_float(row.get("capex"))
    depreciation_amortization = _safe_float(row.get("depreciation_amortization"))
    row["capex_to_da_calculated_rate"] = (
        round(capex / depreciation_amortization, 6)
        if capex is not None and depreciation_amortization not in (None, 0)
        else None
    )
    return row


def _select_depreciation_amortization(row: dict[str, Any], mapping: dict[str, Any]) -> None:
    """Choose one period D&A value without summing overlapping iFinD fields."""
    primary_indicator = (
        ((mapping.get("fields") or {}).get("depreciation_amortization") or {}).get("indicator")
        or "p_depreciation_and_amortization"
    )
    primary_value = row.get("depreciation_amortization")
    row["depreciation_amortization_primary"] = primary_value
    candidates = (
        (primary_indicator, primary_value),
        ("deprec_and_amorti", row.get("depreciation_amortization_alt")),
        ("depreciation", row.get("depreciation_only")),
    )
    row["depreciation_amortization"] = None
    row["depreciation_amortization_indicator_used"] = None
    for indicator, value in candidates:
        if value is None or value == "":
            continue
        row["depreciation_amortization"] = value
        row["depreciation_amortization_indicator_used"] = indicator
        break


def _normalize_scalar(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _normalize_amount(value: Any, amount_scale: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value
    if amount_scale == "raw":
        return round(numeric / 100_000_000, 6)
    if isinstance(amount_scale, (int, float)) and amount_scale:
        return round(numeric / float(amount_scale), 6)
    return numeric


def _attach_total_shares(client: IFindHttpClient, code: str, rows: list[dict[str, Any]], as_of_date: str) -> None:
    if not rows:
        return
    try:
        payload = client.basic_data(code, [{"indicator": "total_shares", "indiparams": [as_of_date]}])
        table = ((payload.get("tables") or [{}])[0].get("table") or {})
        share_count = _normalize_amount(_first(table.get("total_shares")), "raw")
    except (IFindError, requests.RequestException, KeyError, TypeError, ValueError):
        share_count = None
    latest = max(rows, key=lambda row: row.get("year") or 0)
    latest["total_shares"] = share_count
    latest["total_shares_as_of"] = as_of_date


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_indicators(mapping: dict[str, Any], report_date: str) -> list[dict[str, Any]]:
    indicators = []
    for field in mapping.get("fields", {}).values():
        indicator = field.get("indicator")
        if not indicator:
            continue
        params = [str(item).replace("{report_date}", report_date) for item in field.get("indiparams", [])]
        indicators.append({"indicator": indicator, "indiparams": params})
    return indicators


def _validate_mapping(mapping: dict[str, Any], mapping_path: Path) -> None:
    missing = []
    for field_key, field in mapping.get("fields", {}).items():
        if field.get("required") and not field.get("indicator"):
            missing.append(field_key)
    if missing:
        raise SystemExit(
            "Missing required iFinD indicator mapping in "
            f"{mapping_path}: {', '.join(missing)}. "
            "Fill these from iFinD indicator-function query, then deploy as config."
        )


def _load_mapping(mapping_path: Path) -> dict[str, Any]:
    mapping = json.loads(json.dumps(DEFAULT_THS_BD_MAPPING))
    if not mapping_path.exists():
        return mapping
    local = json.loads(mapping_path.read_text(encoding="utf-8"))
    for key, value in local.items():
        if key != "fields":
            mapping[key] = value
    fields = mapping.setdefault("fields", {})
    for field_key, local_field in (local.get("fields") or {}).items():
        if not isinstance(local_field, dict):
            continue
        merged = dict(fields.get(field_key) or {})
        for key, value in local_field.items():
            if key == "indicator" and not value and merged.get("indicator"):
                continue
            merged[key] = value
        fields[field_key] = merged
    return mapping


def _first(values: Any) -> Any:
    if isinstance(values, list):
        return values[0] if values else None
    return values


def _nth(values: Any, index: int) -> Any:
    if isinstance(values, list):
        return values[index] if index < len(values) else None
    return values


def _extract_dates(table: dict[str, Any], requested_dates: list[str]) -> list[str]:
    for key in ("time", "date", "tradeDate", "report_date", "reportDate"):
        values = table.get(key)
        if isinstance(values, list) and values:
            return [str(value).replace("-", "") for value in values]
    return requested_dates


def _fail_if_required_fields_empty(rows: list[dict[str, Any]]) -> None:
    empty = []
    for field_key in REQUIRED_FIELDS:
        if not any(row.get(field_key) not in (None, "") for row in rows):
            empty.append(field_key)
    if empty:
        raise SystemExit(
            "iFinD QuantAPI returned no values for required fields: "
            f"{', '.join(empty)}. "
            "Check the security code, report date, account permission, or local indicator override."
        )


def _data_pool_outputpara(mapping: dict[str, Any]) -> str:
    output_fields = []
    for field in mapping.get("fields", {}).values():
        indicator = field.get("indicator")
        if indicator:
            output_fields.append(_format_data_pool_output_field(indicator))
    extra = (mapping.get("dataPool") or {}).get("outputparaExtra") or []
    output_fields.extend(extra)
    if not output_fields:
        raise SystemExit("No data_pool output fields configured in mapping.")
    return ",".join(output_fields)


def _format_data_pool_output_field(indicator: str) -> str:
    return indicator if ":" in indicator else f"{indicator}:Y"


def _render_data_pool_functionpara(template: dict[str, Any], code: str, report_date: str) -> dict[str, Any]:
    return {key: _render_template_value(value, code, report_date) for key, value in template.items()}


def _render_template_value(value: Any, code: str, report_date: str) -> Any:
    if isinstance(value, str):
        return value.replace("{code}", code).replace("{report_date}", report_date).replace("{year}", report_date[:4])
    if isinstance(value, list):
        return [_render_template_value(item, code, report_date) for item in value]
    if isinstance(value, dict):
        return {key: _render_template_value(item, code, report_date) for key, item in value.items()}
    return value


def _first_table_row(payload: dict[str, Any]) -> dict[str, Any]:
    tables = payload.get("tables") or []
    if not tables:
        return {}
    table = (tables[0] or {}).get("table") or {}
    if isinstance(table, list):
        return table[0] if table else {}
    if isinstance(table, dict):
        first = {}
        for key, value in table.items():
            first[key] = _first(value)
        return first
    return {}


if __name__ == "__main__":
    main()
