"""Export patient-flow DCF draft and valuation payloads to an analyst workbook.

The workbook layout follows the legacy innovative-drug valuation style:

- One summary sheet.
- One worksheet per drug/product.
- Each drug worksheet contains separate indication/market patient-flow blocks.
- Each block calculates patients -> treated patients -> sales -> risk/equity adjusted revenue.
"""

from __future__ import annotations

import math
import re
import unicodedata
from copy import copy
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.comments import Comment
from openpyxl.worksheet.datavalidation import DataValidation

from packages.dcf_core.independent_revenue import ANNUAL_FIELDS
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.styles.numbers import is_date_format
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.packaging.custom import StringProperty
from openpyxl.workbook.properties import CalcProperties

from packages.ai_assumptions.field_policy import AI_PREFILL, get_policy
from packages.dcf_core.success_probability import (
    resolve_market_success_probability,
)
from packages.dcf_core.sales_ramp import (
    DEFAULT_YEARS_TO_PEAK,
    POST_PEAK_DECLINE_RATE,
    POST_PEAK_PLATEAU_YEARS,
)
from packages.dcf_core.treatment_status_rules import autoimmune_severity_ratio_records, treatment_status_rules
from packages.excel_export.maiwei_template import (
    PROTOTYPE_NAMES, TEMPLATE_PATH, format_dcf_note_columns, format_dcf_typography,
    format_saved_dcf_comments, market_notes,
    write_compact_drug_sheet, write_note_columns,
)
from packages.excel_export.model_checks import build_model_check_report, write_model_check_report
from packages.excel_export.scope_check import DEFAULT_SCOPE_GAP, GENERATOR_PROPERTY, generator_record


DARK_FILL = PatternFill("solid", fgColor="A64045")
SECTION_FILL = PatternFill("solid", fgColor="A64045")
SUBSECTION_FILL = PatternFill("solid", fgColor="FFFFFF")
INPUT_FILL = PatternFill("solid", fgColor="FDEEEF")
OUTPUT_FILL = PatternFill("solid", fgColor="FFFFFF")
WARN_FILL = PatternFill("solid", fgColor="FFFFFF")
TOTAL_FILL = PatternFill("solid", fgColor="7D3038")
THIN_BORDER = Border(
    left=Side(style="thin", color="E7D7D8"),
    right=Side(style="thin", color="E7D7D8"),
    top=Side(style="thin", color="E7D7D8"),
    bottom=Side(style="thin", color="E7D7D8"),
)
SUMMARY_HEADER_ROW = 3
DEFAULT_RULE_PEAK_PENETRATION = 0.05
ONE_DECIMAL_NUMBER_FORMAT = "#,##0.0"
ONE_DECIMAL_PERCENT_FORMAT = "0.0%"
AI_MODEL_DISCLAIMER = (
    "当前模型由AI程序生成，不代表DrugAlpha数据库估值，"
    "请根据实际情况自行调整参数确定估值。"
)


def export_valuation_workbook(
    draft: dict[str, Any],
    valuation: dict[str, Any],
    output_path: str | Path,
    financial_assumptions: dict[str, Any] | None = None,
    ai_assumptions: dict[str, Any] | None = None,
    apply_ai_assumptions: bool = False,
    official_independent: bool = False,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    years = valuation.get("forecastYears") or draft.get("forecast_years") or []
    use_template = any(
        (market.get("patient_flow") or {}).get("patient_basis")
        for product in draft.get("products", [])
        for indication in product.get("indications", [])
        for market in indication.get("markets", [])
    )
    wb = load_workbook(TEMPLATE_PATH) if use_template else Workbook()
    if use_template:
        summary = wb["DCF估值"]
        for merged in list(summary.merged_cells.ranges):
            summary.unmerge_cells(str(merged))
        summary.delete_rows(1, summary.max_row)
        wb.remove(wb["里程碑收入"])
    else:
        wb.remove(wb.active)
        summary = wb.create_sheet("DCF估值")
    template_fcf_rows = {}
    scope = draft.get("valuation_scope", [])
    scoped_markets = {(item["product_id"], item["market"]) for item in scope}
    scope_gaps = {}
    for item in draft.get("unquantified_scope", []):
        scope_gaps.setdefault((item["product_id"], item["market"]), []).append(item["reason"])
    sheet_names = _product_market_sheet_names(draft)
    model_check_report = build_model_check_report(draft, valuation, financial_assumptions)

    sensitivity = wb.create_sheet("估值敏感性")
    product_total_rows: list[dict[str, Any]] = []
    product_result_by_id = _product_results_by_id(valuation)
    ai_lookup = _ai_proposal_lookup(ai_assumptions)

    simplified_products = []
    for product in draft.get("products") or []:
        if product.get("modeling_mode") == "simplified_growth":
            simplified_products.append(product)
            continue
        result = product_result_by_id.get(product.get("product_id")) or {}
        for market_key in _product_market_keys(product):
            sheet_name = sheet_names[(product.get("product_id"), market_key)]
            entries = [
                (indication, market)
                for indication in product.get("indications") or []
                if not _is_noncommercial_patient_population(indication)
                for market in indication.get("markets") or []
                if market.get("market") == market_key and _market_is_included(market)
            ]
            key = product.get("product_id"), market_key
            if not entries and key in scoped_markets:
                continue
            conditional_start = conditional_row = None
            has_main_revenue = not entries or any(not market.get("conditional_only") for _, market in entries)
            if any((market.get("patient_flow") or {}).get("patient_basis") for _, market in entries):
                sheet, total_row, conditional_row = write_compact_drug_sheet(wb, sheet_name, product, entries, years, market_key)
                fcf_starts = template_fcf_rows[sheet_name] = []
                if has_main_revenue:
                    start = (conditional_row or total_row) + 2
                    _write_product_fcf_block(sheet, start, years, total_row, result, market_key=market_key)
                    fcf_starts.append(start)
                if conditional_row:
                    conditional_start = (fcf_starts[-1] + 15) if fcf_starts else conditional_row + 2
                    _write_product_fcf_block(sheet, conditional_start, years, conditional_row, result, market_key=market_key)
                    sheet.cell(conditional_start, 1).value = "成功条件下单药简化现金流 / DCF"
                    fcf_starts.append(conditional_start)
            else:
                sheet = wb.create_sheet(sheet_name)
                total_row = _write_product_sheet(
                    sheet, product, result, years, market_filter=market_key,
                    ai_lookup=ai_lookup, apply_ai_assumptions=apply_ai_assumptions,
                )
            product_total_rows.append(
                {
                    "productId": product.get("product_id"),
                    "drugName": _summary_drug_name(product, market_key),
                    "target": product.get("target"),
                    "market": market_key,
                    "marketLabel": _market_summary_label(market_key),
                    "sheetName": sheet_name,
                    "totalRow": total_row if has_main_revenue else None,
                    "conditionalRevenueRow": conditional_row,
                    "conditionalValueRow": conditional_start + 13 if conditional_start else None,
                    "yearStartCol": 2,
                }
            )

    if simplified_products:
        other_sheet = wb.create_sheet("其他药物")
        product_total_rows.extend(
            _write_simplified_products_sheet(
                other_sheet,
                simplified_products,
                product_result_by_id,
                years,
            )
        )

    represented = set()
    for item in product_total_rows:
        key = item["productId"], item["market"]
        represented.add(key)
        if key in scoped_markets:
            item["scopeSource"] = "DrugAlpha 估值跟踪（纳入且对应地区峰值为正）"
        if key in scope_gaps:
            item["scopeGap"] = "；".join(scope_gaps[key])
    for item in scope:
        key = item["product_id"], item["market"]
        if key in represented:
            continue
        region = "中国" if item["market"] == "china" else "海外"
        product_total_rows.append({
            "productId": item["product_id"],
            "drugName": f"{item['drug_name']}（{region}）",
            "target": item.get("target"),
            "market": item["market"],
            "scopeSource": "DrugAlpha 估值跟踪（纳入且对应地区峰值为正）",
            "scopeGap": "；".join(scope_gaps.get(key, [DEFAULT_SCOPE_GAP])),
        })

    milestone_sheet = wb.create_sheet("里程碑收入")
    milestone_total_row = _write_milestone_income_sheet(
        milestone_sheet,
        draft,
        product_total_rows,
        years,
    )

    charts = wb.create_sheet("收入趋势图")
    assumptions_basis = wb.create_sheet("假设依据")
    sources = wb.create_sheet("数据来源与声明")
    summary_layout = _write_summary(
        summary,
        valuation,
        years,
        product_total_rows,
        financial_assumptions,
        ai_assumptions,
        milestone_total_row=milestone_total_row,
        model_check_report=model_check_report,
    )
    if use_template:
        for name in PROTOTYPE_NAMES.values():
            wb.remove(wb[name])
        for name, starts in template_fcf_rows.items():
            sheet = wb[name]
            for start in starts:
                sheet.cell(start + 9, 2, f"='DCF估值'!$C${summary_layout['dcfRows']['wacc']}")
                sheet.cell(start + 10, 2, f"='DCF估值'!$C${summary_layout['dcfRows']['terminal_growth']}")
    _write_sensitivity(sensitivity, summary, summary_layout)
    _write_charts(charts, summary, years, product_total_rows)
    _write_assumption_basis(assumptions_basis, valuation, draft, years, financial_assumptions, ai_assumptions)
    _write_sources(sources, valuation, draft, financial_assumptions, ai_assumptions)
    if (ai_assumptions or {}).get("researchProvider") == "user_agent":
        sources.append(["本次预填执行方", "用户自己的agent；平台未调用AI API。建议和来源需人工复核。"])
    # Keep source financials at the end of the workbook.  This is an audit
    # sheet only: it exposes the exact historical inputs used to form the DCF
    # operating assumptions without adding another editable model input area.
    historical_financials = wb.create_sheet("历史财务数据")
    _write_historical_financials_sheet(
        historical_financials,
        valuation,
        years,
        financial_assumptions,
    )

    _write_financial_reconciliation(historical_financials, financial_assumptions or {})
    independent_product_sheets = (
        {item["sheetName"] for item in product_total_rows if item.get("sheetName")}
        if draft.get("revenue_mode") == "independent_patient_flow" else set()
    )
    for sheet in wb.worksheets:
        sheet.sheet_view.showGridLines = False
        if sheet.title in template_fcf_rows:
            _unlock_used_range(sheet)
            continue
        _format_used_range(sheet)
        if sheet.title in independent_product_sheets:
            sheet.column_dimensions[get_column_letter(len(years) + 2)].width = 30.6
            sheet.column_dimensions[get_column_letter(len(years) + 3)].width = 60.6
        _apply_one_decimal_display(sheet)
        _unlock_used_range(sheet)
        sheet.protection.sheet = False
    _separate_summary_notes(summary, len(years) + 3, summary_layout["dcfRows"])
    _finalize_sensitivity_sheet(sensitivity)
    if (financial_assumptions or {}).get("equityBridge"):
        for row in range(37, 42):
            historical_financials.cell(row, 2).number_format = "0.0000"
        historical_financials["B42"].number_format = "#,##0"
        for key in ("enterprise_value", "net_cash", "equity_value", "share_count", "value_per_share"):
            summary.cell(summary_layout["dcfRows"][key], 3).number_format = (
                "0.0000" if key == "share_count" else "0.00"
            )
        for coordinate in ("D7", "H6", "B23", "C23", "D23", "B25", "C25", "D25"):
            sensitivity[coordinate].number_format = "0.00"
    for row in range(46, 46 + len((financial_assumptions or {}).get("expenseScopeAudit") or [])):
        _number_format_range(historical_financials, row, 3, row, 5, "0.00000")

    if wb.calculation is None:
        wb.calculation = CalcProperties()
    wb.calculation.calcMode = "auto"
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    format_dcf_note_columns(wb, draft.get("company_key") or valuation.get("companyKey"))
    format_dcf_typography(wb)
    if official_independent:
        if draft.get("revenue_mode") != "independent_patient_flow" or not scope:
            raise ValueError("Official independent DCF requires a source-derived valuation scope")
        provenance = generator_record({"company": draft["company_name"], "valuation_scope": scope})
        if GENERATOR_PROPERTY in wb.custom_doc_props.names:
            del wb.custom_doc_props[GENERATOR_PROPERTY]
        wb.custom_doc_props.append(StringProperty(name=GENERATOR_PROPERTY, value=provenance))
    wb.save(output)
    format_saved_dcf_comments(output)
    write_model_check_report(output.with_suffix(".checks.json"), model_check_report)
    return output


def _write_summary(
    sheet,
    valuation: dict[str, Any],
    years: list[int],
    product_total_rows: list[dict[str, Any]],
    financial_assumptions: dict[str, Any] | None = None,
    ai_assumptions: dict[str, Any] | None = None,
    *,
    milestone_total_row: int | None = None,
    model_check_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    year_start_col = 3
    last_year_col = year_start_col + len(years) - 1
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(last_year_col + 2, 10))
    sheet["A1"] = f"{valuation.get('companyName') or ''}-创新药DCF估值模型"
    sheet["A1"].fill = DARK_FILL
    sheet["A1"].font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True, size=16)
    sheet["A1"].alignment = Alignment(horizontal="left", vertical="center")

    header_row = SUMMARY_HEADER_ROW
    headers = ["药物", "靶点", *[f"{year}E" for year in years], "说明", "来源／依据"]
    if any(item.get("conditionalValueRow") for item in product_total_rows):
        headers.append("成功条件下单药DCF（亿元）")
    _write_row(sheet, header_row, 1, headers, header=True)

    revenue_row = header_row + 1
    growth_row = header_row + 2
    first_product_row = header_row + 3
    milestone_revenue_row = first_product_row + len(product_total_rows)
    company_revenue_rows = []
    for offset, item in enumerate(product_total_rows):
        row = first_product_row + offset
        sheet.cell(row, 1, item.get("drugName"))
        target = item.get("target") or ""
        conditional_only = item.get("conditionalRevenueRow") and not item.get("totalRow")
        if conditional_only:
            target = f"{target}（成功条件）"
        sheet.cell(row, 2, target)
        notes = []
        if conditional_only:
            notes.append("年度数值为成功条件下归属收入，未做概率加权")
        if item.get("conditionalValueRow"):
            notes.append("部分适应症成功概率待定" if item.get("totalRow") else "风险调整后贡献待定")
            notes.append("成功条件下单药DCF单列，未并入公司估值")
        if item.get("scopeGap"):
            notes.append(f"已入选，存在未量化范围。{item['scopeGap']}")
        if notes or item.get("scopeSource"):
            write_note_columns(sheet, row, last_year_col + 1, "。".join(notes), item.get("scopeSource") or "")
        if item.get("totalRow"):
            company_revenue_rows.append(row)
        display_row = item.get("totalRow") or item.get("conditionalRevenueRow")
        if display_row:
            for year_idx, _year in enumerate(years):
                col = year_start_col + year_idx
                source_col = get_column_letter(int(item.get("yearStartCol") or 3) + year_idx)
                sheet_name = item["sheetName"].replace("'", "''")
                sheet.cell(row, col, f"='{sheet_name}'!{source_col}{display_row}")
                sheet.cell(row, col).font = Font(name="SimSun", color="000000", size=11)
        if item.get("conditionalValueRow"):
            sheet_name = item["sheetName"].replace("'", "''")
            cell = sheet.cell(row, last_year_col + 3, f"='{sheet_name}'!B{item['conditionalValueRow']}")
            cell.number_format = "#,##0.0"

    sheet.cell(milestone_revenue_row, 1, "里程碑收入")
    sheet.cell(milestone_revenue_row, 2, "里程碑合计")
    sheet.cell(revenue_row, 1, "总收入（亿元）")
    sheet.cell(revenue_row, 2, "收入合计")
    if any(item.get("scopeGap") or item.get("conditionalValueRow") for item in product_total_rows):
        write_note_columns(sheet, revenue_row, last_year_col + 1, "部分范围DCF，仅汇总已量化且计入主情景的归属收入。")
    sheet.cell(growth_row, 1, "yoy")
    sheet.cell(growth_row, 2, "同比增长")
    ifind_base = _ifind_previous_year_revenue(financial_assumptions, years[0] if years else None)
    source_status = (financial_assumptions or {}).get("sourceStatus") or {}
    is_mcp = source_status.get("mcpHistory")
    latest = (financial_assumptions or {}).get("latestHistorical") or {}
    yoy_provider = (
        (latest.get("field_sources") or {}).get("revenue") or source_status.get("historyProvider")
        if is_mcp else "iFinD QuantAPI / THS_BD prime_oi"
    )
    yoy_source_col = last_year_col + 2
    if ifind_base is not None:
        base_year, base_revenue = ifind_base
        base_name = "MCP_PREVIOUS_YEAR_REVENUE_CNY_100M" if is_mcp else "IFIND_PREVIOUS_YEAR_REVENUE_CNY_100M"
        sheet.parent.defined_names.add(
            DefinedName(
                base_name,
                attr_text=str(base_revenue),
                comment=f"{yoy_provider}，{base_year}年，亿元",
            )
        )
    for col in range(year_start_col, last_year_col + 1):
        letter = get_column_letter(col)
        milestone_source_col = get_column_letter(3 + (col - year_start_col))
        if milestone_total_row is not None:
            sheet.cell(
                milestone_revenue_row,
                col,
                f"='里程碑收入'!{milestone_source_col}{milestone_total_row}",
            )
        else:
            sheet.cell(milestone_revenue_row, col, 0)
        if len(company_revenue_rows) == len(product_total_rows) and product_total_rows:
            refs = f"{letter}{first_product_row}:{letter}{milestone_revenue_row}"
        else:
            refs = ",".join(f"{letter}{row}" for row in [*company_revenue_rows, milestone_revenue_row])
        sheet.cell(revenue_row, col, f"=SUM({refs})")
        if col == year_start_col:
            if ifind_base is None:
                sheet.cell(growth_row, col, "")
                sheet.cell(growth_row, yoy_source_col, "iFinD上年收入未取到，首年留空")
                sheet.cell(growth_row, col).comment = Comment(
                    "未取得首个预测年前一年的iFinD营业收入，首年YoY不使用模板值或AkShare值替代。",
                    "DrugAlpha DCF",
                )
            else:
                sheet.cell(
                    growth_row,
                    col,
                    (
                        f'=IF({base_name}>0,'
                        f'{letter}{revenue_row}/{base_name}-1,"")'
                    ),
                )
                base_year, base_revenue = ifind_base
                sheet.cell(growth_row, col).comment = Comment(
                    (
                        f"首个预测年YoY基期采用{yoy_provider}："
                        f"{base_year}年营业收入{base_revenue:.1f}亿元。"
                    ),
                    "DrugAlpha DCF",
                )
                sheet.cell(growth_row, yoy_source_col, f"{yoy_provider if is_mcp else 'iFinD'} {base_year}年营业收入")
        else:
            previous_letter = get_column_letter(col - 1)
            sheet.cell(growth_row, col, f'=IF({previous_letter}{revenue_row}>0,{letter}{revenue_row}/{previous_letter}{revenue_row}-1,"")')
    _style_output_rows(
        sheet,
        [revenue_row, milestone_revenue_row],
        1,
        last_year_col,
    )
    if product_total_rows:
        for row in (revenue_row, growth_row, milestone_revenue_row):
            for col in range(1, last_year_col + 1):
                sheet.cell(row, col).font = copy(sheet.cell(first_product_row, col).font)
    _number_format_range(sheet, revenue_row, year_start_col, revenue_row, last_year_col, "#,##0.0")
    _number_format_range(sheet, milestone_revenue_row, year_start_col, milestone_revenue_row, last_year_col, "#,##0.0")
    _number_format_range(sheet, growth_row, year_start_col, growth_row, last_year_col, "0.0%")

    dcf_row = milestone_revenue_row + 2
    dcf_rows = _write_dcf_block(
        sheet,
        dcf_row,
        years,
        revenue_row,
        valuation,
        financial_assumptions,
        ai_assumptions,
    )
    model_check_row = max(dcf_rows.values()) + 2
    _write_model_check_status(sheet, model_check_row, model_check_report or {})
    disclaimer_row = model_check_row + 2
    _write_ai_model_disclaimer(
        sheet,
        disclaimer_row,
        AI_MODEL_DISCLAIMER,
        last_col=last_year_col + 2,
    )
    return {
        "years": years,
        "yearStartCol": year_start_col,
        "lastYearCol": last_year_col,
        "revenueRow": revenue_row,
        "milestoneRevenueRow": milestone_revenue_row,
        "dcfRows": dcf_rows,
        "modelCheckRow": model_check_row,
        "disclaimerRow": disclaimer_row,
    }


def _write_model_check_status(sheet, row: int, report: dict[str, Any]) -> None:
    status = str(report.get("status") or "review")
    display_status = str(report.get("displayStatus") or "需复核")
    sheet.cell(row, 1, "模型检查")
    sheet.cell(row, 2, display_status)
    sheet.cell(row, 1).font = Font(name="SimSun", color="000000", bold=True, size=11)
    sheet.cell(row, 2).font = Font(
        name="SimSun",
        color="000000" if status == "pass" else "A64045",
        bold=True,
        size=11,
    )
    sheet.cell(row, 2).fill = OUTPUT_FILL if status == "pass" else INPUT_FILL
    sheet.cell(row, 1).border = THIN_BORDER
    sheet.cell(row, 2).border = THIN_BORDER
    issues = [
        item for item in report.get("checks") or []
        if item.get("status") in {"warning", "fail"}
    ]
    if issues:
        issue_lines = [
            f"{item.get('scope') or '公司层面'}：{item.get('message') or '需复核'}"
            for item in issues[:8]
        ]
        if len(issues) > len(issue_lines):
            issue_lines.append(f"另有{len(issues) - len(issue_lines)}项，详见本次生成记录。")
        sheet.cell(row, 2).comment = Comment("\n".join(issue_lines), "DrugAlpha DCF")
    else:
        sheet.cell(row, 2).comment = Comment(
            f"本次共完成{report.get('checkCount') or 0}项生成时勾稽与合理性检查。",
            "DrugAlpha DCF",
        )


def _write_ai_model_disclaimer(sheet, row: int, disclaimer: str, last_col: int) -> None:
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
    cell = sheet.cell(row, 1, disclaimer or AI_MODEL_DISCLAIMER)
    cell.font = Font(name="SimSun", color="A64045", size=10)
    cell.fill = OUTPUT_FILL
    cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=False)
    cell.border = Border(top=Side(style="thin", color="E7D7D8"))
    sheet.row_dimensions[row].height = 22


def _ifind_previous_year_revenue(
    financial_assumptions: dict[str, Any] | None,
    first_forecast_year: int | None,
) -> tuple[int, float] | None:
    if not isinstance(first_forecast_year, int):
        return None
    latest = (financial_assumptions or {}).get("latestHistorical") or {}
    source = (latest.get("field_sources") or {}).get("revenue") or latest.get("source")
    year = latest.get("year")
    revenue = latest.get("revenue")
    if (
        (not ((financial_assumptions or {}).get("sourceStatus") or {}).get("mcpHistory")
         and not str(source or "").startswith("ifind_quantapi_http"))
        or year != first_forecast_year - 1
        or not isinstance(revenue, (int, float))
        or isinstance(revenue, bool)
        or revenue <= 0
    ):
        return None
    return int(year), float(revenue)


def _write_product_sheet(
    sheet,
    product: dict[str, Any],
    product_result: dict[str, Any],
    years: list[int],
    *,
    market_filter: str,
    ai_lookup: dict[tuple[str, str, str, str], dict[str, Any]] | None = None,
    apply_ai_assumptions: bool = False,
) -> int:
    year_start_col = 2
    last_year_col = year_start_col + len(years) - 1
    description_col = last_year_col + 1
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=description_col)
    title_market = f" {_market_summary_label(market_filter)}" if _should_label_market(product, market_filter) else ""
    sheet["A1"] = f"{product.get('drug_name') or ''}{title_market} | {product.get('target') or ''}"
    sheet["A1"].fill = DARK_FILL
    sheet["A1"].font = Font(color="FFFFFF", bold=True, size=15)

    _write_row(sheet, 3, 1, ["字段", *[f"{year}E" for year in years], "说明"], header=True)
    current_row = 4
    adjusted_output_rows = []
    unadjusted_output_rows: list[dict[str, int | str]] = []
    market_entries = [
        (indication, market)
        for indication in product.get("indications") or []
        if not _is_noncommercial_patient_population(indication)
        for market in indication.get("markets") or []
        if market.get("market") == market_filter and _market_is_included(market)
    ]
    valuation_market_lookup = _valuation_market_result_lookup(product_result)
    market_entry_count = len(market_entries)
    independent = bool(market_entries) and all(market.get("revenue_input_sources") for _, market in market_entries)
    # Keep rights at the bottom so the same page reads from patients through company revenue.
    if independent:
        _write_row(sheet, 3, description_col + 1, ["来源／依据"], header=True)
        settlement_header = current_row + sum(
            11 + len(market["patient_flow"].get("patient_funnel") or [])
            for _, market in market_entries
        )
    for index, (indication, market) in enumerate(market_entries):
        if independent:
            output_row, block_rows = _write_independent_market_block(
                sheet, current_row, indication, market, years,
                settlement_start=settlement_header + 2 + 6 * index,
            )
            adjusted_output_rows.append(output_row)
            unadjusted_output_rows.append({"grossSalesRow": block_rows["gross_sales"]})
            current_row = block_rows["gross_sales"] + 2
            continue
        market_for_export = dict(market)
        indication_id = str(indication.get("indication_id") or "")
        valuation_market = valuation_market_lookup.get((indication_id, market_filter)) or {}
        allocated_peak = valuation_market.get("peakSalesPerIndication")
        if isinstance(allocated_peak, (int, float)) and not isinstance(allocated_peak, bool):
            market_for_export["allocated_peak_sales_anchor"] = float(allocated_peak)
        elif not (
            isinstance(market_for_export.get("allocated_peak_sales_anchor"), (int, float))
            and not isinstance(market_for_export.get("allocated_peak_sales_anchor"), bool)
        ):
            # Older drafts may predate product-level peak allocation. Never repeat
            # the full product anchor in every indication block: use an equal-share
            # guard when the valuation result cannot provide the intended weight.
            product_peak = market_for_export.get("peak_sales_anchor")
            if (
                isinstance(product_peak, (int, float))
                and not isinstance(product_peak, bool)
                and market_entry_count > 0
            ):
                market_for_export["allocated_peak_sales_anchor"] = float(product_peak) / market_entry_count
        output_row, block_rows = _write_indication_market_block(
            sheet,
            current_row,
            product,
            indication,
            market_for_export,
            years,
            ai_lookup=ai_lookup,
            apply_ai_assumptions=apply_ai_assumptions,
        )
        adjusted_output_rows.append(output_row)
        gross_sales_row = block_rows.get("gross_sales")
        exchange_rate_row = block_rows.get("exchange_rate")
        if isinstance(gross_sales_row, int):
            unadjusted_output_rows.append(
                {
                    "grossSalesRow": gross_sales_row,
                    "exchangeRateRow": exchange_rate_row if isinstance(exchange_rate_row, int) else 0,
                    "market": market_filter,
                }
            )
        current_row = output_row + 3

    if independent:
        _write_block_subsection(sheet, settlement_header, description_col, "归属收入｜终端销售汇总与权益结算")
        sheet.cell(3, description_col, "说明")
        unadjusted_total_row = settlement_header + 1
        adjusted_total_row = settlement_header + 2 + 6 * market_entry_count
        currency = market_entries[0][1]["currency"]
        sheet.cell(unadjusted_total_row, 1, f"终端销售合计（亿{currency}）")
        sheet.cell(adjusted_total_row, 1, "计入DCF的归属收入（人民币亿元）")
        sheet.cell(unadjusted_total_row, description_col, "汇总各适应症产品销售，采用获批条件下的终端金额。")
        sheet.cell(
            adjusted_total_row, description_col,
            "汇总各适应症经概率、权益与汇率调整后的公司归属收入。展开上方明细可修改对应假设。",
        )
        sheet.sheet_properties.outlinePr.summaryBelow = True
        sheet.row_dimensions.group(unadjusted_total_row + 1, adjusted_total_row - 1, hidden=True)
        sheet.row_dimensions[adjusted_total_row].collapsed = True
    else:
        unadjusted_total_row = current_row
        adjusted_total_row = current_row + 1
        unadjusted_unit = "亿元" if market_filter == "china" else "亿美元"
        sheet.cell(unadjusted_total_row, 1, f"未调整销售额合计（{unadjusted_unit}）")
        sheet.cell(adjusted_total_row, 1, "风险/权益调整收入合计（亿元）")
    for idx, _year in enumerate(years):
        col = year_start_col + idx
        letter = get_column_letter(col)
        unadjusted_terms = []
        for item in unadjusted_output_rows:
            gross_term = f"{letter}{item['grossSalesRow']}"
            unadjusted_terms.append(gross_term)
        unadjusted_formula = f"SUM({','.join(unadjusted_terms)})" if unadjusted_terms else "0"
        adjusted_terms = [f"{letter}{row}" for row in adjusted_output_rows]
        adjusted_formula = f"SUM({','.join(adjusted_terms)})" if adjusted_terms else "0"
        sheet.cell(unadjusted_total_row, col, f"={unadjusted_formula}")
        sheet.cell(adjusted_total_row, col, f"={adjusted_formula}")
    for col in range(1, description_col + 1):
        cell = sheet.cell(unadjusted_total_row, col)
        cell.fill = OUTPUT_FILL
        cell.border = THIN_BORDER
        cell.font = Font(name="SimSun", color="000000", bold=True, size=10)
    _number_format_range(
        sheet,
        unadjusted_total_row,
        year_start_col,
        adjusted_total_row,
        last_year_col,
        ONE_DECIMAL_NUMBER_FORMAT,
    )
    _style_total_row(sheet, adjusted_total_row, description_col)

    fcf_row = adjusted_total_row + 2
    _write_product_fcf_block(
        sheet,
        fcf_row,
        years,
        adjusted_total_row,
        product_result,
        market_key=market_filter,
    )
    return adjusted_total_row


def _valuation_market_result_lookup(
    product_result: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Index calculated indication-market results for robust Excel parameter mapping."""

    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for indication_result in product_result.get("indicationResults") or []:
        indication_id = str(indication_result.get("indicationId") or "")
        for market_result in indication_result.get("marketResults") or []:
            market_name = str(market_result.get("market") or "")
            if indication_id and market_name:
                lookup[(indication_id, market_name)] = market_result
    return lookup


def _write_simplified_products_sheet(
    sheet,
    products: list[dict[str, Any]],
    product_result_by_id: dict[str, dict[str, Any]],
    years: list[int],
) -> list[dict[str, Any]]:
    """Write one auditable growth-model sheet for all simplified products."""

    year_start_col = 5
    last_year_col = year_start_col + len(years) - 1
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_year_col)
    sheet["A1"] = "其他药物｜简化收入预测"
    sheet["A1"].fill = DARK_FILL
    sheet["A1"].font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True, size=16)
    sheet["A1"].alignment = Alignment(horizontal="left", vertical="center")
    _write_row(
        sheet,
        3,
        1,
        ["药物", "简化原因", "靶点 / 市场", "项目", *[f"{year}E" for year in years]],
        header=True,
    )

    current_row = 4
    summary_rows: list[dict[str, Any]] = []
    revenue_rows: list[int] = []
    for product in products:
        result = product_result_by_id.get(product.get("product_id")) or {}
        markets = result.get("simplifiedMarketResults") or []
        split_by_market = len(markets) > 1
        for market_result in markets:
            market_key = market_result.get("market") or "china"
            market_label = _market_summary_label(market_key)
            drug_name = product.get("drug_name") or product.get("product_id") or "Product"
            summary_name = f"{drug_name}（{market_label}）" if split_by_market else drug_name
            revenue_row = current_row
            growth_row = current_row + 1
            cap_row = current_row + 2
            revenue_rows.append(revenue_row)

            sheet.cell(revenue_row, 1, summary_name)
            sheet.cell(revenue_row, 2, product.get("modeling_reason") or "产品级简化收入增长法")
            sheet.cell(revenue_row, 3, f"{product.get('target') or ''} / {market_label}")
            sheet.cell(revenue_row, 4, "风险/权益调整收入（亿元）")
            baseline_year = market_result.get("baselineYear")
            sheet.cell(
                growth_row,
                2,
                "基期收入假设"
                + (f"（{baseline_year}年）" if isinstance(baseline_year, int) else ""),
            )
            sheet.cell(growth_row, 4, "销售增速（可编辑）")
            sheet.cell(cap_row, 2, "估值跟踪峰值参考，不参与收入公式")
            sheet.cell(cap_row, 4, "峰值参考（亿元）")

            revenue = list(market_result.get("equityAdjustedRevenueCny100m") or [])
            growth = list(market_result.get("defaultGrowthRates") or [])
            editable_base_year = market_result.get("editableBaseYear")
            peak_cap = market_result.get("adjustedPeakRevenueCny100m")
            for idx, year in enumerate(years):
                col = year_start_col + idx
                letter = get_column_letter(col)
                rule_value = _list_year_value(revenue, idx) or 0
                sheet.cell(cap_row, col, peak_cap if isinstance(peak_cap, (int, float)) else 0)
                if editable_base_year is not None and year == editable_base_year:
                    sheet.cell(revenue_row, col, rule_value)
                    sheet.cell(revenue_row, col).fill = INPUT_FILL
                    sheet.cell(revenue_row, col).font = Font(color="A64045")
                    sheet.cell(growth_row, col, "")
                elif editable_base_year is None or year < editable_base_year:
                    sheet.cell(revenue_row, col, rule_value)
                    sheet.cell(growth_row, col, "")
                else:
                    previous_letter = get_column_letter(col - 1)
                    rate = _list_year_value(growth, idx)
                    sheet.cell(growth_row, col, rate if isinstance(rate, (int, float)) else None)
                    sheet.cell(
                        revenue_row,
                        col,
                        f'=IF(OR({previous_letter}{revenue_row}="",{letter}{growth_row}=""),"",'
                        f"{previous_letter}{revenue_row}*(1+{letter}{growth_row}))",
                    )
                    sheet.cell(growth_row, col).fill = INPUT_FILL
                    sheet.cell(growth_row, col).font = Font(color="A64045")

            for row in (revenue_row, growth_row, cap_row):
                for col in range(1, last_year_col + 1):
                    cell = sheet.cell(row, col)
                    cell.border = THIN_BORDER
                    cell.alignment = Alignment(vertical="center", wrap_text=True)
                    cell.protection = Protection(locked=False)
            sheet.cell(revenue_row, 1).font = Font(bold=True)
            _number_format_range(sheet, revenue_row, year_start_col, revenue_row, last_year_col, ONE_DECIMAL_NUMBER_FORMAT)
            _number_format_range(sheet, growth_row, year_start_col, growth_row, last_year_col, ONE_DECIMAL_PERCENT_FORMAT)
            _number_format_range(sheet, cap_row, year_start_col, cap_row, last_year_col, ONE_DECIMAL_NUMBER_FORMAT)
            _add_cell_comment(
                sheet.cell(revenue_row, 4),
                "参考平台数据生成基期收入；可调整。",
            )
            _add_cell_comment(
                sheet.cell(cap_row, 4),
                "来自估值跟踪的峰值参考；不参与收入公式。",
            )
            summary_rows.append(
                {
                    "productId": product.get("product_id"),
                    "drugName": summary_name,
                    "target": product.get("target"),
                    "market": market_key,
                    "marketLabel": market_label,
                    "sheetName": "其他药物",
                    "totalRow": revenue_row,
                    "yearStartCol": year_start_col,
                }
            )
            current_row = cap_row + 2

    if revenue_rows:
        total_row = current_row
        sheet.cell(total_row, 1, "其他药物合计")
        sheet.cell(total_row, 4, "风险/权益调整收入（亿元）")
        for idx, _year in enumerate(years):
            col = year_start_col + idx
            letter = get_column_letter(col)
            sheet.cell(total_row, col, f"=SUM({','.join(f'{letter}{row}' for row in revenue_rows)})")
        _style_total_row(sheet, total_row, last_year_col)
        _number_format_range(sheet, total_row, year_start_col, total_row, last_year_col, ONE_DECIMAL_NUMBER_FORMAT)

    return summary_rows


def _write_milestone_income_sheet(
    sheet,
    draft: dict[str, Any],
    product_total_rows: list[dict[str, Any]],
    years: list[int],
) -> int:
    """Create a company-level editable milestone-income schedule.

    Milestones are deliberately kept outside patient-flow revenue. Every value
    starts at zero, and the summary/company DCF links to the total row so later
    user edits flow through the workbook automatically.
    """

    year_start_col = 3
    last_year_col = year_start_col + len(years) - 1
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(last_year_col, 4))
    sheet["A1"] = "里程碑收入"
    sheet["A1"].fill = DARK_FILL
    sheet["A1"].font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True, size=16)
    sheet["A1"].alignment = Alignment(horizontal="left", vertical="center")
    _write_row(
        sheet,
        3,
        1,
        ["药物", "说明", *[f"{year}E" for year in years]],
        header=True,
    )

    included_product_ids = {
        str(item.get("productId") or "")
        for item in product_total_rows
        if item.get("productId")
    }
    product_rows: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for product in draft.get("products") or []:
        product_id = str(product.get("product_id") or "")
        if not product_id or product_id in seen_ids or product_id not in included_product_ids:
            continue
        seen_ids.add(product_id)
        product_rows.append(
            (
                product.get("drug_name") or product_id,
                "可编辑，默认0",
            )
        )
    product_rows.append(("其他里程碑", "可补充未归属单药的里程碑收入"))

    first_input_row = 4
    for offset, (drug_name, note) in enumerate(product_rows):
        row = first_input_row + offset
        sheet.cell(row, 1, drug_name)
        sheet.cell(row, 2, note)
        for col in range(year_start_col, last_year_col + 1):
            cell = sheet.cell(row, col, 0.0)
            cell.fill = INPUT_FILL
            cell.font = Font(name="SimSun", color="A64045", size=11)
            cell.protection = Protection(locked=False)

    total_row = first_input_row + len(product_rows)
    sheet.cell(total_row, 1, "里程碑收入合计（亿元）")
    sheet.cell(total_row, 2, "自动汇总至DCF估值页")
    for col in range(year_start_col, last_year_col + 1):
        letter = get_column_letter(col)
        sheet.cell(
            total_row,
            col,
            f"=SUM({letter}{first_input_row}:{letter}{total_row - 1})",
        )
    _style_total_row(sheet, total_row, last_year_col)
    _number_format_range(
        sheet,
        first_input_row,
        year_start_col,
        total_row,
        last_year_col,
        ONE_DECIMAL_NUMBER_FORMAT,
    )
    return total_row


def _write_independent_market_block(sheet, start_row, indication, market, years, *, settlement_start=None):
    last_col = len(years) + 1
    source_col = last_col + 1
    currency = market["currency"]
    market_label = "中国" if market["market"] == "china" else "美国"
    _write_block_subsection(
        sheet, start_row, source_col,
        f"患者池｜{indication['indication_text']} | {market_label}",
    )
    flow = market["patient_flow"]
    sources = market["revenue_input_sources"]
    funnel = flow.get("patient_funnel") or []
    annual_inputs = {
        "eligible_patients": [value * 10000 for value in flow["annual_eligible_patients_10k"]],
        "class_penetration": flow["penetration_curve"],
        "product_class_share": flow["product_share_curve"],
        "treatment_months": flow["treatment_months"],
        "monthly_net_price": [value * 10000 for value in flow["monthly_price"]],
    }
    inputs = {
        **annual_inputs,
        "launch_year": [market["launch_year"]],
        "success_probability": [market["success_probability"]],
        "economic_share": [_market_economic_share(market)],
        "exchange_rate": [market["exchange_rate"]],
    }
    sales_keys = (
        "launch_year", "class_penetration", "product_class_share", "treated_patients",
        "treatment_months", "monthly_net_price", "gross_sales",
    )
    rights_keys = ("success_probability", "economic_share", "exchange_rate", "risk_adjusted")
    patient_total_row = start_row + 1 + len(funnel)
    if settlement_start is None:
        settlement_start = patient_total_row + 9
    _write_block_subsection(sheet, patient_total_row + 1, source_col, "产品销售")
    _write_block_subsection(
        sheet, settlement_start, source_col, f"归属收入明细｜{indication['indication_text']}"
    )
    rows = {"eligible_patients": patient_total_row}
    rows.update({key: patient_total_row + offset for offset, key in enumerate(sales_keys, 2)})
    rows.update({key: settlement_start + offset for offset, key in enumerate(rights_keys, 1)})
    labels = {key: label for key, (label, _, _) in ANNUAL_FIELDS.items()}
    labels.update({
        "monthly_net_price": f"月净价（{currency}/人/月）",
        "treated_patients": "使用本产品患者数（人）",
        "gross_sales": f"产品净销售额（亿{currency}）",
        "launch_year": "上市年",
        "success_probability": "上市成功概率" if market["market"] == "china" else "海外达峰概率",
        "economic_share": "公司归属比例",
        "exchange_rate": "汇率（人民币/当地货币）",
        "risk_adjusted": "计入DCF的适应症归属收入（人民币亿元）",
    })
    formats = {key: fmt for key, (_, fmt, _) in ANNUAL_FIELDS.items()}
    formats.update({"launch_year": "0", "success_probability": "0.0%", "economic_share": "0.0%", "exchange_rate": "0.0000"})
    descriptions = {
        "treated_patients": "上市后，可治疗患者数 × 同类药物渗透率 × 该药物在同类药物中份额",
        "gross_sales": "使用本产品患者数 × 治疗时长 × 月净价",
        "exchange_rate": "" if "exchange_rate" in sources else "中国市场按人民币计价",
        "risk_adjusted": "终端销售额 × 对应成功或达峰概率 × 公司归属比例 × 汇率",
    }
    if funnel:
        del inputs["eligible_patients"]
        if len(funnel) > 1:
            descriptions["eligible_patients"] = "可展开上方患者流查看逐项假设和依据。"
        _write_independent_patient_funnel(sheet, start_row + 1, patient_total_row, last_col, funnel)
    for key, row in rows.items():
        sheet.cell(row, 1, labels[key])
        explanation, evidence = market_notes(market, (key,))
        if key in ("class_penetration", "product_class_share") and market.get("penetration_basis") != "observed":
            explanation = "【自主假设】" + explanation
        write_note_columns(sheet, row, source_col, explanation, evidence, base=descriptions.get(key))
        if key not in inputs:
            continue
        series = inputs[key]
        _style_assumption_rows(sheet, [row], 2, 1 + len(series))
        for col, value in enumerate(series, 2):
            cell = sheet.cell(row, col, value)
            cell.number_format = formats[key]
        limit = ANNUAL_FIELDS.get(key, (None, None, 1 if key in ("success_probability", "economic_share") else None))[2]
        if key == "launch_year":
            validation = DataValidation(type="whole", operator="between", formula1=1900, formula2=2200)
        elif limit is not None:
            validation = DataValidation(type="decimal", operator="between", formula1=0, formula2=limit)
        else:
            validation = DataValidation(
                type="decimal", operator="greaterThan" if key == "exchange_rate" else "greaterThanOrEqual", formula1=0,
            )
        validation.showErrorMessage = True
        validation.errorTitle = "输入超出范围"
        validation.error = "请按本行单位和允许范围输入数字。"
        sheet.add_data_validation(validation)
        validation.add(f"B{row}:{get_column_letter(1 + len(series))}{row}")

    for col, year in enumerate(years, 2):
        letter = get_column_letter(col)
        sheet.cell(rows["treated_patients"], col,
                   f"=IF({year}<$B${rows['launch_year']},0,{letter}{rows['eligible_patients']}*"
                   f"{letter}{rows['class_penetration']}*{letter}{rows['product_class_share']})")
        sheet.cell(rows["gross_sales"], col,
                   f"={letter}{rows['treated_patients']}*{letter}{rows['treatment_months']}*"
                   f"{letter}{rows['monthly_net_price']}/100000000")
        sheet.cell(rows["risk_adjusted"], col,
                   f"={letter}{rows['gross_sales']}*$B${rows['success_probability']}*"
                   f"$B${rows['economic_share']}*$B${rows['exchange_rate']}")
    _style_output_rows(sheet, [rows[key] for key in ("treated_patients", "gross_sales", "risk_adjusted")], 2, last_col)
    _number_format_range(sheet, rows["treated_patients"], 2, rows["treated_patients"], last_col, "#,##0")
    return rows["risk_adjusted"], rows


def _write_independent_patient_funnel(sheet, first_row, total_row, last_col, steps):
    basis_col = last_col + 1
    for row, step in enumerate(steps, first_row):
        sheet.cell(row, 1, step["label"])
        explanation, evidence = market_notes({"revenue_input_sources": {"step": step["source"]}}, ("step",))
        write_note_columns(sheet, row, basis_col, explanation, evidence)
        _style_assumption_rows(sheet, [row], 2, last_col)
        number_format = "0.0%" if step["operation"] == "multiply" else "#,##0"
        for col, value in enumerate(step["values"], 2):
            sheet.cell(row, col, value).number_format = number_format
    for col in range(2, last_col + 1):
        letter = get_column_letter(col)
        expression = f"{letter}{first_row}"
        for row, step in enumerate(steps[1:], first_row + 1):
            cell_ref = f"{letter}{row}"
            operation = step["operation"]
            if operation == "multiply":
                expression = f"({expression}*{cell_ref})"
            elif operation == "add":
                expression = f"({expression}+{cell_ref})"
            else:
                expression = f"MAX(0,{expression}-{cell_ref})"
        sheet.cell(total_row, col, f"={expression}").number_format = "#,##0"
    _style_output_rows(sheet, [total_row], 2, last_col)
    if total_row > first_row + 1:
        sheet.sheet_properties.outlinePr.summaryBelow = True
        sheet.row_dimensions.group(first_row + 1, total_row - 1, hidden=True)
        sheet.row_dimensions[total_row].collapsed = True


def _write_indication_market_block(
    sheet,
    start_row: int,
    product: dict[str, Any],
    indication: dict[str, Any],
    market: dict[str, Any],
    years: list[int],
    *,
    ai_lookup: dict[tuple[str, str, str, str], dict[str, Any]] | None = None,
    apply_ai_assumptions: bool = False,
) -> tuple[int, dict[str, int]]:
    if market.get("revenue_input_sources"):
        return _write_independent_market_block(sheet, start_row, indication, market, years)
    year_start_col = 2
    last_year_col = year_start_col + len(years) - 1
    description_col = last_year_col + 1
    ai_value_col = description_col + 1
    ai_basis_col = description_col + 2
    market_label = "国内市场" if market.get("market") == "china" else "美国/海外市场"
    flow = market.get("patient_flow") or {}
    market_launch_year = market.get("launch_year") or indication.get("launch_year")
    market_peak_year = market.get("peak_year") or indication.get("peak_year")
    biomarker_ratio = _bounded_ratio(flow.get("biomarker_ratio"), default=1.0)
    target_ratio_proposal = _find_ai_proposal(
        ai_lookup,
        product,
        indication,
        market,
        "target_treatment_ratio",
    ) if apply_ai_assumptions else None
    ai_target_ratio = _proposal_scalar_value(target_ratio_proposal)
    target_treatment_ratio = _bounded_ratio(
        ai_target_ratio if ai_target_ratio is not None else flow.get("target_treatment_ratio"),
        default=1.0,
    )
    eligible_patient_ratio = biomarker_ratio * target_treatment_ratio
    pos, pos_source = resolve_market_success_probability(
        market=market,
        indication_pos=indication.get("pos"),
        indication_phase=indication.get("phase"),
    )
    equity_or_share = _market_economic_share(market)
    exchange_rate = market.get("exchange_rate") or (1 if market.get("market") == "china" else 7)
    patient_proposal = _find_ai_proposal(
        ai_lookup,
        product,
        indication,
        market,
        "base_patient_count_10k",
    ) if apply_ai_assumptions else None
    ai_patient_value = _proposal_scalar_value(patient_proposal)
    source_patient_value = _first_numeric_value(flow.get("base_patient_count_10k"))
    rule_patient_value = None
    rule_patient_method = None
    if source_patient_value is None and ai_patient_value is None:
        rule_patient_value, rule_patient_method = _rule_patient_count_prefill(
            years=years,
            patient_growth_rate=float(flow.get("patient_growth_rate") or 0),
            eligible_patient_ratio=eligible_patient_ratio,
            monthly_price=float(_first_numeric_value(flow.get("monthly_price")) or 0),
            treatment_months=float(_first_numeric_value(flow.get("treatment_months")) or 0),
            peak_sales_anchor=market.get("allocated_peak_sales_anchor") or market.get("peak_sales_anchor"),
            eligible_indication_count=1,
            peak_year=market_peak_year,
        )
    initial_patient_value = source_patient_value if source_patient_value is not None else rule_patient_value

    sheet.merge_cells(start_row=start_row, start_column=1, end_row=start_row, end_column=description_col)
    title = " | ".join(
        part
        for part in (
            str(indication.get("indication_text") or "未命名适应症"),
            market_label,
            str(indication.get("phase") or "").strip(),
        )
        if part
    )
    sheet.cell(start_row, 1, title)
    sheet.cell(start_row, 1).fill = SECTION_FILL
    sheet.cell(start_row, 1).font = Font(color="FFFFFF", bold=True)
    sheet.cell(start_row, 1).alignment = Alignment(horizontal="left", vertical="center", wrap_text=False)
    sheet.row_dimensions[start_row].height = 20
    evidence_comment = _trial_evidence_comment(indication)
    if evidence_comment:
        sheet.cell(start_row, 1).comment = Comment(evidence_comment, "DrugAlpha DCF")

    # Keep the patient-flow model in one continuous, dependency-ordered block.
    # Editable assumptions remain pale pink; formula rows remain white.
    rows = {
        "model_section": start_row + 1,
        "base_patients": start_row + 2,
        "growth": start_row + 3,
        "biomarker_ratio": start_row + 4,
        "target_treatment_ratio": start_row + 5,
        "treatable_patients": start_row + 6,
        "penetration": start_row + 7,
        "treated_patients": start_row + 8,
        "monthly_price": start_row + 9,
        "treatment_months": start_row + 10,
        "annual_cost": start_row + 11,
        "gross_sales": start_row + 12,
        "pos": start_row + 13,
        "share": start_row + 14,
        "exchange_rate": start_row + 15,
        "risk_adjusted": start_row + 16,
    }

    _write_block_subsection(
        sheet,
        rows["model_section"],
        description_col,
        "患者流与收入测算（淡粉可直接修改，白底自动计算）",
    )
    if ai_lookup:
        sheet.cell(rows["model_section"], ai_value_col, "AI建议值")
        sheet.cell(rows["model_section"], ai_basis_col, "AI依据")
        for col in (ai_value_col, ai_basis_col):
            cell = sheet.cell(rows["model_section"], col)
            cell.fill = SUBSECTION_FILL
            cell.font = Font(name="Microsoft YaHei", color="071B35", bold=True, size=10)
            cell.border = THIN_BORDER

    allocated_peak_anchor = market.get("allocated_peak_sales_anchor")
    if not isinstance(allocated_peak_anchor, (int, float)):
        allocated_peak_anchor = market.get("peak_sales_anchor")
    labels = {
        "base_patients": ("患者规模（万人）", _patient_source_note(flow)),
        "growth": ("患者增长率", "年度增长假设，可调整"),
        "biomarker_ratio": ("Biomarker比例", _biomarker_ratio_note(flow)),
        "target_treatment_ratio": ("目标治疗人群比例", "根据目标患者范围，可调整"),
        "penetration": ("产品渗透率", "渗透率假设，可调整"),
        "monthly_price": (_price_label(market, flow), _price_source_note(flow)),
        "treatment_months": (_treatment_units_label(flow), _treatment_duration_note(flow)),
        "pos": ("上市概率", "根据研发阶段或估值跟踪"),
        "share": (_share_label(market), "根据产品权益或分成安排"),
        "exchange_rate": ("汇率", "市场汇率假设"),
        "treatable_patients": (
            "可治疗患者数（万人）",
            "患者规模 × Biomarker比例 × 目标治疗人群比例",
        ),
        "treated_patients": (
            "使用本产品患者数（万人）",
            "可治疗患者数 × 产品渗透率",
        ),
        "annual_cost": (_annual_cost_label(market), _annual_cost_calculation_note(flow)),
        "gross_sales": (_gross_sales_label(market), "使用本产品患者数 × 年治疗费用"),
        "risk_adjusted": (
            "风险/权益调整收入（亿元）",
            _risk_adjusted_calculation_note(market),
        ),
    }
    for key, (label, note) in labels.items():
        row = rows[key]
        sheet.cell(row, 1, label)
        sheet.cell(row, description_col, note)
    _apply_reference_links(
        sheet.cell(rows["base_patients"], description_col),
        _patient_source_urls(flow),
    )
    _apply_reference_links(
        sheet.cell(rows["biomarker_ratio"], description_col),
        _biomarker_source_urls(flow),
    )

    for year_idx, _year in enumerate(years):
        col = year_start_col + year_idx
        prev_col = get_column_letter(col - 1) if year_idx > 0 else None
        col_letter = get_column_letter(col)

        base_patient = initial_patient_value if year_idx == 0 else None
        if year_idx == 0:
            sheet.cell(rows["base_patients"], col, base_patient)
        else:
            sheet.cell(
                rows["base_patients"],
                col,
                f'=IF({prev_col}{rows["base_patients"]}="","",{prev_col}{rows["base_patients"]}*(1+{prev_col}{rows["growth"]}))',
            )
        sheet.cell(rows["growth"], col, flow.get("patient_growth_rate") or 0)
        sheet.cell(rows["biomarker_ratio"], col, biomarker_ratio)
        sheet.cell(rows["target_treatment_ratio"], col, target_treatment_ratio)
        sheet.cell(
            rows["treatable_patients"],
            col,
            f'=IF({col_letter}{rows["base_patients"]}="","",{col_letter}{rows["base_patients"]}*{col_letter}{rows["biomarker_ratio"]}*{col_letter}{rows["target_treatment_ratio"]})',
        )
        sheet.cell(rows["penetration"], col, _list_year_value(flow.get("penetration_curve"), year_idx))
        sheet.cell(
            rows["treated_patients"],
            col,
            f'=IF(OR({col_letter}{rows["treatable_patients"]}="",{col_letter}{rows["penetration"]}=""),"",{col_letter}{rows["treatable_patients"]}*{col_letter}{rows["penetration"]})',
        )
        sheet.cell(rows["monthly_price"], col, _list_year_value(flow.get("monthly_price"), year_idx))
        sheet.cell(rows["treatment_months"], col, _list_year_value(flow.get("treatment_months"), year_idx))
        sheet.cell(rows["annual_cost"], col, f"={col_letter}{rows['monthly_price']}*{col_letter}{rows['treatment_months']}")
        gross_sales_formula = (
            f'=IF(OR({col_letter}{rows["treated_patients"]}="",'
            f'{col_letter}{rows["annual_cost"]}=""),"",'
            f'{col_letter}{rows["treated_patients"]}*{col_letter}{rows["annual_cost"]})'
        )
        sheet.cell(rows["gross_sales"], col, gross_sales_formula)
        sheet.cell(rows["pos"], col, pos)
        sheet.cell(rows["share"], col, equity_or_share)
        sheet.cell(rows["exchange_rate"], col, exchange_rate)
        sheet.cell(
            rows["risk_adjusted"],
            col,
            f'=IF({col_letter}{rows["gross_sales"]}="","",{col_letter}{rows["gross_sales"]}*{col_letter}{rows["pos"]}*{col_letter}{rows["share"]}*{col_letter}{rows["exchange_rate"]})',
        )

    _style_assumption_rows(
        sheet,
        rows=[
            rows["base_patients"],
            rows["growth"],
            rows["biomarker_ratio"],
            rows["target_treatment_ratio"],
            rows["penetration"],
            rows["monthly_price"],
            rows["treatment_months"],
            rows["pos"],
            rows["share"],
            rows["exchange_rate"],
        ],
        start_col=year_start_col,
        end_col=last_year_col,
    )
    if apply_ai_assumptions:
        _apply_ai_proposal_values(
            sheet,
            rows,
            year_start_col,
            last_year_col,
            product,
            indication,
            market,
            ai_lookup,
        )
    effective_patient_value = (
        float(ai_patient_value)
        if ai_patient_value is not None
        else float(initial_patient_value)
        if initial_patient_value is not None
        else None
    )
    if ai_patient_value is not None:
        patient_source_label = "公开资料辅助预填，可调整"
        penetration_source_label = "渗透率假设，可调整"
    elif rule_patient_value is not None:
        if rule_patient_method == "peak_anchor_backsolve":
            patient_source_label = "模型预填，可调整"
        else:
            patient_source_label = "暂用基础假设，需复核"
        penetration_source_label = "渗透率假设，可调整"
    else:
        patient_source_label = None
        penetration_source_label = None

    if patient_source_label and not sheet.cell(rows["base_patients"], description_col).value:
        sheet.cell(rows["base_patients"], description_col, patient_source_label)
    has_backend_peak_calibration = isinstance(market.get("peak_anchor_calibration"), dict)
    if not has_backend_peak_calibration and effective_patient_value is not None and (
        isinstance(allocated_peak_anchor, (int, float))
        or not _has_numeric_values(flow.get("penetration_curve"))
    ):
        inferred_curve = _backsolve_export_penetration_curve(
            years=years,
            base_patient_count_10k=effective_patient_value,
            patient_growth_rate=float(flow.get("patient_growth_rate") or 0),
            eligible_patient_ratio=eligible_patient_ratio,
            monthly_price=float(_first_numeric_value(flow.get("monthly_price")) or 0),
            treatment_months=float(_first_numeric_value(flow.get("treatment_months")) or 0),
            peak_sales_anchor=allocated_peak_anchor,
            eligible_indication_count=1,
            launch_year=market_launch_year,
            peak_year=market_peak_year,
            market=str(market.get("market") or "china"),
        )
        if _has_numeric_values(inferred_curve):
            for offset, value in enumerate(inferred_curve):
                cell = sheet.cell(rows["penetration"], year_start_col + offset)
                cell.value = value
                if ai_patient_value is not None:
                    _style_ai_applied_cell(cell)
            sheet.cell(rows["penetration"], description_col, penetration_source_label or "渗透率假设，可调整")
    _add_row_comments(
        sheet,
        rows["base_patients"],
        year_start_col,
        last_year_col,
        f"患者规模：{_patient_source_note(flow)}。必要的父疾病→子疾病换算已在本行完成；可调整。",
    )
    _add_row_comments(sheet, rows["growth"], year_start_col, last_year_col, "参考疾病人群变化；可调整。")
    _add_row_comments(
        sheet,
        rows["biomarker_ratio"],
        year_start_col,
        last_year_col,
        _biomarker_ratio_comment(flow) + " 可调整。",
    )
    _add_row_comments(
        sheet,
        rows["target_treatment_ratio"],
        year_start_col,
        last_year_col,
        "基于适应症限定的目标患者范围预填；可调整。",
    )
    _add_row_comments(sheet, rows["penetration"], year_start_col, last_year_col, "参考市场情况；可调整。")
    _add_row_comments(
        sheet,
        rows["monthly_price"],
        year_start_col,
        last_year_col,
        _price_rule_comment(flow, market) + "\n可调整。",
    )
    _add_row_comments(
        sheet,
        rows["treatment_months"],
        year_start_col,
        last_year_col,
        f"{_treatment_duration_note(flow)}；可调整。",
    )
    _add_ai_row_comments(sheet, rows["base_patients"], year_start_col, last_year_col, product, indication, market, "base_patient_count_10k", ai_lookup)
    _add_ai_row_comments(sheet, rows["target_treatment_ratio"], year_start_col, last_year_col, product, indication, market, "target_treatment_ratio", ai_lookup)
    _add_ai_row_comments(sheet, rows["growth"], year_start_col, last_year_col, product, indication, market, "patient_growth_rate", ai_lookup)
    _add_ai_row_comments(sheet, rows["penetration"], year_start_col, last_year_col, product, indication, market, "penetration_curve", ai_lookup)
    _add_ai_row_comments(sheet, rows["monthly_price"], year_start_col, last_year_col, product, indication, market, "monthly_price", ai_lookup)
    _add_ai_row_comments(sheet, rows["treatment_months"], year_start_col, last_year_col, product, indication, market, "treatment_months", ai_lookup)
    _write_ai_proposal_cells(sheet, rows["base_patients"], ai_value_col, ai_basis_col, product, indication, market, "base_patient_count_10k", ai_lookup)
    _write_ai_proposal_cells(sheet, rows["target_treatment_ratio"], ai_value_col, ai_basis_col, product, indication, market, "target_treatment_ratio", ai_lookup)
    _write_ai_proposal_cells(sheet, rows["growth"], ai_value_col, ai_basis_col, product, indication, market, "patient_growth_rate", ai_lookup)
    _write_ai_proposal_cells(sheet, rows["penetration"], ai_value_col, ai_basis_col, product, indication, market, "penetration_curve", ai_lookup)
    _write_ai_proposal_cells(sheet, rows["monthly_price"], ai_value_col, ai_basis_col, product, indication, market, "monthly_price", ai_lookup)
    _write_ai_proposal_cells(sheet, rows["treatment_months"], ai_value_col, ai_basis_col, product, indication, market, "treatment_months", ai_lookup)
    _add_row_comments(
        sheet,
        rows["pos"],
        year_start_col,
        last_year_col,
        "参考研发阶段或估值跟踪；可调整。",
    )
    _add_row_comments(
        sheet,
        rows["share"],
        year_start_col,
        last_year_col,
        "参考产品权益或分成安排；可调整。",
    )
    _add_row_comments(sheet, rows["exchange_rate"], year_start_col, last_year_col, "参考市场汇率；可调整。")
    _style_output_rows(
        sheet,
        rows=[rows["treatable_patients"], rows["treated_patients"], rows["annual_cost"], rows["gross_sales"], rows["risk_adjusted"]],
        start_col=year_start_col,
        end_col=last_year_col,
    )
    _number_formats_for_block(sheet, rows, year_start_col, last_year_col)
    return rows["risk_adjusted"], rows


def _write_product_fcf_block(
    sheet,
    start_row: int,
    years: list[int],
    revenue_row: int,
    product_result: dict[str, Any],
    *,
    market_key: str = "china",
) -> None:
    year_start_col = 2
    last_year_col = year_start_col + len(years) - 1
    description_col = last_year_col + 1
    sheet.merge_cells(start_row=start_row, start_column=1, end_row=start_row, end_column=description_col)
    sheet.cell(start_row, 1, "产品简化现金流 / DCF")
    sheet.cell(start_row, 1).fill = SECTION_FILL
    sheet.cell(start_row, 1).font = Font(color="FFFFFF", bold=True)

    rows = {
        "revenue": start_row + 2,
        "cash_flow_margin": start_row + 3,
        "fcf": start_row + 4,
        "discount_period": start_row + 5,
        "discount_factor": start_row + 6,
        "pv_fcf": start_row + 7,
        "wacc": start_row + 9,
        "terminal_growth": start_row + 10,
        "pv_forecast_fcf": start_row + 11,
        "pv_terminal_value": start_row + 12,
        "enterprise_value": start_row + 13,
    }
    labels = [
        (rows["revenue"], "产品收入（亿元）", "来自产品合计"),
        (rows["cash_flow_margin"], "产品现金流率", "可编辑假设"),
        (rows["fcf"], "自由现金流（亿元）", "收入 × 产品现金流率"),
        (rows["discount_period"], "折现期", "mid-year convention"),
        (rows["discount_factor"], "折现因子", "(1+WACC)^折现期"),
        (rows["pv_fcf"], "FCF现值（亿元）", "自由现金流 / 折现因子"),
    ]
    for row, label, note in labels:
        sheet.cell(row, 1, label)
        sheet.cell(row, description_col, note)

    default_cash_flow_margin = 0.95 if market_key == "us" else 0.30
    cash_flow_margin_note = (
        "海外分成/权益调整收入轻资产口径，默认95%，可编辑"
        if market_key == "us"
        else "国内产品经营现金流口径，默认30%，可编辑"
    )
    sheet.cell(rows["cash_flow_margin"], description_col, cash_flow_margin_note)

    for idx, _year in enumerate(years):
        col = year_start_col + idx
        letter = get_column_letter(col)
        sheet.cell(rows["revenue"], col, f"={letter}{revenue_row}")
        sheet.cell(rows["cash_flow_margin"], col, default_cash_flow_margin)
        sheet.cell(
            rows["fcf"],
            col,
            f"={letter}{rows['revenue']}*{letter}{rows['cash_flow_margin']}",
        )
        sheet.cell(rows["discount_period"], col, idx + 0.5)
        sheet.cell(
            rows["discount_factor"],
            col,
            f"=(1+$B${rows['wacc']})^{letter}{rows['discount_period']}",
        )
        sheet.cell(
            rows["pv_fcf"],
            col,
            f"={letter}{rows['fcf']}/{letter}{rows['discount_factor']}",
        )

    dcf = product_result.get("dcf") or {}
    wacc = dcf.get("discountRate")
    terminal_growth = dcf.get("terminalGrowthRate")
    if not isinstance(wacc, (int, float)) or isinstance(wacc, bool):
        wacc = 0.10
    if not isinstance(terminal_growth, (int, float)) or isinstance(terminal_growth, bool):
        terminal_growth = 0.02
    sheet.cell(rows["wacc"], 1, "WACC / 折现率")
    sheet.cell(rows["wacc"], 2, float(wacc))
    sheet.cell(rows["wacc"], description_col, "可编辑假设")
    sheet.cell(rows["terminal_growth"], 1, "永续增长率")
    sheet.cell(rows["terminal_growth"], 2, float(terminal_growth))
    sheet.cell(rows["terminal_growth"], description_col, "可编辑假设")

    final_col = get_column_letter(last_year_col)
    first_col = get_column_letter(year_start_col)
    sheet.cell(
        rows["pv_forecast_fcf"],
        2,
        f"=SUM({first_col}{rows['pv_fcf']}:{final_col}{rows['pv_fcf']})",
    )
    sheet.cell(
        rows["pv_terminal_value"],
        2,
        (
            f'=IF($B${rows["wacc"]}>$B${rows["terminal_growth"]},'
            f'{final_col}{rows["fcf"]}*(1+$B${rows["terminal_growth"]})/'
            f'($B${rows["wacc"]}-$B${rows["terminal_growth"]})/'
            f'{final_col}{rows["discount_factor"]},"")'
        ),
    )
    sheet.cell(
        rows["enterprise_value"],
        2,
        f"=B{rows['pv_forecast_fcf']}+B{rows['pv_terminal_value']}",
    )
    dcf_rows = [
        (rows["pv_forecast_fcf"], "预测期 FCF 现值（亿元）"),
        (rows["pv_terminal_value"], "终值现值（亿元）"),
        (rows["enterprise_value"], "产品 DCF 价值（亿元）"),
    ]
    for row, label in dcf_rows:
        sheet.cell(row, 1, label)
        sheet.cell(row, description_col, "估值结果")
        sheet.cell(row, 1).fill = SUBSECTION_FILL
        sheet.cell(row, 1).font = Font(bold=True)

    _style_assumption_rows(
        sheet,
        rows=[rows["cash_flow_margin"]],
        start_col=year_start_col,
        end_col=last_year_col,
    )
    _style_output_rows(
        sheet,
        rows=[rows["revenue"], rows["fcf"], rows["discount_period"], rows["discount_factor"], rows["pv_fcf"]],
        start_col=year_start_col,
        end_col=last_year_col,
    )
    for row in (rows["wacc"], rows["terminal_growth"]):
        sheet.cell(row, 2).fill = INPUT_FILL
        sheet.cell(row, 2).font = Font(name="SimSun", color="A64045", size=11)
        sheet.cell(row, 2).protection = Protection(locked=False)
    for row in (rows["pv_forecast_fcf"], rows["pv_terminal_value"], rows["enterprise_value"]):
        sheet.cell(row, 2).fill = OUTPUT_FILL
        sheet.cell(row, 2).font = Font(name="SimSun", color="000000", bold=True, size=11)
    _number_format_range(sheet, rows["revenue"], year_start_col, rows["revenue"], last_year_col, "#,##0.0")
    _number_format_range(sheet, rows["fcf"], year_start_col, rows["fcf"], last_year_col, "#,##0.0")
    _number_format_range(sheet, rows["cash_flow_margin"], year_start_col, rows["cash_flow_margin"], last_year_col, "0.0%")
    _number_format_range(sheet, rows["discount_period"], year_start_col, rows["discount_factor"], last_year_col, "0.0")
    _number_format_range(sheet, rows["pv_fcf"], year_start_col, rows["pv_fcf"], last_year_col, "#,##0.0")
    _number_format_range(sheet, rows["wacc"], 2, rows["terminal_growth"], 2, "0.0%")
    _number_format_range(sheet, rows["pv_forecast_fcf"], 2, rows["enterprise_value"], 2, "#,##0.0")


def _write_dcf_block(
    sheet,
    start_row: int,
    years: list[int],
    revenue_row: int,
    valuation: dict[str, Any],
    financial_assumptions: dict[str, Any] | None = None,
    ai_assumptions: dict[str, Any] | None = None,
) -> dict[str, int]:
    year_start_col = 3
    last_year_col = year_start_col + len(years) - 1
    description_col = last_year_col + 1
    source_col = last_year_col + 2

    base_result = next((item for item in valuation.get("scenarioResults") or [] if item.get("scenario") == "base"), {})
    dcf = base_result.get("companyDcf") or {}
    combined_sales_admin = (financial_assumptions or {}).get("operatingExpenseMode") == "combined_sales_admin"
    has_bridge = bool((financial_assumptions or {}).get("equityBridge"))
    bridge_offset = int(has_bridge)

    rows = {
        "gross_margin": start_row,
        "gross_profit": start_row + 1,
        "sales_expense": start_row + 2,
        "sales_expense_rate": start_row + 3,
        "rd_expense": start_row + 4,
        "rd_expense_rate": start_row + 5,
        "admin_expense": start_row + 6,
        "admin_expense_rate": start_row + 7,
        "ebit": start_row + 8,
        "tax_rate": start_row + 9,
        "tax": start_row + 10,
        "nopat": start_row + 11,
        "da": start_row + 12,
        "da_rate": start_row + 13,
        "capex": start_row + 14,
        "capex_rate": start_row + 15,
        "nwc_change": start_row + 16,
        "fcf": start_row + 17,
        "discount_period": start_row + 18,
        "wacc": start_row + 19,
        "discount_factor": start_row + 20,
        "pv_fcf": start_row + 21,
        "terminal_growth": start_row + 22,
        "terminal_value": start_row + 23,
        "pv_terminal_value": start_row + 24,
        "enterprise_value": start_row + 25,
        "equity_value": start_row + 26 + bridge_offset,
        "share_count": start_row + 27 + bridge_offset,
        "value_per_share": start_row + 28 + bridge_offset,
    }
    if has_bridge:
        rows["net_cash"] = start_row + 26

    labels = [
        ("gross_margin", "毛利率", "可编辑假设"),
        ("gross_profit", "毛利（亿元）", "收入 × 毛利率"),
        ("sales_expense", "销售费用（亿元）", "三阶段金额预填，可编辑"),
        ("sales_expense_rate", "销售费用率", "费用金额 ÷ 总收入（自动计算，仅供参考）"),
        ("rd_expense", "研发费用（亿元）", "三阶段金额预填，可编辑"),
        ("rd_expense_rate", "研发费用率", "研发费用 ÷ 总收入（自动计算，仅供参考）"),
        ("admin_expense", "管理费用（亿元）", "三阶段金额预填，可编辑"),
        ("admin_expense_rate", "管理费用率", "管理费用 ÷ 总收入（自动计算，仅供参考）"),
        ("ebit", "EBIT（亿元）", "毛利 - 销售费用 - 研发费用 - 管理费用"),
        ("tax_rate", "所得税率", "可编辑假设"),
        ("tax", "所得税（亿元）", "MAX(EBIT,0) × 税率"),
        ("nopat", "NOPAT（亿元）", "EBIT - 所得税"),
        ("da", "加：折旧摊销（亿元）", "历史金额外推，可编辑"),
        ("da_rate", "折旧摊销 / 收入", "折旧摊销 ÷ 总收入（自动计算，仅供参考）"),
        ("capex", "减：资本开支（亿元）", "历史金额外推，可编辑"),
        ("capex_rate", "资本开支 / 收入", "资本开支 ÷ 总收入（自动计算，仅供参考）"),
        ("nwc_change", "减：营运资本变动（亿元）", "历史口径预填，可编辑"),
        ("fcf", "自由现金流（亿元）", "NOPAT + D&A - Capex - NWC 变动"),
        ("discount_period", "折现期", "默认 mid-year convention"),
        ("wacc", "WACC / 折现率", "可编辑假设"),
        ("discount_factor", "折现因子", "(1+WACC)^折现期"),
        ("pv_fcf", "预测期 FCF 现值（亿元）", "FCF / 折现因子"),
        ("terminal_growth", "永续增长率", "可编辑假设"),
        ("terminal_value", "终值（亿元）", "末年 FCF × (1+g) / (WACC-g)"),
        ("pv_terminal_value", "终值现值（亿元）", "终值 / 末年折现因子"),
        ("enterprise_value", "企业价值（亿元）", "预测期 FCF 现值 + 终值现值"),
        ("equity_value", "股权价值*（亿元）", "暂按企业价值；*未作净现金/净债务调整"),
        ("share_count", "总股本（亿股）", _share_count_source_label(financial_assumptions)),
        ("value_per_share", "每股价值*（元/股）", "企业价值 / 总股本；*未作净现金/净债务调整"),
    ]
    if has_bridge:
        labels[-3:] = [
            ("net_cash", "加：净现金（亿元）", "引用历史财务数据页，净债务为负数"),
            ("equity_value", "股权价值（亿元）", "企业价值 + 净现金"),
            ("share_count", "总股本（亿股）", _share_count_source_label(financial_assumptions)),
            ("value_per_share", "每股价值（元/股）", "股权价值 / 总股本"),
        ]
    for key, label, note in labels:
        row = rows[key]
        sheet.cell(row, 1, label)
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        sheet.cell(row, description_col, note)

    annual_rate_curves = {
        "gross_margin": [0.88, 0.89, 0.90, 0.90, 0.90, 0.90, 0.90, 0.90, 0.90, 0.90],
        "sales_expense_rate": [0.35, 0.32, 0.28, 0.24, 0.21, 0.19, 0.18, 0.17, 0.16, 0.16],
        "rd_expense_rate": [0.45, 0.40, 0.34, 0.28, 0.24, 0.21, 0.19, 0.18, 0.17, 0.16],
        "admin_expense_rate": [0.10, 0.09, 0.08, 0.07, 0.06, 0.05, 0.05, 0.05, 0.05, 0.05],
        "tax_rate": [0.00, 0.00, 0.05, 0.10, 0.12, 0.15, 0.15, 0.15, 0.15, 0.15],
        "da_rate": [0.00 for _ in years],
        "capex_rate": [0.00 for _ in years],
        "nwc_rate": [0.00 for _ in years],
    }
    effective_curves = _effective_financial_curves(financial_assumptions, ai_assumptions)
    if effective_curves:
        for key, values in effective_curves.items():
            if key in annual_rate_curves and isinstance(values, list):
                annual_rate_curves[key] = values
    scalar_assumptions = {
        "wacc": dcf["discountRate"] if dcf.get("discountRate") is not None else 0.10,
        "terminal_growth": dcf["terminalGrowthRate"] if dcf.get("terminalGrowthRate") is not None else 0.02,
        "share_count": (financial_assumptions or {}).get("shareCount100m"),
    }
    applied_ai_fields = _applied_ai_financial_fields(ai_assumptions)
    if combined_sales_admin:
        applied_ai_fields.pop("admin_expense_rate", None)
    amount_row_by_rate = {
        "sales_expense_rate": "sales_expense",
        "rd_expense_rate": "rd_expense",
        "admin_expense_rate": "admin_expense",
        "da_rate": "da",
        "capex_rate": "capex",
        "nwc_rate": "nwc_change",
    }
    for key in annual_rate_curves:
        source_row_key = amount_row_by_rate.get(key, key)
        sheet.cell(
            rows[source_row_key],
            source_col,
            _financial_curve_source_label(
                key,
                financial_assumptions,
                ai_applied=key in applied_ai_fields,
                combined_sales_admin=combined_sales_admin,
            ),
        )
        sheet.cell(rows[source_row_key], source_col).fill = OUTPUT_FILL
        sheet.cell(rows[source_row_key], source_col).font = Font(color="333333")
        if source_row_key != key and key in rows:
            sheet.cell(rows[key], source_col, "由金额自动计算，仅供参考")
            sheet.cell(rows[key], source_col).fill = OUTPUT_FILL
            sheet.cell(rows[key], source_col).font = Font(color="333333")
    scalar_sources = {
        "wacc": "规则预填，可编辑",
        "terminal_growth": "规则预填，可编辑",
        "share_count": _share_count_source_label(financial_assumptions),
    }
    for key, source in scalar_sources.items():
        sheet.cell(rows[key], source_col, source)
        sheet.cell(rows[key], source_col).fill = OUTPUT_FILL
        sheet.cell(rows[key], source_col).font = Font(color="333333")
    for key, value in scalar_assumptions.items():
        sheet.cell(rows[key], 3, value)
    sheet.cell(rows["wacc"], 3).fill = INPUT_FILL
    sheet.cell(rows["wacc"], 3).font = Font(color="8A343B")
    sheet.cell(rows["wacc"], 3).protection = Protection(locked=False)
    sheet.cell(rows["share_count"], 3).fill = OUTPUT_FILL
    sheet.cell(rows["share_count"], 3).font = Font(color="333333")

    base_revenue_curve = list(base_result.get("companyRevenueCny100m") or [])
    amount_curves = dict((financial_assumptions or {}).get("amountCurves") or {})

    for year_idx, _year in enumerate(years):
        col = year_start_col + year_idx
        letter = get_column_letter(col)
        revenue_letter = letter
        sheet.cell(rows["gross_margin"], col, _curve_value(annual_rate_curves["gross_margin"], year_idx))
        sheet.cell(rows["gross_profit"], col, f"={revenue_letter}{revenue_row}*{letter}{rows['gross_margin']}")
        for amount_key, rate_key in (
            ("sales_expense", "sales_expense_rate"),
            ("rd_expense", "rd_expense_rate"),
            ("admin_expense", "admin_expense_rate"),
            ("da", "da_rate"),
            ("capex", "capex_rate"),
        ):
            amount_value = _curve_value(amount_curves.get(amount_key) or [], year_idx)
            if amount_value is None or rate_key in applied_ai_fields:
                base_revenue = _curve_value(base_revenue_curve, year_idx) or 0.0
                amount_value = base_revenue * (_curve_value(annual_rate_curves[rate_key], year_idx) or 0.0)
            if combined_sales_admin and amount_key == "admin_expense":
                sheet.cell(rows[amount_key], col, "=0")
            else:
                sheet.cell(rows[amount_key], col, round(float(amount_value), 6))
            sheet.cell(rows[rate_key], col, f'=IFERROR({letter}{rows[amount_key]}/{revenue_letter}{revenue_row},0)')
        sheet.cell(rows["ebit"], col, f"={letter}{rows['gross_profit']}-{letter}{rows['sales_expense']}-{letter}{rows['rd_expense']}-{letter}{rows['admin_expense']}")
        sheet.cell(rows["tax_rate"], col, _curve_value(annual_rate_curves["tax_rate"], year_idx))
        sheet.cell(rows["tax"], col, f"=MAX({letter}{rows['ebit']},0)*{letter}{rows['tax_rate']}")
        sheet.cell(rows["nopat"], col, f"={letter}{rows['ebit']}-{letter}{rows['tax']}")
        nwc_amount = _curve_value(amount_curves.get("nwc_change") or [], year_idx)
        if nwc_amount is None or "nwc_rate" in applied_ai_fields:
            base_revenue = _curve_value(base_revenue_curve, year_idx) or 0.0
            previous_base_revenue = _curve_value(base_revenue_curve, year_idx - 1) or 0.0 if year_idx > 0 else 0.0
            nwc_amount = (base_revenue - previous_base_revenue) * (_curve_value(annual_rate_curves["nwc_rate"], year_idx) or 0.0)
        sheet.cell(rows["nwc_change"], col, round(float(nwc_amount), 6))
        sheet.cell(rows["fcf"], col, f"={letter}{rows['nopat']}+{letter}{rows['da']}-{letter}{rows['capex']}-{letter}{rows['nwc_change']}")
        sheet.cell(rows["discount_period"], col, year_idx + 0.5)
        if year_idx > 0:
            sheet.cell(rows["wacc"], col, f"=$C${rows['wacc']}")
        sheet.cell(rows["discount_factor"], col, f"=(1+{letter}{rows['wacc']})^{letter}{rows['discount_period']}")
        sheet.cell(rows["pv_fcf"], col, f"={letter}{rows['fcf']}/{letter}{rows['discount_factor']}")

    final_col = get_column_letter(last_year_col)
    sheet.cell(rows["terminal_growth"], 3, scalar_assumptions["terminal_growth"])
    sheet.cell(rows["terminal_growth"], 3).fill = INPUT_FILL
    sheet.cell(rows["terminal_growth"], 3).font = Font(color="8A343B")
    sheet.cell(rows["terminal_growth"], 3).protection = Protection(locked=False)
    sheet.cell(
        rows["terminal_value"],
        3,
        f"={final_col}{rows['fcf']}*(1+$C${rows['terminal_growth']})/($C${rows['wacc']}-$C${rows['terminal_growth']})",
    )
    sheet.cell(rows["pv_terminal_value"], 3, f"=C{rows['terminal_value']}/{final_col}{rows['discount_factor']}")
    sheet.cell(
        rows["enterprise_value"],
        3,
        f"=SUM({get_column_letter(year_start_col)}{rows['pv_fcf']}:{final_col}{rows['pv_fcf']})+C{rows['pv_terminal_value']}",
    )
    equity_formula = f"=C{rows['enterprise_value']}"
    if has_bridge:
        sheet.cell(rows["net_cash"], 3, "='历史财务数据'!B41").fill = OUTPUT_FILL
        sheet.cell(rows["share_count"], 3, "='历史财务数据'!B42/100000000")
        equity_formula += f"+C{rows['net_cash']}"
    sheet.cell(rows["equity_value"], 3, equity_formula)
    sheet.cell(rows["value_per_share"], 3, f'=IF(C{rows["share_count"]}>0,C{rows["equity_value"]}/C{rows["share_count"]},"")')
    _add_cell_comment(sheet.cell(rows["share_count"], 3), _share_count_comment(financial_assumptions))

    input_rows = [
        rows["gross_margin"],
        rows["sales_expense"],
        rows["rd_expense"],
        rows["admin_expense"],
        rows["tax_rate"],
        rows["da"],
        rows["capex"],
        rows["nwc_change"],
        rows["wacc"],
        rows["terminal_growth"],
    ]
    output_rows = [
        rows["gross_profit"],
        rows["ebit"],
        rows["nopat"],
        rows["fcf"],
        rows["pv_fcf"],
        rows["terminal_value"],
        rows["pv_terminal_value"],
        rows["enterprise_value"],
        rows["equity_value"],
        rows["value_per_share"],
    ]
    for row in input_rows:
        sheet.cell(row, 1).fill = SUBSECTION_FILL
        sheet.cell(row, 1).font = Font(bold=True)
    editable_annual_rows = [
        rows["gross_margin"], rows["sales_expense"], rows["rd_expense"],
        rows["admin_expense"],
        rows["tax_rate"], rows["da"], rows["capex"], rows["nwc_change"],
    ]
    _style_assumption_rows(sheet, editable_annual_rows, year_start_col, last_year_col)
    dcf_comments = _dcf_assumption_comments(financial_assumptions)
    for key, comment in dcf_comments.items():
        comment_row_key = amount_row_by_rate.get(key, key)
        if comment_row_key not in rows:
            continue
        if key in annual_rate_curves:
            _add_row_comments(sheet, rows[comment_row_key], year_start_col, last_year_col, comment)
        else:
            _add_cell_comment(sheet.cell(rows[comment_row_key], 3), comment)
    for field, decision in applied_ai_fields.items():
        comment_row_key = amount_row_by_rate.get(field, field)
        if comment_row_key not in rows:
            continue
        ai_comment = "\n".join([
            f"{_research_provider_label(ai_assumptions)}未来曲线已通过硬规则校验并应用。",
            f"置信度：{decision.get('confidence') or '未提供'}",
            f"调整原因：{decision.get('reason') or '未提供'}",
            "历史财务和来源锁定字段未被修改。",
        ])
        _add_row_comments(sheet, rows[comment_row_key], year_start_col, last_year_col, ai_comment)
    financial_review_comment = _format_financial_review(ai_assumptions)
    if financial_review_comment:
        _add_cell_comment(sheet.cell(rows["gross_margin"], year_start_col), financial_review_comment)
    _style_output_rows(sheet, output_rows[:5], year_start_col, last_year_col)
    for row in [rows["terminal_value"], rows["pv_terminal_value"], rows["enterprise_value"], rows["equity_value"], rows["value_per_share"]]:
        sheet.cell(row, 3).fill = OUTPUT_FILL
        sheet.cell(row, 3).font = Font(bold=True)

    _number_format_range(sheet, rows["gross_profit"], year_start_col, rows["fcf"], last_year_col, "#,##0.0")
    _number_format_range(sheet, rows["pv_fcf"], year_start_col, rows["pv_fcf"], last_year_col, "#,##0.0")
    for row in [rows["gross_margin"], rows["sales_expense_rate"], rows["rd_expense_rate"], rows["admin_expense_rate"], rows["tax_rate"], rows["da_rate"], rows["capex_rate"]]:
        _number_format_range(sheet, row, year_start_col, row, last_year_col, "0.0%")
    _number_format_range(sheet, rows["wacc"], 3, rows["wacc"], last_year_col, "0.0%")
    _number_format_range(sheet, rows["terminal_growth"], 3, rows["terminal_growth"], 3, "0.0%")
    _number_format_range(sheet, rows["terminal_value"], 3, rows["equity_value"], 3, "#,##0.0")
    _number_format_range(sheet, rows["value_per_share"], 3, rows["value_per_share"], 3, ONE_DECIMAL_NUMBER_FORMAT)
    return rows


def _write_sensitivity(
    sheet,
    summary_sheet,
    summary_layout: dict[str, Any],
) -> None:
    """Write a formula-driven WACC / terminal-growth sensitivity table."""

    sheet.merge_cells("A1:H1")
    sheet["A1"] = "估值敏感性"
    sheet["A1"].fill = DARK_FILL
    sheet["A1"].font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True, size=16)
    sheet["A1"].alignment = Alignment(horizontal="left", vertical="center")

    sheet.merge_cells("A3:F3")
    sheet["A3"] = "每股价值敏感性（元/股）"
    sheet.merge_cells("G3:H3")
    sheet["G3"] = "当前估值校验"
    for cell in (sheet["A3"], sheet["G3"]):
        cell.fill = SECTION_FILL
        cell.font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="left", vertical="center")

    dcf_rows = summary_layout.get("dcfRows") or {}
    summary_title = str(summary_sheet.title).replace("'", "''")
    summary_ref = f"'{summary_title}'"
    terminal_growth_offsets = [-0.01, -0.005, 0.0, 0.005, 0.01]
    wacc_offsets = [-0.02, -0.01, 0.0, 0.01, 0.02]
    growth_headers = [
        f"={summary_ref}!$C${dcf_rows['terminal_growth']}{offset:+.3%}" if offset else f"={summary_ref}!$C${dcf_rows['terminal_growth']}"
        for offset in terminal_growth_offsets
    ]
    _write_row(sheet, 4, 1, ["WACC / 永续增长率", *growth_headers], header=True)
    for col in range(2, 7):
        sheet.cell(4, col).fill = INPUT_FILL
        sheet.cell(4, col).font = Font(name="SimSun", color="8A343B", bold=True, size=11)
        sheet.cell(4, col).number_format = "0.0%"
        sheet.cell(4, col).protection = Protection(locked=False)

    year_start_col = int(summary_layout.get("yearStartCol") or 3)
    last_year_col = int(summary_layout.get("lastYearCol") or year_start_col)
    first_year_letter = get_column_letter(year_start_col)
    last_year_letter = get_column_letter(last_year_col)
    fcf_row = int(dcf_rows["fcf"])
    discount_period_row = int(dcf_rows["discount_period"])
    share_count_row = int(dcf_rows["share_count"])
    net_cash_term = f"+{summary_ref}!$C${dcf_rows['net_cash']}" if "net_cash" in dcf_rows else ""

    for row_offset, wacc_offset in enumerate(wacc_offsets, start=5):
        sheet.cell(
            row_offset,
            1,
            f"={summary_ref}!$C${dcf_rows['wacc']}{wacc_offset:+.2%}" if wacc_offset else f"={summary_ref}!$C${dcf_rows['wacc']}",
        )
        sheet.cell(row_offset, 1).fill = INPUT_FILL
        sheet.cell(row_offset, 1).font = Font(name="SimSun", color="8A343B", bold=True, size=11)
        sheet.cell(row_offset, 1).number_format = "0.0%"
        sheet.cell(row_offset, 1).protection = Protection(locked=False)
        for col in range(2, 7):
            growth_letter = get_column_letter(col)
            sheet.cell(
                row_offset,
                col,
                (
                    f'=IF(OR($A{row_offset}<={growth_letter}$4,{summary_ref}!$C${share_count_row}<=0),"",'
                    f'(SUMPRODUCT({summary_ref}!${first_year_letter}${fcf_row}:${last_year_letter}${fcf_row},'
                    f'1/(1+$A{row_offset})^{summary_ref}!${first_year_letter}${discount_period_row}:${last_year_letter}${discount_period_row})+'
                    f'{summary_ref}!${last_year_letter}${fcf_row}*(1+{growth_letter}$4)/'
                    f'($A{row_offset}-{growth_letter}$4)/(1+$A{row_offset})^{summary_ref}!${last_year_letter}${discount_period_row}{net_cash_term})/'
                    f'{summary_ref}!$C${share_count_row})'
                ),
            )
            sheet.cell(row_offset, col).number_format = ONE_DECIMAL_NUMBER_FORMAT
            sheet.cell(row_offset, col).fill = OUTPUT_FILL

    side_rows = [
        (4, "WACC", f"={summary_ref}!$C${dcf_rows['wacc']}", "0.0%"),
        (5, "永续增长率", f"={summary_ref}!$C${dcf_rows['terminal_growth']}", "0.0%"),
        (6, "当前每股价值（元/股）", f"={summary_ref}!$C${dcf_rows['value_per_share']}", ONE_DECIMAL_NUMBER_FORMAT),
        (
            7,
            "终值现值占企业价值",
            f'=IF({summary_ref}!$C${dcf_rows["enterprise_value"]}<>0,'
            f'{summary_ref}!$C${dcf_rows["pv_terminal_value"]}/{summary_ref}!$C${dcf_rows["enterprise_value"]},"")',
            "0.0%",
        ),
        (8, "复核阈值", 0.70, "0.0%"),
        (9, "状态", '=IF(H7="","待计算",IF(H7>H8,"建议复核","正常"))', "General"),
    ]
    for row, label, value, number_format in side_rows:
        sheet.cell(row, 7, label)
        sheet.cell(row, 8, value)
        sheet.cell(row, 7).font = Font(name="SimSun", bold=True, size=11)
        sheet.cell(row, 8).number_format = number_format
        if row == 8:
            sheet.cell(row, 8).fill = INPUT_FILL
            sheet.cell(row, 8).font = Font(name="SimSun", color="8A343B", size=11)
            sheet.cell(row, 8).protection = Protection(locked=False)
        else:
            sheet.cell(row, 8).fill = OUTPUT_FILL

    sheet.merge_cells("A12:H12")
    sheet["A12"] = (
        "说明：敏感性结果使用DCF估值页的自由现金流、折现期、净现金和总股本。"
        if net_cash_term else
        "说明：敏感性结果引用DCF估值页自由现金流、折现期和总股本重新计算；当前未作净现金/净债务调整。"
    )
    sheet["A12"].font = Font(name="SimSun", color="666666", size=10)
    sheet["A12"].alignment = Alignment(horizontal="left", vertical="center", wrap_text=False)
    _write_scenario_comparison(sheet, summary_ref, summary_layout)
    sheet.freeze_panes = "B5"


def _write_scenario_comparison(
    sheet,
    summary_ref: str,
    summary_layout: dict[str, Any],
) -> None:
    """Stress-test product revenue around the selected valuation case."""

    years = list(summary_layout.get("years") or [])
    if not years:
        return
    dcf_rows = summary_layout.get("dcfRows") or {}
    year_start_col = int(summary_layout.get("yearStartCol") or 3)
    last_year_col = int(summary_layout.get("lastYearCol") or year_start_col)
    revenue_row = int(summary_layout.get("revenueRow") or 0)
    milestone_row = int(summary_layout.get("milestoneRevenueRow") or 0)

    sheet.merge_cells("A14:H14")
    sheet["A14"] = "产品收入兑现率敏感性"
    sheet["A14"].fill = DARK_FILL
    sheet["A14"].font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True, size=14)
    sheet["A14"].alignment = Alignment(horizontal="left", vertical="center")
    _write_row(sheet, 16, 1, ["指标", "较低兑现", "主情景输入", "较高兑现"], header=True)
    sheet.merge_cells("E16:H16")
    sheet["E16"] = "口径说明"
    sheet["E16"].fill = SECTION_FILL
    sheet["E16"].font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True)
    sheet["E16"].alignment = Alignment(horizontal="left", vertical="center")

    scenario_columns = {2: 0.80, 3: 1.00, 4: 1.20}
    sheet["A17"] = "产品收入兑现率"
    for col, value in scenario_columns.items():
        cell = sheet.cell(17, col, value)
        cell.fill = INPUT_FILL
        cell.font = Font(name="SimSun", color="8A343B", bold=col == 3, size=11)
        cell.number_format = "0.0%"
        cell.protection = Protection(locked=False)

    reference_years = [2030 if 2030 in years else years[len(years) // 2], years[-1]]
    labels = [
        f"{reference_years[0]}E总收入（亿元）",
        f"{reference_years[1]}E总收入（亿元）",
        "预测期FCF现值（亿元）",
        "终值现值（亿元）",
        "企业价值（亿元）",
        "每股价值（元/股）",
        "企业价值较主情景变化",
    ]
    if "net_cash" in dcf_rows:
        labels.append("股权价值（亿元）")
    for row, label in enumerate(labels, start=18):
        sheet.cell(row, 1, label)

    helper_start_row = 28
    scenario_helper_rows: dict[int, tuple[int, int]] = {}
    for scenario_index, scenario_col in enumerate(scenario_columns):
        revenue_helper_start = helper_start_row + scenario_index * (len(years) * 2 + 2)
        fcf_helper_start = revenue_helper_start + len(years)
        scenario_helper_rows[scenario_col] = (revenue_helper_start, fcf_helper_start)
        factor_ref = f"${get_column_letter(scenario_col)}$17"
        for year_index, _year in enumerate(years):
            summary_col = year_start_col + year_index
            summary_letter = get_column_letter(summary_col)
            revenue_helper_row = revenue_helper_start + year_index
            fcf_helper_row = fcf_helper_start + year_index
            revenue_helper_cell = f"$G${revenue_helper_row}"
            base_revenue_ref = f"{summary_ref}!${summary_letter}${revenue_row}"
            milestone_ref = f"{summary_ref}!${summary_letter}${milestone_row}"
            sheet.cell(
                revenue_helper_row,
                7,
                f"=({base_revenue_ref}-{milestone_ref})*{factor_ref}+{milestone_ref}",
            )
            gross_profit_expression = (
                f"{revenue_helper_cell}*"
                f"{summary_ref}!${summary_letter}${dcf_rows['gross_margin']}"
            )
            ebit_expression = (
                f"{gross_profit_expression}"
                f"-{summary_ref}!${summary_letter}${dcf_rows['sales_expense']}"
                f"-{summary_ref}!${summary_letter}${dcf_rows['rd_expense']}"
                f"-{summary_ref}!${summary_letter}${dcf_rows['admin_expense']}"
            )
            sheet.cell(
                fcf_helper_row,
                8,
                (
                    f"={ebit_expression}"
                    f"-MAX({ebit_expression},0)*{summary_ref}!${summary_letter}${dcf_rows['tax_rate']}"
                    f"+{summary_ref}!${summary_letter}${dcf_rows['da']}"
                    f"-{summary_ref}!${summary_letter}${dcf_rows['capex']}"
                    f"-{summary_ref}!${summary_letter}${dcf_rows['nwc_change']}"
                ),
            )
        for hidden_row in range(revenue_helper_start, fcf_helper_start + len(years)):
            sheet.row_dimensions[hidden_row].hidden = True

    last_year_letter = get_column_letter(last_year_col)
    wacc_ref = f"{summary_ref}!$C${dcf_rows['wacc']}"
    growth_ref = f"{summary_ref}!$C${dcf_rows['terminal_growth']}"
    share_count_ref = f"{summary_ref}!$C${dcf_rows['share_count']}"
    for scenario_col in scenario_columns:
        column_letter = get_column_letter(scenario_col)
        revenue_helper_start, fcf_helper_start = scenario_helper_rows[scenario_col]
        first_reference_index = years.index(reference_years[0])
        last_reference_index = years.index(reference_years[1])
        sheet.cell(18, scenario_col, f"=$G${revenue_helper_start + first_reference_index}")
        sheet.cell(19, scenario_col, f"=$G${revenue_helper_start + last_reference_index}")
        pv_terms = []
        for year_index in range(len(years)):
            summary_letter = get_column_letter(year_start_col + year_index)
            pv_terms.append(
                f"$H${fcf_helper_start + year_index}/(1+{wacc_ref})^"
                f"{summary_ref}!${summary_letter}${dcf_rows['discount_period']}"
            )
        sheet.cell(20, scenario_col, "=" + "+".join(pv_terms))
        final_fcf_ref = f"$H${fcf_helper_start + len(years) - 1}"
        sheet.cell(
            21,
            scenario_col,
            (
                f"={final_fcf_ref}*(1+{growth_ref})/({wacc_ref}-{growth_ref})/"
                f"(1+{wacc_ref})^{summary_ref}!${last_year_letter}${dcf_rows['discount_period']}"
            ),
        )
        sheet.cell(22, scenario_col, f"={column_letter}20+{column_letter}21")
        equity_row = 22
        if "net_cash" in dcf_rows:
            equity_row = 25
            sheet.cell(equity_row, scenario_col, f"={column_letter}22+{summary_ref}!$C${dcf_rows['net_cash']}")
        sheet.cell(23, scenario_col, f'=IF({share_count_ref}>0,{column_letter}{equity_row}/{share_count_ref},"")')
        sheet.cell(24, scenario_col, f'=IF($C$22<>0,{column_letter}22/$C$22-1,"")')

    sheet.merge_cells("E17:H17")
    sheet["E17"] = "仅调整产品收入兑现率；里程碑、上市时间、POS、权益、经营及折现假设不变。"
    sheet["E17"].font = Font(name="SimSun", color="666666", size=10)
    sheet["E17"].alignment = Alignment(horizontal="left", vertical="center", wrap_text=False)
    sheet.merge_cells("E20:G20")
    sheet["E20"] = "主情景勾稽差异（亿元）"
    sheet["H20"] = f"=C22-{summary_ref}!$C${dcf_rows['enterprise_value']}"
    sheet.merge_cells("E21:G21")
    sheet["E21"] = "状态"
    sheet["H21"] = '=IF(ABS(H20)<=0.01,"正常","建议复核")'
    sheet["H20"].number_format = ONE_DECIMAL_NUMBER_FORMAT
    for cell in (sheet["E20"], sheet["E21"]):
        cell.font = Font(name="SimSun", color="000000", bold=True, size=11)
    for cell in (sheet["H20"], sheet["H21"]):
        cell.fill = OUTPUT_FILL
        cell.font = Font(name="SimSun", color="000000", size=11)
    for row in range(18, 24):
        _number_format_range(sheet, row, 2, row, 4, ONE_DECIMAL_NUMBER_FORMAT)
    _number_format_range(sheet, 24, 2, 24, 4, "0.0%")
    _style_output_rows(sheet, list(range(18, 18 + len(labels))), 2, 4)


def _finalize_sensitivity_sheet(sheet) -> None:
    """Restore the base-case emphasis after workbook-wide border formatting."""

    cell = sheet["D7"]
    cell.font = Font(name="SimSun", color="7D3038", bold=True, size=11)
    cell.border = Border(
        left=Side(style="medium", color="A64045"),
        right=Side(style="medium", color="A64045"),
        top=Side(style="medium", color="A64045"),
        bottom=Side(style="medium", color="A64045"),
    )
    sheet["H9"].number_format = "General"
    for row in range(17, 25):
        sheet.cell(row, 3).border = Border(
            left=Side(style="medium", color="A64045"),
            right=Side(style="medium", color="A64045"),
            top=Side(style="medium", color="A64045") if row == 17 else Side(style="thin", color="E4D0CE"),
            bottom=Side(style="medium", color="A64045") if row == 24 else Side(style="thin", color="E4D0CE"),
        )
        if row >= 18:
            sheet.cell(row, 3).font = Font(name="SimSun", color="7D3038", bold=True, size=11)
    sheet.freeze_panes = "B5"


def _write_charts(sheet, summary_sheet, years: list[int], product_total_rows: list[dict[str, Any]]) -> None:
    year_start_col = 3
    last_year_col = year_start_col + len(years) - 1
    header_row = SUMMARY_HEADER_ROW
    revenue_row = header_row + 1

    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=10)
    sheet["A1"] = "收入趋势图"
    sheet["A1"].fill = DARK_FILL
    sheet["A1"].font = Font(color="FFFFFF", bold=True, size=16)

    sheet["A3"] = "总收入预测图"
    sheet["A3"].fill = SUBSECTION_FILL
    sheet["A3"].font = Font(bold=True)

    chart = BarChart()
    chart.title = "总收入预测"
    chart.y_axis.title = "亿元"
    chart.x_axis.title = "年份"
    chart.x_axis.delete = False
    chart.x_axis.tickLblPos = "low"
    chart.x_axis.majorTickMark = "out"
    chart.y_axis.delete = False
    chart.y_axis.tickLblPos = "nextTo"
    chart.y_axis.majorTickMark = "out"
    chart.y_axis.numFmt = "0.0"
    data = Reference(summary_sheet, min_col=year_start_col, min_row=revenue_row, max_col=last_year_col, max_row=revenue_row)
    cats = Reference(summary_sheet, min_col=year_start_col, min_row=header_row, max_col=last_year_col, max_row=header_row)
    chart.add_data(data, from_rows=True, titles_from_data=False)
    chart.set_categories(cats)
    if chart.series:
        chart.series[0].graphicalProperties.solidFill = "A64045"
        chart.series[0].graphicalProperties.line.solidFill = "7D3038"
    chart.legend = None
    chart.graphical_properties = GraphicalProperties(noFill=True)
    chart.graphical_properties.line.noFill = True
    chart.plot_area.graphicalProperties = GraphicalProperties(noFill=True)
    chart.plot_area.graphicalProperties.line.noFill = True
    chart.height = 9
    chart.width = 18
    sheet.add_chart(chart, "A5")


def _write_assumption_basis(
    sheet,
    valuation: dict[str, Any],
    draft: dict[str, Any],
    years: list[int],
    financial_assumptions: dict[str, Any] | None,
    ai_assumptions: dict[str, Any] | None,
) -> None:
    """Write a concise external-facing assumption summary without model IP details."""
    last_col = max(4, len(years) + 2)
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    sheet["A1"] = "假设依据"
    sheet["A1"].fill = DARK_FILL
    sheet["A1"].font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True, size=16)
    _write_row(sheet, 3, 1, ["项目", "说明"], header=True)
    concise_rows = [
        ["产品与适应症", "来自 DrugAlpha 已发布数据及估值跟踪。"],
        ["患者规模", "参考 DrugAlpha 疾病知识库及公开资料；可在淡粉单元格调整。"],
        ["目标治疗人群", "结合适应症、Biomarker及目标患者范围预填；建议复核。"],
        ["治疗费用", "参考同类药物及市场价格；可调整。"],
        ["预计用药时长", "肿瘤领域参考同类药物PFS，其他领域参考常见疗程；可调整。"],
        ["上市概率与权益", "参考研发阶段、估值跟踪及产品权益安排；可调整。"],
        ["历史财务", "优先采用 iFinD QuantAPI / THS_BD，缺失时使用既定备选来源。"],
        ["公司经营假设", "基于历史财务及合理性复核生成；DCF估值页中可直接调整。"],
        ["免责声明", valuation.get("disclaimer") or draft.get("disclaimer") or AI_MODEL_DISCLAIMER],
    ]
    if (ai_assumptions or {}).get("researchProvider") == "user_agent":
        concise_rows.extend([
            ["预填执行方", "用户agent；平台未调用AI API，建议及来源需人工复核。"],
            ["财务复核说明", ((ai_assumptions or {}).get("financialReview") or {}).get("basisComment") or "未提供"],
        ])
    for product in draft.get("products") or []:
        selection = product.get("valuation_indication_selection") or {}
        if selection.get("needsHumanReview"):
            qualification = ("同靶点参考形成的估值假设，不代表本药已确认开展该适应症，需人工复核"
                if selection.get("evidencePriority") in ("same_target_clinical", "same_target_public")
                else "估值使用的适应症选择需人工复核，不替代本药研发资料")
            concise_rows.append(["待复核的适应症估值假设", "；".join(str(part) for part in (
                product.get("drug_name"), f"原始管线：{selection.get('parentIndication') or '未提供'}",
                f"估值假设：{selection.get('selectedIndication') or '未提供'}",
                f"可信度：{selection.get('confidence') or '未提供'}", qualification,
                selection.get("basisComment")) if part)])
    for row_idx, values in enumerate(concise_rows, start=4):
        _write_row(sheet, row_idx, 1, values)
        sheet.cell(row_idx, 2).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)


def _write_financial_reconciliation(sheet, assumptions: dict[str, Any]) -> None:
    bridge = assumptions.get("equityBridge")
    if bridge:
        _write_row(sheet, 35, 1, ["净现金与股本", "金额（亿元）/ 股数", "原始来源"], header=True)
        _write_row(sheet, 36, 1, ["估值基准日", date.fromisoformat(bridge["as_of"]), "首个完整预测年度的期初"])
        sheet["B36"].number_format = "yyyy-mm-dd"
        for row, label, key in (
            (37, "现金及现金等价物", "cash_equivalents"),
            (38, "可计入的定期存款", "eligible_deposits"),
            (39, "减：有息借款", "interest_bearing_debt"),
            (40, "减：其他已核实债务性项目", "other_debt_like"),
            (42, "总股本（股）", "shares_outstanding"),
        ):
            source = bridge["sources"].get(key)
            _write_row(sheet, row, 1, [label, bridge[key] if source else None])
            if source and source.startswith("https://"):
                sheet.cell(row, 3, "打开原始披露").hyperlink = source
            elif source:
                sheet.cell(row, 3, source)
            sheet.cell(row, 2).fill = INPUT_FILL
        _write_row(sheet, 41, 1, ["净现金（亿元）", "=SUM(B37:B38)-SUM(B39:B40)"])
        sheet["B41"].fill = OUTPUT_FILL
        if bridge.get("note"):
            _write_row(sheet, 43, 1, ["净现金口径", bridge["note"]])
            sheet.merge_cells("B43:F43")
            sheet["B43"].alignment = Alignment(wrap_text=True, vertical="center")
            sheet.row_dimensions[43].height = 30
    audits = assumptions.get("expenseScopeAudit") or []
    if audits:
        _write_row(sheet, 45, 1, ["费用口径复核", "费用字段", "供应商金额", "扣除重复研发",
                                "独立费用口径", "数据来源"], header=True)
        for row, item in enumerate(audits, start=46):
            label = "管理费用" if item["field"] == "admin_expense" else "销售及管理费用"
            _write_row(sheet, row, 1, [item["year"], label, item["reported"], item["rd"],
                                      f"=C{row}-D{row}", item["source"]])


def _write_historical_financials_sheet(
    sheet,
    valuation: dict[str, Any],
    forecast_years: list[int],
    financial_assumptions: dict[str, Any] | None,
) -> None:
    """Write the three annual financial periods immediately before the forecast.

    ``historicalRows`` is produced by ``prepare_export_financials.py`` from
    iFinD first, with any field-level fallback preserved in ``field_sources``.
    Showing it separately makes the DCF's financial starting point auditable
    without presenting internal extrapolation rules in the operating sheets.
    """

    assumptions = financial_assumptions or {}
    source_status = assumptions.get("sourceStatus") or {}
    all_history = sorted(
        (row for row in (assumptions.get("historicalRows") or []) if row.get("year")),
        key=lambda row: int(row.get("year") or 0),
    )
    first_forecast_year = min((int(year) for year in forecast_years), default=None)
    requested_years = (
        list(range(first_forecast_year - 3, first_forecast_year))
        if first_forecast_year is not None
        else [int(row.get("year")) for row in all_history[-3:]]
    )
    history_by_year = {int(row.get("year")): row for row in all_history}
    history = [history_by_year.get(year, {"year": year}) for year in requested_years]

    last_col = 3 + len(requested_years)
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    sheet["A1"] = f"{valuation.get('companyName') or ''}－历史财务数据"
    sheet["A1"].fill = DARK_FILL
    sheet["A1"].font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True, size=16)
    sheet["A1"].alignment = Alignment(horizontal="left", vertical="center")

    provider = str(source_status.get("historyProvider") or "iFinD QuantAPI / THS_BD")
    _write_row(sheet, 3, 1, ["证券代码", source_status.get("securityCode") or "-", "财务数据来源", provider])
    _write_row(
        sheet,
        4,
        1,
        [
            "取数口径",
            "预测起始年前三个完整年度。各字段按实际来源标注，缺失历史数据留空。"
            if source_status.get("mcpHistory") else
            "预测起始年前连续三个完整年度；iFinD QuantAPI / THS_BD 优先，字段缺失时保留已标注的备选来源。",
        ],
    )
    sheet.merge_cells(start_row=4, start_column=2, end_row=4, end_column=last_col)

    fields = [
        ("revenue", "营业收入（亿元）", "prime_oi"),
        ("gross_profit", "毛利（亿元）", "gross_profit"),
        ("cost", "营业成本（亿元）", "sales_cost"),
        ("sales_expense", "销售费用（亿元）", "sales_fee"),
        ("rd_expense", "研发费用（亿元）", "rad_cost"),
        ("admin_expense", "管理费用（亿元）", "manage_fee"),
        ("sales_admin_expense", "销售及管理费用合计（亿元）", "sales_ad_and_ga"),
        ("financial_expense", "财务费用（亿元）", "financial_expense"),
        ("cash_equivalents", "现金及现金等价物（亿元）", "cce"),
        ("profit_before_tax", "税前利润（亿元）", "profit_before_tax"),
        ("income_tax", "所得税（亿元）", "income_tax"),
        ("net_income_parent", "归母净利润（亿元）", "ni_attr_to_cs"),
        ("depreciation_amortization", "折旧摊销（亿元）", "实际采用字段见说明"),
        ("accumulated_amortization", "累计摊销（亿元；仅审计）", "accum_amortized"),
        ("capital_cost_to_daa_reported", "资本开支/折旧摊销（iFinD原始值；仅审计）", "capital_cost_to_daa"),
        ("capex_to_da_calculated_rate", "资本开支/折旧摊销（按金额重算）", "资本开支/实际采用折旧摊销"),
        ("capex", "资本开支（亿元）", "capital_cost"),
        ("operating_capital", "营运资本（亿元）", "operating_capital"),
        ("operating_capital_change", "营运资本变动（亿元）", "operating_capital_change"),
        ("operating_cash_flow", "经营现金流（亿元）", "ncf_from_oa"),
        ("gross_margin_rate", "毛利率", "(收入-成本)/收入"),
        ("sales_expense_rate", "销售费用率", "销售费用/收入"),
        ("rd_expense_rate", "研发费用率", "研发费用/收入"),
        ("admin_expense_rate", "管理费用率", "管理费用/收入"),
        ("da_rate", "折旧摊销/收入", "折旧摊销/收入"),
    ]
    header_row = 6
    _write_row(
        sheet,
        header_row,
        1,
        ["指标", "数据字段/口径", *[f"{year}A" for year in requested_years], "来源/说明"],
        header=True,
    )
    for row_idx, (key, label, metric) in enumerate(fields, start=header_row + 1):
        values = [_history_value(row, key) for row in history]
        if key == "depreciation_amortization":
            note = _history_depreciation_source_note(history)
        elif key.endswith("_rate"):
            note = "按该年度历史财务数据计算。"
        else:
            note = _history_source_note(history, key)
        _write_row(sheet, row_idx, 1, [label, metric, *values, note])
        if key.endswith("_rate"):
            _number_format_range(sheet, row_idx, 3, row_idx, 2 + len(requested_years), "0.0%")
        else:
            _number_format_range(sheet, row_idx, 3, row_idx, 2 + len(requested_years), "#,##0.0")


def _write_assumption_basis_detailed(
    sheet,
    valuation: dict[str, Any],
    draft: dict[str, Any],
    years: list[int],
    financial_assumptions: dict[str, Any] | None,
    ai_assumptions: dict[str, Any] | None,
) -> None:
    ai_lookup = _ai_proposal_lookup(ai_assumptions)
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(7, len(years) + 2))
    sheet["A1"] = "假设依据"
    sheet["A1"].fill = DARK_FILL
    sheet["A1"].font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True, size=16)

    row = 3
    sheet.cell(row, 1, "一、历史财务数据（用于费用率/费用绝对值假设）")
    sheet.cell(row, 1).fill = SECTION_FILL
    sheet.cell(row, 1).font = Font(color="FFFFFF", bold=True)
    row += 1

    history = (financial_assumptions or {}).get("historicalRows") or []
    combined_sales_admin = (financial_assumptions or {}).get("operatingExpenseMode") == "combined_sales_admin"
    historical_fields = [
        ("revenue", "收入/营运收入（亿元）", "prime_oi"),
        ("gross_profit", "毛利（亿元）", "gross_profit"),
        ("net_income_parent", "归母净利润（亿元）", "ni_attr_to_cs"),
        ("cost", "销售成本（亿元）", "sales_cost"),
        ("sales_expense", "销售费用（未单独披露）" if combined_sales_admin else "销售费用（亿元）", "sales_fee"),
        ("rd_expense", "研发费用（亿元）", "rad_cost"),
        ("admin_expense", "管理费用披露值（可能为合计口径）" if combined_sales_admin else "管理费用/行政开支（亿元）", "manage_fee"),
        ("sales_admin_expense", "销售及管理费用合计（亿元）", "sales_ad_and_ga"),
        ("income_tax", "所得税（亿元）", "income_tax"),
        ("profit_before_tax", "税前利润（亿元）", "profit_before_tax"),
        ("financial_expense", "财务费用（亿元）", "financial_expense"),
        ("depreciation_amortization", "折旧摊销（亿元）", "实际采用字段见说明"),
        ("accumulated_amortization", "累计摊销（亿元；仅审计，不进入DCF）", "accum_amortized"),
        ("capital_cost_to_daa_reported", "资本支出/折旧摊销（iFinD原始值；仅审计）", "capital_cost_to_daa"),
        ("capex_to_da_calculated_rate", "资本支出/折旧摊销（按金额重算）", "capital_cost/实际采用折旧摊销"),
        ("capex", "资本性支出（亿元）", "capital_cost"),
        ("operating_capital", "营运资本（亿元）", "operating_capital"),
        ("operating_capital_change", "营运资本变动（亿元）", "operating_capital_change"),
        ("operating_cash_flow", "经营现金流（亿元）", "ncf_from_oa"),
        ("gross_margin_rate", "毛利率", "(收入-销售成本)/收入"),
        ("sales_expense_rate", "销售费用率（未单独披露）" if combined_sales_admin else "销售费用率", "销售费用/收入"),
        ("sales_admin_expense_rate", "销售及管理费用率（合计口径）", "销售及管理费用合计/收入"),
        ("rd_expense_rate", "研发费用率", "研发费用/收入"),
        ("admin_expense_rate", "行政开支率", "行政开支/收入"),
        ("da_rate", "折旧摊销/收入", "折旧摊销/收入"),
    ]
    history_years = [item.get("year") for item in history]
    _write_row(sheet, row, 1, ["项目", "数据字段/计算", *history_years, "说明"], header=True)
    row += 1
    for key, label, source in historical_fields:
        values = [_history_value(item, key) for item in history]
        if combined_sales_admin and key == "sales_expense" and not any(item.get(key) is not None for item in history):
            note = "iFinD 返回空值；采用 sales_ad_and_ga 合计口径，不拆分。"
        elif key == "depreciation_amortization":
            note = _history_depreciation_source_note(history)
        elif key in {"gross_margin_rate", "sales_expense_rate", "sales_admin_expense_rate", "rd_expense_rate", "admin_expense_rate", "da_rate", "capex_to_da_calculated_rate"}:
            note = "由历史财务计算。"
        else:
            note = _history_source_note(history, key)
        _write_row(sheet, row, 1, [label, source, *values, note])
        if key.endswith("_rate"):
            _number_format_range(sheet, row, 3, row, 2 + len(history_years), "0.0%")
        else:
            _number_format_range(sheet, row, 3, row, 2 + len(history_years), "#,##0.0")
        row += 1

    row += 2
    sheet.cell(row, 1, "二、经营假设曲线（金额写入DCF估值页，比例自动计算）")
    sheet.cell(row, 1).fill = SECTION_FILL
    sheet.cell(row, 1).font = Font(color="FFFFFF", bold=True)
    row += 1
    curve_fields = [
        ("gross_margin", "毛利率", "以最近一年毛利率为起点，向成熟毛利率缓慢过渡。"),
        (
            "sales_expense_rate",
            "销售费用率",
            "仅披露销售及管理费用合计时，合计金额暂预填至销售费用；管理费用预填 0，两个金额均可编辑，比例自动计算。" if combined_sales_admin else "以销售费用绝对值为基数按 12% 增长外推；金额可编辑，比例自动计算。",
        ),
        ("rd_expense_rate", "研发费用率", "以研发费用绝对值为基数按 10% 增长外推；金额可编辑，比例自动计算。"),
        ("admin_expense_rate", "管理费用率", "仅披露销售及管理费用合计时，管理费用初始预填 0，避免重复扣减；金额可编辑，比例自动计算。" if combined_sales_admin else "以行政开支绝对值为基数按 8% 增长外推；金额可编辑，比例自动计算。"),
        ("tax_rate", "所得税率", "亏损期不按历史有效税率外推，逐步过渡至成熟税率 15%。"),
        ("da_rate", "折旧摊销/收入", "以折旧摊销绝对值为基数按 3% 增长外推；金额可编辑，比例自动计算。"),
        ("capex_rate", "资本开支/收入", "优先按历史资本开支绝对值外推；金额可编辑，比例自动计算；缺失时回退为 0。"),
    ]
    _write_row(sheet, row, 1, ["项目", "方法", *[f"{year}E" for year in years]], header=True)
    row += 1
    curves = (financial_assumptions or {}).get("curves") or {}
    for key, label, method in curve_fields:
        values = [_curve_value(curves.get(key) or [], idx) for idx, _year in enumerate(years)]
        _write_row(sheet, row, 1, [label, method, *values])
        _number_format_range(sheet, row, 3, row, 2 + len(years), "0.0%")
        row += 1

    row += 2
    sheet.cell(row, 1, "三、AI未来财务曲线调整审计")
    sheet.cell(row, 1).fill = SECTION_FILL
    sheet.cell(row, 1).font = Font(color="FFFFFF", bold=True)
    row += 1
    adjustment = (ai_assumptions or {}).get("financialAdjustment") or {}
    decisions = adjustment.get("decisions") or []
    _write_row(sheet, row, 1, ["项目", "校验结果 / 调整依据", *[f"{year}E" for year in years]], header=True)
    row += 1
    if decisions:
        for decision in decisions:
            field = str(decision.get("field") or "")
            status = decision.get("status") or "rejected"
            errors = "；".join(str(item) for item in decision.get("validationErrors") or [])
            note = " | ".join(part for part in [
                "已应用" if status == "applied" else "未应用",
                f"置信度 {decision.get('confidence') or 'low'}",
                str(decision.get("reason") or "").strip(),
                errors,
            ] if part)
            values = decision.get("appliedCurve") if status == "applied" else decision.get("suggestedCurve")
            _write_row(sheet, row, 1, [f"{_financial_curve_label(field)}（AI{'采用' if status == 'applied' else '未采用'}）", note, *(values or [])])
            _number_format_range(sheet, row, 3, row, 2 + len(years), "0.0%")
            row += 1
    else:
        _write_row(sheet, row, 1, ["调整状态", f"{_research_provider_label(ai_assumptions)}未给出可校验的未来曲线；继续采用历史规则曲线。"])
        row += 1

    row += 2
    sheet.cell(row, 1, "四、股本口径")
    sheet.cell(row, 1).fill = SECTION_FILL
    sheet.cell(row, 1).font = Font(color="FFFFFF", bold=True)
    row += 1
    _write_row(sheet, row, 1, ["项目", "数值 / 状态", "说明"], header=True)
    row += 1
    for label, value, note in _equity_basis_rows(financial_assumptions):
        _write_row(sheet, row, 1, [label, value, note])
        _number_format_range(sheet, row, 2, row, 2, "#,##0.0")
        row += 1

    row += 2
    sheet.cell(row, 1, f"五、{_research_provider_label(ai_assumptions)} 财务假设复核与调整说明")
    sheet.cell(row, 1).fill = SECTION_FILL
    sheet.cell(row, 1).font = Font(color="FFFFFF", bold=True)
    row += 1
    financial_review = (ai_assumptions or {}).get("financialReview") or {}
    if financial_review:
        _write_row(sheet, row, 1, ["复核模式", financial_review.get("aiMode") or "ai_check_only"])
        row += 1
        _write_row(sheet, row, 1, ["置信度", financial_review.get("confidence") or "未提供"])
        row += 1
        _write_row(sheet, row, 1, ["复核结论", financial_review.get("basisComment") or ""])
        row += 1
        for check in _clean_financial_review_checks(financial_review.get("checks")):
            _write_row(sheet, row, 1, ["待确认项", str(check)])
            row += 1
    else:
        _write_row(sheet, row, 1, ["复核状态", f"{_research_provider_label(ai_assumptions)} 本次不可用或未运行；历史财务规则曲线仍已写入，不影响导出。"])
        row += 1

    row += 2
    sheet.cell(row, 1, "六、产品收入假设来源")
    sheet.cell(row, 1).fill = SECTION_FILL
    sheet.cell(row, 1).font = Font(color="FFFFFF", bold=True)
    row += 1
    source_rows = [
        ["资产纳入范围", "仅纳入估值跟踪中includedInValuation=true且至少一个市场峰值销售大于0的药物；没有有效峰值销售的产品不进入DCF，也不在Excel中出现。上游管线成熟阶段仅用于上市时间和POS，不再单独触发纳入。"],
        ["其他药物简化收入", "PD-1/PD-L1单抗、生物类似药及指定产品名单不做适应症患者流。统一进入“其他药物”sheet：有规范化单品历史销售时优先衔接实际值；当前缺失时以产品级峰值锚点与上市时间反推。首个有效预测年收入和后续逐年增速可编辑，收入不超过风险/权益调整后的峰值上限。公司层面iFinD营业收入不会被当作单品销售额。"],
        ["无成熟适应症的估值资产", "由公开网页检索结果与 DrugAlpha AI 选择一个具体瘤种作为峰值销售测算基准；实体瘤等篮子试验总称仅作为原始父行，不能直接进入估值。国内/海外共用该具体瘤种，仅市场锚点及权益参数不同；选择、上市年和成功率均需人工复核。"],
        ["产品/适应症", "来自 DrugAlpha pipeline_tracking published 稳定数据；同一药物下按标准化适应症合并，多个临床试验仅作为证据，不重复形成收入测算。"],
        ["中国预计上市年", "中国/美国/欧盟等区域上市标签直接读取实际statusStartDate；准备NDA、preNDA、ANDA与正式NDA/BLA均按阶段开始时间+1年，3期/注册性临床+3年，普通2期+5年；撤回NDA不按有效申报处理。缺少开始时间时才采用阶段规则或DrugAlpha AI可编辑建议。"],
        ["海外预计上市年", "与国内分开推算。优先读取适应症登记号含NCT的海外临床，并按NDA/BLA +1年、3期/注册性临床 +3年、2期 +5年推算；没有可用NCT临床时，按该药国内最高开发阶段的最早开始时间 +4年。"],
        ["销售爬坡/峰值年", "国内默认10%/30%/55%/75%/90%/100%，海外默认8%/25%/50%/70%/85%/100%；峰值年默认上市年+7年。峰值年、六档爬坡比例、峰值后平台期和自然降幅均可在单药sheet修改；默认峰值年及后1年维持100%，随后每年下降3%，本版不含专利悬崖。"],
        ["患者规模/流行病学", "优先来自 DrugAlpha disease-knowledge；缺失时依次采用 DrugAlpha AI 建议与通用规则预填，不允许商业化资产留空。"],
        ["峰值销售锚点", "来自 DrugAlpha valuation_tracking 国内/海外峰值销售假设；同一药物存在多个适应症时，仅按各适应症峰值年市场空间权重分配。市场空间不乘研发阶段或上市成功率，避免重复折价。"],
        ["市场空间/渗透率", "市场空间=峰值年患者规模×Biomarker比例×目标治疗人群比例×治疗费用；分配后的峰值销售用于反推产品渗透率。研发阶段仅在上市成功率和风险调整收入中体现。"],
        ["上市成功率（POS）", "国内按阶段固定：区域上市/已上市100%，正式NDA/BLA及准备NDA、preNDA、ANDA为95%，3期90%，2期80%；撤回NDA不按有效申报处理。海外优先读取估值跟踪successProbability，缺失时才按国内阶段表回退。"],
        ["国内权益比例", "优先读取估值跟踪 domestic.equityRatio。国内风险调整收入=销售额×国内POS×权益比例；字段缺失时暂按100%并明确标注，不影响估值跟踪原始数据。"],
        ["多适应症收入重叠", "患者流产品的估值跟踪峰值为产品级POS前锚点。各适应症销售额以按市场空间分配后的峰值为上限，因此同一市场的适应症上限合计不超过产品峰值；其他药物直接按产品级峰值建模，不做适应症分配。"],
        ["免责声明", valuation.get("disclaimer") or draft.get("disclaimer") or AI_MODEL_DISCLAIMER],
    ]
    for source_row in source_rows:
        _write_row(sheet, row, 1, source_row)
        row += 1
    for product in draft.get("products") or []:
        selection = product.get("valuation_indication_selection") or {}
        if not selection:
            continue
        _write_row(sheet, row, 1, [
            f"{product.get('drug_name') or '未命名药物'} 早期适应症选择",
            " | ".join(part for part in [
                f"原始口径 {selection.get('parentIndication') or '未提供'}",
                f"测算瘤种 {selection.get('selectedIndication') or '待确认'}",
                f"阶段 {selection.get('selectedPhase') or '未提供'}",
                f"上市年 {selection.get('suggestedLaunchYear') or '待确认'}",
                f"峰值年 {selection.get('suggestedPeakYear') or '待确认'}",
                f"DrugAlpha AI原始POS建议（未采用） {selection.get('suggestedPos') if selection.get('suggestedPos') is not None else '未提供'}",
                f"置信度 {selection.get('confidence') or 'low'}",
                str(selection.get("basisComment") or ""),
            ] if part),
        ])
        row += 1

    row += 1
    _write_row(
        sheet,
        row,
        1,
        ["药物", "合并后适应症", "代表阶段/开始时间", "合并记录数", "全部登记号", "临床证据/代表记录选择依据"],
        header=True,
    )
    row += 1
    for product in draft.get("products") or []:
        if product.get("modeling_mode") == "simplified_growth":
            continue
        for indication in product.get("indications") or []:
            evidence = indication.get("trial_evidence") or []
            _write_row(
                sheet,
                row,
                1,
                [
                    product.get("drug_name"),
                    indication.get("indication_text"),
                    " / ".join(
                        part
                        for part in [
                            str(indication.get("phase") or ""),
                            str(indication.get("status_start_date") or ""),
                        ]
                        if part
                    ),
                    indication.get("merged_trial_count") or max(len(evidence), 1),
                    "；".join(str(item) for item in indication.get("trial_reg_nos") or []),
                    _trial_evidence_summary(indication),
                ],
            )
            sheet.cell(row, 6).alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            sheet.row_dimensions[row].height = max(36, min(90, 22 + len(evidence) * 14))
            row += 1

    row += 2
    sheet.cell(row, 1, "七、目标治疗人群比例简化规则表")
    sheet.cell(row, 1).fill = SECTION_FILL
    sheet.cell(row, 1).font = Font(color="FFFFFF", bold=True)
    row += 1
    _write_row(sheet, row, 1, ["等级", "治疗状态/适用说明/示例", "固定比例"], header=True)
    row += 1
    for rule in sorted(treatment_status_rules(), key=lambda item: str(item.get("ruleId") or "")):
        rule_note = "｜".join(
            part
            for part in [
                str(rule.get("label") or ""),
                str(rule.get("description") or ""),
                f"肿瘤示例：{'；'.join(str(item) for item in rule.get('oncologyExamples') or [])}",
                f"非肿瘤示例：{'；'.join(str(item) for item in rule.get('nonOncologyExamples') or [])}",
            ]
            if part
        )
        _write_row(
            sheet,
            row,
            1,
            [rule.get("ruleId"), rule_note, rule.get("ratio")],
        )
        _number_format_range(sheet, row, 3, row, 3, "0.0%")
        row += 1

    row += 1
    _write_row(sheet, row, 1, ["自免疾病严重度证据表（精确匹配时直接预填）", "比例口径/研究人群/定义", "地区", "来源"], header=True)
    sheet.merge_cells(start_row=row, start_column=4, end_row=row, end_column=max(4, len(years) + 2))
    row += 1
    for record in autoimmune_severity_ratio_records():
        ratios = record.get("ratios") or {}
        ratio_text = "；".join(
            f"{key}={float(value):.1%}"
            for key, value in ratios.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        _write_row(
            sheet,
            row,
            1,
            [
                f"{record.get('diseaseKey') or ''}｜{'/'.join(str(item) for item in (record.get('aliases') or [])[:2])}",
                "｜".join(part for part in [ratio_text, str(record.get("denominator") or ""), str(record.get("severityDefinition") or "")] if part),
                str(record.get("market") or ""),
                "｜".join(part for part in [str(record.get("sourceTitle") or ""), str(record.get("sourceUrl") or "")] if part),
            ],
        )
        sheet.merge_cells(start_row=row, start_column=4, end_row=row, end_column=max(4, len(years) + 2))
        sheet.cell(row, 4).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        sheet.row_dimensions[row].height = 58
        row += 1

    row += 1
    _write_row(sheet, row, 1, ["本次药物", "适应症/市场/匹配依据/Biomarker", "匹配等级", "目标治疗人群比例"], header=True)
    row += 1
    for product in draft.get("products") or []:
        if product.get("modeling_mode") == "simplified_growth":
            continue
        for indication in product.get("indications") or []:
            for market in indication.get("markets") or []:
                flow = market.get("patient_flow") or {}
                target_ratio_proposal = _find_ai_proposal(
                    ai_lookup,
                    product,
                    indication,
                    market,
                    "target_treatment_ratio",
                )
                ai_target_ratio = _proposal_scalar_value(target_ratio_proposal)
                effective_target_ratio = (
                    ai_target_ratio
                    if ai_target_ratio is not None
                    else flow.get("target_treatment_ratio")
                )
                if (
                    str(flow.get("treatment_status_rule_id") or "T0") == "T0"
                    and not flow.get("biomarker_ratio_requires_review")
                ):
                    continue
                applied_note = "｜".join(
                    part
                    for part in [
                        str(indication.get("indication_text") or ""),
                        _market_summary_label(str(market.get("market") or "china")),
                        str(flow.get("treatment_status_rule_reason") or ""),
                        f"比例来源 {flow.get('target_treatment_ratio_source') or ''}",
                        f"DrugAlpha AI联网依据 {target_ratio_proposal.get('basisComment') or ''}" if target_ratio_proposal else "",
                        f"Biomarker {_bounded_ratio(flow.get('biomarker_ratio'), default=1.0):.1%}：{flow.get('biomarker_ratio_source') or ''}",
                    ]
                    if part
                )
                _write_row(
                    sheet,
                    row,
                    1,
                    [
                        product.get("drug_name"),
                        applied_note,
                        f"{flow.get('treatment_status_rule_id') or 'T0'}｜{flow.get('treatment_status_rule_label') or ''}",
                        effective_target_ratio,
                    ],
                )
                _number_format_range(sheet, row, 4, row, 4, "0.0%")
                row += 1

    row += 2
    sheet.cell(row, 1, "八、治疗时长/PFS参考")
    sheet.cell(row, 1).fill = SECTION_FILL
    sheet.cell(row, 1).font = Font(color="FFFFFF", bold=True)
    row += 1
    _write_row(
        sheet,
        row,
        1,
        ["药物", "适应症/来源说明", "费用分类", "费用单位", "疗程/PFS采用模式", "参考mPFS（月）", "治疗时长/计费系数", "临床/公开来源记录"],
        header=True,
    )
    row += 1
    written_treatment_rows: set[tuple[str, str, str, Any, Any]] = set()
    for product in draft.get("products") or []:
        if product.get("modeling_mode") == "simplified_growth":
            continue
        for indication in product.get("indications") or []:
            for market in indication.get("markets") or []:
                flow = market.get("patient_flow") or {}
                reference_pfs = flow.get("treatment_months_reference_pfs")
                annual_months = _first_numeric_value(flow.get("treatment_months"))
                row_key = (
                    str(product.get("drug_name") or ""),
                    str(indication.get("indication_text") or ""),
                    str(flow.get("treatment_months_mode") or ""),
                    reference_pfs,
                    annual_months,
                )
                if row_key in written_treatment_rows:
                    continue
                written_treatment_rows.add(row_key)
                references = flow.get("treatment_months_references") or []
                reference_text = "\n".join(_treatment_reference_text(item) for item in references[:5])
                _write_row(
                    sheet,
                    row,
                    1,
                    [
                        product.get("drug_name"),
                        "｜".join(
                            part
                            for part in [
                                str(indication.get("indication_text") or ""),
                                str(flow.get("treatment_months_source_detail") or ""),
                            ]
                            if part
                        ),
                        flow.get("drug_cost_category_label") or flow.get("drug_cost_category"),
                        "单次/疗程费用" if flow.get("price_basis") == "per_treatment" else "月治疗费用",
                        _treatment_months_mode_label(flow),
                        reference_pfs,
                        annual_months,
                        reference_text,
                    ],
                )
                _number_format_range(sheet, row, 6, row, 7, "0.0")
                sheet.cell(row, 8).alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
                row += 1


def _write_sources(
    sheet,
    valuation: dict[str, Any],
    draft: dict[str, Any],
    financial_assumptions: dict[str, Any] | None = None,
    ai_assumptions: dict[str, Any] | None = None,
) -> None:
    source_status = (financial_assumptions or {}).get("sourceStatus") or {}
    history_provider = _financial_provider_display(source_status.get("historyProvider"))
    rows = [
        ["项目", "内容"],
        ["数据快照ID", valuation.get("sourceSnapshotId") or draft.get("source_snapshot_id")],
        ["公司", valuation.get("companyName") or draft.get("company_name")],
        ["产品数据", "DrugAlpha已发布的管线及估值跟踪数据。"],
        ["疾病数据", "DrugAlpha疾病知识库及公开资料。"],
        ["财务数据", history_provider or "iFinD QuantAPI / 备用数据源。"],
        ["证券代码", source_status.get("securityCode") or draft.get("stock_codes")],
        ["财务数据最新年份", source_status.get("latestHistoricalYear")],
        ["AI辅助", "仅用于缺失数据预填和合理性复核；可编辑假设仍需人工确认。"],
        ["药物收入模式", (
            "独立患者流。估值跟踪仅用于圈定范围，药物页按各行输入正向计算收入。"
            if draft.get("revenue_mode") == "independent_patient_flow" else
            "平台基线。部分药物收入可能使用平台峰值反推。"
        )],
        ["模型边界", "仅只读上游已发布数据；生成文件保存在DCF模块内部。"],
        ["免责声明", valuation.get("disclaimer") or draft.get("disclaimer") or AI_MODEL_DISCLAIMER],
    ]
    for row_index, values in enumerate(rows, start=1):
        _write_row(sheet, row_index, 1, values, header=row_index == 1)


def _financial_provider_display(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    labels = {
        "ifind_quantapi_http": "iFinD QuantAPI",
        "akshare_supplement": "AkShare备用数据",
        "akshare_sina_a_share_fallback": "AkShare新浪A股备用数据",
    }
    return " + ".join(labels.get(part, part) for part in text.split("+") if part)


def _write_sources_detailed(
    sheet,
    valuation: dict[str, Any],
    draft: dict[str, Any],
    financial_assumptions: dict[str, Any] | None = None,
    ai_assumptions: dict[str, Any] | None = None,
) -> None:
    source_status = (financial_assumptions or {}).get("sourceStatus") or {}
    financial_review = (ai_assumptions or {}).get("financialReview") or {}
    financial_adjustment = (ai_assumptions or {}).get("financialAdjustment") or {}
    rows = [
        ["Item", "Value"],
        ["Source Snapshot ID", valuation.get("sourceSnapshotId") or draft.get("source_snapshot_id")],
        ["Company", valuation.get("companyName") or draft.get("company_name")],
        ["Asset Coverage", "Only valuation-tracking products explicitly included with a positive domestic or overseas peak-sales anchor enter the DCF. Products without a valid peak-sales anchor are excluded from both the model and workbook."],
        ["Simplified Product Revenue", "PD-1/PD-L1 antibodies, biosimilars, and the approved global product list are grouped in the 其他药物 sheet. Normalized product-level actual sales are used when available; otherwise the opening revenue is inferred from the product peak anchor and launch timing. The first active forecast year and subsequent growth rates are editable, with revenue capped at the risk/equity-adjusted peak. Company-level iFinD revenue is never substituted for product sales."],
        ["Financial Assumptions", source_status.get("historyProvider") or "Default biotech template (company history unavailable)"],
        ["Financial Security", source_status.get("securityCode") or draft.get("stock_codes")],
        ["Financial Latest Year", source_status.get("latestHistoricalYear")],
        ["AI Financial Review", financial_review.get("aiMode") or "not available; no AI override applied"],
        ["AI Financial Adjustment", f"applied {financial_adjustment.get('appliedCount') or 0}; rejected {financial_adjustment.get('rejectedCount') or 0}; history/source values preserved as provenance"],
        ["Net Cash / Net Debt Adjustment", "Excluded from current DCF; equity value temporarily equals enterprise value."],
        ["Model Boundary", "Reads DrugAlpha stable/published data; writes generated DCF files only under DCF module data/."],
        ["Disclaimer", valuation.get("disclaimer") or draft.get("disclaimer")],
        ["Treatment Status Rule", "T0/T1/T2/T3/T4固定比例为100%/70%/50%/30%/20%；指定药物或多个治疗类别归T2。非肿瘤严重度不套用T1固定比例。"],
        ["Autoimmune Severity Evidence", "优先按疾病+地区+严重度定义读取自免证据表；未精确匹配时联网检索并由DrugAlpha AI归纳，30%仅为检索失败占位。"],
        ["Biomarker Rule", "无明确Biomarker限定时为100%；有明确限定但DrugAlpha缺少匹配比例时暂按100%并提示人工复核。"],
        ["Tumor Treatment Duration", "优先读取DrugAlpha同药/同靶点、同适应症且同线次的已发布median_pfs_months；缺失时读取DrugAlpha同适应症的相邻线次（同药优先、同靶点其次）；最后按固定线次规则兜底。各预测年度直接采用参考mPFS，不设置12个月上限，也不做跨年患者队列结转。"],
        ["Indication Deduplication", "DCF按药物+标准化适应症合并管线记录；多个登记号、阶段开始时间、终点及进展记录保留为临床证据，只生成一个适应症收入块。"],
        ["Peak Sales Allocation", "同一药物的国内/海外峰值锚点分别分配；权重仅依据各适应症峰值年市场空间（患者规模×Biomarker比例×目标治疗人群比例×治疗费用），不使用研发阶段或上市成功率。研发阶段只通过POS影响风险调整收入。"],
        ["Success Probability", "国内POS按阶段固定：已上市100%、NDA/BLA 95%、3期90%、2期80%。海外POS优先采用估值跟踪successProbability，缺失时按阶段表回退；海外不会再同时乘阶段POS与估值跟踪POS。"],
        ["Domestic Equity Ratio", "国内风险调整收入=POS前销售额×国内阶段POS×估值跟踪equityRatio。equityRatio缺失时暂按100%，并在单药sheet注明来源；不会回写估值跟踪。"],
        ["Market-specific Launch Timing", "国内、海外上市年分别建模。海外优先使用NCT登记临床的阶段开始时间并沿用国内阶段时长规则；无NCT时按该药国内最高阶段最早开始时间+4年。市场各自的上市年同时驱动收入起算、峰值年、市场空间和渗透率爬坡。"],
        ["Post-launch Sales Ramp", "国内采用10%/30%/55%/75%/90%/100%，海外采用8%/25%/50%/70%/85%/100%；默认峰值年为上市年+7年。Excel可修改峰值年、六档比例、峰值后平台期和自然降幅。默认峰值年及后1年维持100%，随后每年下降3%；该下降不是专利悬崖。"],
        ["Multi-indication Revenue Cap", "估值跟踪峰值按产品/市场定义且位于POS之前。单适应症bottom-up销售以其市场空间分配后峰值为上限，使多适应症合计不突破产品峰值锚点；缺少峰值锚点时不自动套用无依据的重叠折价。"],
        ["Pricing Formula", "肿瘤、非肿瘤口服小分子和非肿瘤注射生物药按月费用×治疗月数；CAR-T/基因治疗按单次/疗程费用×计费系数1。模型不再单独设置治疗模式。"],
        ["Default Drug Cost Categories", "国内第2年基准：肿瘤药物1.0万元/月、非肿瘤口服小分子0.2万元/月、非肿瘤注射生物药0.3万元/月、CAR-T/基因治疗120万元/次。美国参考分别为1.7/0.6/0.7万美元/月及58.5万美元/次；均可在单药sheet修改。"],
        ["China Price Curve", "国内前三类月治疗费用采用上市首年=第2年基准/60%；第2-3年保持；第4-5年×95%；第6-7年×95%^2；第8年起×95%^3。CAR-T/基因治疗默认不自动套医保降价曲线。"],
        ["Non-oncology Treatment Duration", "长期用药默认12个月；固定/短疗程场景采用完整疗程月数；CAR-T、细胞治疗和基因治疗的计费系数为1。"],
    ]
    for proposal in (ai_assumptions or {}).get("proposals") or []:
        if proposal.get("field") != "target_treatment_ratio":
            continue
        rows.append(
            [
                f"Target Population Research - {proposal.get('drugName') or ''}",
                " | ".join(
                    part
                    for part in [
                        str(proposal.get("indication") or ""),
                        str(proposal.get("market") or ""),
                        f"ratio={proposal.get('suggestedValue')}",
                        str(proposal.get("basisComment") or ""),
                        "\n".join(str(url) for url in proposal.get("sourceUrls") or []),
                    ]
                    if part
                ),
            ]
        )
    for product in draft.get("products") or []:
        selection = product.get("valuation_indication_selection") or {}
        if not selection:
            continue
        rows.extend([
            [
                f"AI Valuation Indication - {product.get('drug_name') or ''}",
                " | ".join(part for part in [
                    f"parent={selection.get('parentIndication') or ''}",
                    f"selected={selection.get('selectedIndication') or ''}",
                    f"phase={selection.get('selectedPhase') or ''}",
                    f"launch={selection.get('suggestedLaunchYear') or ''}",
                    f"peak={selection.get('suggestedPeakYear') or ''}",
                    f"deepseek_pos_unapplied={selection.get('suggestedPos') if selection.get('suggestedPos') is not None else ''}",
                    f"confidence={selection.get('confidence') or 'low'}",
                    str(selection.get("basisComment") or ""),
                ] if part),
            ],
            [
                f"AI Valuation Indication Sources - {product.get('drug_name') or ''}",
                "\n".join(str(url) for url in selection.get("sourceUrls") or []),
            ],
        ])
    for row_idx, row in enumerate(rows, start=1):
        _write_row(sheet, row_idx, 1, row, header=row_idx == 1)


def _ai_proposal_lookup(ai_assumptions: dict[str, Any] | None) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    if not ai_assumptions:
        return lookup
    for item in ai_assumptions.get("proposals") or []:
        key = (
            _normalize_ai_key(item.get("drugName")),
            _normalize_ai_key(item.get("indication")),
            _normalize_ai_key(item.get("market")),
            _normalize_ai_key(item.get("field")),
        )
        if all(key):
            lookup.setdefault(key, item)
    return lookup


def _find_ai_proposal(
    ai_lookup: dict[tuple[str, str, str, str], dict[str, Any]] | None,
    product: dict[str, Any],
    indication: dict[str, Any],
    market: dict[str, Any],
    field: str,
) -> dict[str, Any] | None:
    if not ai_lookup:
        return None
    key = (
        _normalize_ai_key(product.get("drug_name")),
        _normalize_ai_key(indication.get("indication_text")),
        _normalize_ai_key(market.get("market")),
        _normalize_ai_key(field),
    )
    return ai_lookup.get(key)


def _add_ai_row_comments(
    sheet,
    row: int,
    start_col: int,
    end_col: int,
    product: dict[str, Any],
    indication: dict[str, Any],
    market: dict[str, Any],
    field: str,
    ai_lookup: dict[tuple[str, str, str, str], dict[str, Any]] | None,
) -> None:
    proposal = _find_ai_proposal(ai_lookup, product, indication, market, field)
    if not proposal:
        return
    _add_row_comments(sheet, row, start_col, end_col, _format_ai_proposal_comment(proposal))


def _apply_ai_proposal_values(
    sheet,
    rows: dict[str, int],
    start_col: int,
    end_col: int,
    product: dict[str, Any],
    indication: dict[str, Any],
    market: dict[str, Any],
    ai_lookup: dict[tuple[str, str, str, str], dict[str, Any]] | None,
) -> None:
    _apply_ai_scalar_to_first_year(
        sheet,
        rows["base_patients"],
        start_col,
        product,
        indication,
        market,
        "base_patient_count_10k",
        ai_lookup,
    )
    _apply_ai_scalar_to_row(
        sheet,
        rows["growth"],
        start_col,
        end_col,
        product,
        indication,
        market,
        "patient_growth_rate",
        ai_lookup,
    )
    _apply_ai_scalar_to_row(
        sheet,
        rows["target_treatment_ratio"],
        start_col,
        end_col,
        product,
        indication,
        market,
        "target_treatment_ratio",
        ai_lookup,
    )
    _apply_ai_curve_or_scalar_to_row(
        sheet,
        rows["penetration"],
        start_col,
        end_col,
        product,
        indication,
        market,
        "penetration_curve",
        ai_lookup,
    )
    _apply_ai_curve_or_scalar_to_row(
        sheet,
        rows["monthly_price"],
        start_col,
        end_col,
        product,
        indication,
        market,
        "monthly_price",
        ai_lookup,
    )
    _apply_ai_curve_or_scalar_to_row(
        sheet,
        rows["treatment_months"],
        start_col,
        end_col,
        product,
        indication,
        market,
        "treatment_months",
        ai_lookup,
    )


def _apply_ai_scalar_to_first_year(
    sheet,
    row: int,
    start_col: int,
    product: dict[str, Any],
    indication: dict[str, Any],
    market: dict[str, Any],
    field: str,
    ai_lookup: dict[tuple[str, str, str, str], dict[str, Any]] | None,
) -> None:
    proposal = _find_ai_proposal(ai_lookup, product, indication, market, field)
    value = _proposal_scalar_value(proposal)
    if value is None:
        return
    cell = sheet.cell(row, start_col)
    cell.value = value
    _style_ai_applied_cell(cell)


def _apply_ai_scalar_to_row(
    sheet,
    row: int,
    start_col: int,
    end_col: int,
    product: dict[str, Any],
    indication: dict[str, Any],
    market: dict[str, Any],
    field: str,
    ai_lookup: dict[tuple[str, str, str, str], dict[str, Any]] | None,
) -> None:
    proposal = _find_ai_proposal(ai_lookup, product, indication, market, field)
    value = _proposal_scalar_value(proposal)
    if value is None:
        return
    for col in range(start_col, end_col + 1):
        cell = sheet.cell(row, col)
        cell.value = value
        _style_ai_applied_cell(cell)


def _apply_ai_curve_or_scalar_to_row(
    sheet,
    row: int,
    start_col: int,
    end_col: int,
    product: dict[str, Any],
    indication: dict[str, Any],
    market: dict[str, Any],
    field: str,
    ai_lookup: dict[tuple[str, str, str, str], dict[str, Any]] | None,
) -> None:
    proposal = _find_ai_proposal(ai_lookup, product, indication, market, field)
    if not proposal:
        return
    if get_policy(proposal.get("field") or field).mode != AI_PREFILL:
        return
    curve = proposal.get("suggestedCurve")
    if isinstance(curve, list) and curve:
        for offset, col in enumerate(range(start_col, end_col + 1)):
            if offset >= len(curve) or curve[offset] is None:
                continue
            cell = sheet.cell(row, col)
            cell.value = curve[offset]
            _style_ai_applied_cell(cell)
        return
    value = _proposal_scalar_value(proposal)
    if value is None:
        return
    for col in range(start_col, end_col + 1):
        cell = sheet.cell(row, col)
        cell.value = value
        _style_ai_applied_cell(cell)


def _proposal_scalar_value(proposal: dict[str, Any] | None) -> float | int | bool | None:
    if not proposal:
        return None
    if get_policy(proposal.get("field") or "").mode != AI_PREFILL:
        return None
    value = proposal.get("suggestedValue")
    if isinstance(value, (int, float, bool)):
        return value
    return None


def _first_numeric_value(value: Any) -> float | int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                return item
    return None


def _bounded_ratio(value: Any, *, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return min(max(float(value), 0.0), 1.0)
    return default


def _has_numeric_values(value: Any) -> bool:
    return _first_numeric_value(value) is not None


def _backsolve_export_penetration_curve(
    *,
    years: list[int],
    base_patient_count_10k: float,
    patient_growth_rate: float,
    eligible_patient_ratio: float,
    monthly_price: float,
    treatment_months: float,
    peak_sales_anchor: Any,
    eligible_indication_count: int,
    launch_year: Any,
    peak_year: Any,
    market: str,
) -> list[float | None]:
    if (
        base_patient_count_10k <= 0
        or eligible_patient_ratio <= 0
        or not isinstance(peak_sales_anchor, (int, float))
        or not isinstance(launch_year, int)
        or not isinstance(peak_year, int)
        or monthly_price <= 0
        or treatment_months <= 0
        or not years
    ):
        return [None for _ in years]
    peak_index = max(peak_year - years[0], 0)
    peak_patients = (
        base_patient_count_10k
        * ((1 + patient_growth_rate) ** peak_index)
        * eligible_patient_ratio
    )
    annual_cost = monthly_price * treatment_months
    peak_sales_per_indication = float(peak_sales_anchor) / max(eligible_indication_count, 1)
    peak_penetration = min(max(peak_sales_per_indication / (peak_patients * annual_cost), 0.0), 1.0)
    from packages.dcf_core.sales_ramp import penetration_curve_from_peak

    return penetration_curve_from_peak(
        years=years,
        peak_penetration=peak_penetration,
        launch_year=launch_year,
        peak_year=peak_year,
        market=market,
    )


def _rule_patient_count_prefill(
    *,
    years: list[int],
    patient_growth_rate: float,
    eligible_patient_ratio: float,
    monthly_price: float,
    treatment_months: float,
    peak_sales_anchor: Any,
    eligible_indication_count: int,
    peak_year: Any,
) -> tuple[float, str]:
    if (
        years
        and isinstance(peak_sales_anchor, (int, float))
        and float(peak_sales_anchor) > 0
        and eligible_patient_ratio > 0
        and monthly_price > 0
        and treatment_months > 0
    ):
        assumed_peak_year = (
            peak_year
            if isinstance(peak_year, int)
            else years[min(DEFAULT_YEARS_TO_PEAK, len(years) - 1)]
        )
        peak_index = max(assumed_peak_year - years[0], 0)
        annual_cost = monthly_price * treatment_months
        peak_sales_per_indication = float(peak_sales_anchor) / max(eligible_indication_count, 1)
        peak_patient_count = peak_sales_per_indication / (
            annual_cost * DEFAULT_RULE_PEAK_PENETRATION * eligible_patient_ratio
        )
        base_patient_count = peak_patient_count / ((1 + patient_growth_rate) ** peak_index)
        return round(max(base_patient_count, 0.01), 4), "peak_anchor_backsolve"
    return 1.0, "minimum_placeholder"


def _is_noncommercial_patient_population(indication: dict[str, Any]) -> bool:
    text = re.sub(r"\s+", "", str(indication.get("indication_text") or "")).lower()
    return any(keyword in text for keyword in ("健康成年人", "健康受试者", "healthyvolunteer"))


def _style_ai_applied_cell(cell) -> None:
    cell.fill = INPUT_FILL
    cell.font = Font(color="8A343B", bold=True)
    cell.protection = Protection(locked=False)


def _write_ai_proposal_cells(
    sheet,
    row: int,
    value_col: int,
    basis_col: int,
    product: dict[str, Any],
    indication: dict[str, Any],
    market: dict[str, Any],
    field: str,
    ai_lookup: dict[tuple[str, str, str, str], dict[str, Any]] | None,
) -> None:
    proposal = _find_ai_proposal(ai_lookup, product, indication, market, field)
    if not proposal:
        return
    value_cell = sheet.cell(row, value_col)
    basis_cell = sheet.cell(row, basis_col)
    value_cell.value = _ai_suggestion_summary(proposal)
    basis_cell.value = _ai_basis_summary(proposal)
    _append_visible_ai_hint(sheet, row, proposal)
    for cell in (value_cell, basis_cell):
        cell.fill = WARN_FILL
        cell.font = Font(name="Microsoft YaHei", color="8A343B", size=9)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = THIN_BORDER


def _append_visible_ai_hint(sheet, row: int, proposal: dict[str, Any]) -> None:
    description_col = next(
        (
            header_cell.column
            for header_cell in sheet[3]
            if str(header_cell.value or "").strip() == "说明"
        ),
        2,
    )
    cell = sheet.cell(row, description_col)
    existing = str(cell.value or "")
    if "AI预填" in existing:
        return
    summary = _ai_suggestion_summary(proposal)
    confidence = proposal.get("confidence") or "unknown"
    cell.value = f"{existing}\nAI预填（置信度:{confidence}，建议复核）"
    cell.fill = WARN_FILL


def _ai_suggestion_summary(proposal: dict[str, Any]) -> str:
    curve = proposal.get("suggestedCurve")
    value = proposal.get("suggestedValue")
    if curve:
        return "曲线: " + ", ".join(_format_ai_number(item) for item in curve[:5]) + ("..." if len(curve) > 5 else "")
    return _format_ai_value(value)


def _ai_basis_summary(proposal: dict[str, Any]) -> str:
    confidence = proposal.get("confidence") or "unknown"
    review = "需复核" if proposal.get("needsHumanReview", True) else "可采用"
    return f"参考公开资料（置信度:{confidence}；{review}）"


def _format_ai_proposal_comment(proposal: dict[str, Any]) -> str:
    value = proposal.get("suggestedValue")
    curve = proposal.get("suggestedCurve")
    if curve:
        suggestion = "AI 建议曲线：" + ", ".join(_format_ai_number(item) for item in curve[:10])
    else:
        suggestion = f"AI 建议值：{_format_ai_value(value)}"
    confidence = proposal.get("confidence") or "unknown"
    review = "是" if proposal.get("needsHumanReview", True) else "否"
    return "\n".join(
        [
            "AI辅助建议",
            suggestion,
            "依据：参考公开资料。",
            f"置信度：{confidence}；需人工复核：{review}",
            "该项可在淡粉输入格中调整。",
        ]
    )


def _format_ai_value(value: Any) -> str:
    if isinstance(value, float):
        return _format_ai_number(value)
    if isinstance(value, dict):
        return "; ".join(f"{key}={_format_ai_value(val)}" for key, val in value.items())
    if isinstance(value, list):
        return ", ".join(_format_ai_value(item) for item in value[:10])
    return "" if value is None else str(value)


def _format_ai_number(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return str(value)
    if abs(value) < 1:
        return f"{value:.2%}"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.4g}"


def _normalize_ai_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _product_results_by_id(valuation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    base = next((item for item in valuation.get("scenarioResults") or [] if item.get("scenario") == "base"), {})
    return {
        product.get("productId"): product
        for product in base.get("productResults") or []
        if product.get("productId")
    }


def _product_market_sheet_names(draft: dict[str, Any]) -> dict[tuple[str, str], str]:
    reserved = ("DCF估值", "估值敏感性", "其他药物", "里程碑收入", "收入趋势图",
                "假设依据", "数据来源与声明", "历史财务数据", "国内药物", "海外药物")
    used = {name.casefold() for name in reserved}
    products = draft.get("products") or []
    entries = [
        (product, _sheet_product_base(product), _product_market_keys(product))
        for product in products
    ]
    overseas_ids = {item["product_id"] for item in draft.get("valuation_scope", []) if item["market"] == "us"}
    overseas = {
        name.casefold() for product, name, keys in entries
        if "us" in keys or product.get("product_id") in overseas_ids
    }
    result = {}
    for product, base, market_keys in entries:
        for market_key in market_keys:
            market_suffix = f"_{_market_summary_label(market_key)}" if base.casefold() in overseas else ""
            name = f"{base[:31 - len(market_suffix)]}{market_suffix}"
            index = 2
            while name.casefold() in used:
                suffix = f"_{index}{market_suffix}"
                name = f"{base[:31 - len(suffix)]}{suffix}"
                index += 1
            used.add(name.casefold())
            result[(product.get("product_id"), market_key)] = name
    return result


def _summary_drug_name(product: dict[str, Any], market_key: str) -> str:
    drug_name = product.get("drug_name") or product.get("product_id") or "Product"
    if product.get("independent_revenue"):
        return f"{drug_name}（{'中国' if market_key == 'china' else '海外'}）"
    if not _should_label_market(product, market_key):
        return drug_name
    return f"{drug_name}（{_market_summary_label(market_key)}）"


def _product_has_multiple_market_sheets(product: dict[str, Any]) -> bool:
    return len(_product_market_keys(product)) > 1


def _should_label_market(product: dict[str, Any], market_key: str) -> bool:
    return market_key == "us" or _product_has_multiple_market_sheets(product)


def _product_market_keys(product: dict[str, Any]) -> list[str]:
    ordered = []
    for key in ("china", "us"):
        if any(
            market.get("market") == key and _market_is_included(market)
            for indication in product.get("indications") or []
            for market in indication.get("markets") or []
        ):
            ordered.append(key)
    return ordered


def _market_is_included(market: dict[str, Any]) -> bool:
    return bool(market.get("revenue_input_sources")) or any(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > 0
        for value in (
            market.get("peak_sales_anchor"),
            market.get("allocated_peak_sales_anchor"),
        )
    )


def _market_summary_label(market_key: str) -> str:
    return "海外" if market_key == "us" else "国内"


def _short_product_name(product: dict[str, Any]) -> str:
    value = product.get("drug_name") or product.get("product_id") or "Product"
    if "/" in value:
        parts = [part for part in value.split("/") if part]
        latin = next((part for part in parts if re.search(r"[A-Za-z0-9]", part)), None)
        if latin:
            return latin
    return value


def _sheet_product_base(product: dict[str, Any]) -> str:
    value = _short_product_name(product).strip()
    regions = r"中国|国内|美国|欧洲|海外"
    value = re.sub(rf"(?:_(?:{regions})|[（(](?:{regions})[）)])+$", "", value)
    return re.sub(r"[\[\]:*?/\\]", "_", value).strip().strip("'") or "Product"


def _write_row(sheet, row_idx: int, start_col: int, values: list[Any], header: bool = False) -> None:
    for offset, value in enumerate(values):
        cell = sheet.cell(row_idx, start_col + offset, value)
        cell.border = THIN_BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.font = Font(name="Microsoft YaHei", color="12263F", size=10)
        if header:
            cell.fill = SECTION_FILL
            cell.font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True, size=10)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _write_block_subsection(sheet, row_idx: int, last_col: int, title: str) -> None:
    sheet.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=last_col)
    cell = sheet.cell(row_idx, 1, title)
    cell.fill = SUBSECTION_FILL
    cell.font = Font(name="Microsoft YaHei", color="8A343B", bold=True, size=10)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    for col in range(1, last_col + 1):
        sheet.cell(row_idx, col).border = THIN_BORDER


def _style_assumption_rows(sheet, rows: list[int], start_col: int, end_col: int) -> None:
    for row in rows:
        for col in range(start_col, end_col + 1):
            cell = sheet.cell(row, col)
            cell.fill = INPUT_FILL
            cell.font = Font(color="8A343B")
            cell.protection = Protection(locked=False)


def _style_output_rows(sheet, rows: list[int], start_col: int, end_col: int) -> None:
    for row in rows:
        for col in range(start_col, end_col + 1):
            sheet.cell(row, col).fill = OUTPUT_FILL


def _style_total_row(sheet, row: int, last_col: int) -> None:
    for col in range(1, last_col + 1):
        cell = sheet.cell(row, col)
        cell.fill = TOTAL_FILL
        cell.font = Font(name="Microsoft YaHei", color="FFFFFF", bold=True, size=10)
        cell.border = THIN_BORDER


def _number_formats_for_block(sheet, rows: dict[str, int], start_col: int, end_col: int) -> None:
    _number_format_range(sheet, rows["base_patients"], start_col, rows["base_patients"], end_col, "#,##0.0")
    _number_format_range(sheet, rows["growth"], start_col, rows["growth"], end_col, "0.0%")
    _number_format_range(sheet, rows["biomarker_ratio"], start_col, rows["target_treatment_ratio"], end_col, "0.0%")
    _number_format_range(sheet, rows["penetration"], start_col, rows["penetration"], end_col, "0.0%")
    _number_format_range(sheet, rows["pos"], start_col, rows["pos"], end_col, "0.0%")
    _number_format_range(sheet, rows["share"], start_col, rows["share"], end_col, "0.0%")
    _number_format_range(sheet, rows["treated_patients"], start_col, rows["gross_sales"], end_col, "#,##0.0")
    _number_format_range(sheet, rows["risk_adjusted"], start_col, rows["risk_adjusted"], end_col, "#,##0.0")


def _number_format_range(sheet, start_row: int, start_col: int, end_row: int, end_col: int, fmt: str) -> None:
    for row in range(start_row, end_row + 1):
        for col in range(start_col, end_col + 1):
            sheet.cell(row, col).number_format = fmt


def _apply_one_decimal_display(sheet) -> None:
    """Standardize numeric display without changing stored values or formulas."""
    for row in sheet.iter_rows():
        for cell in row:
            value = cell.value
            if value is None or isinstance(value, (bool, date, datetime, time)):
                continue
            if is_date_format(cell.number_format):
                continue
            if not isinstance(value, (int, float)) and cell.data_type != "f":
                continue
            left_label = str(sheet.cell(cell.row, max(cell.column - 1, 1)).value or "").strip()
            if sheet.cell(cell.row, 1).value in (
                "可治疗患者数（人）", "使用本产品患者数（人）", "治疗时长（月）", "汇率（人民币/当地货币）"
            ) and cell.number_format in ("#,##0", "0.00", "0.0000"):
                continue
            if str(sheet.cell(cell.row, 1).value or "").startswith("月净价（") and cell.number_format == "#,##0.00":
                continue
            if left_label in {"上市年", "峰值年"}:
                cell.number_format = "0"
                continue
            cell.number_format = (
                ONE_DECIMAL_PERCENT_FORMAT
                if "%" in str(cell.number_format or "")
                else ONE_DECIMAL_NUMBER_FORMAT
            )


def _separate_summary_notes(sheet, col, rows):
    sheet.column_dimensions[get_column_letter(col)].width = 30
    sheet.column_dimensions[get_column_letter(col + 1)].width = 40.6
    sheet.cell(SUMMARY_HEADER_ROW, col + 1, "来源／依据")
    for key, row in rows.items():
        explanation = sheet.cell(row, col).value or ""
        evidence = sheet.cell(row, col + 1).value or ""
        if evidence == "由金额自动计算，仅供参考":
            evidence = ""
        elif evidence.startswith("规则预填"):
            explanation = "；".join(dict.fromkeys(part for part in (explanation, evidence) if part))
            evidence = "用户估值规则"
        write_note_columns(sheet, row, col, explanation, evidence)


def _format_used_range(sheet) -> None:
    utility_sheets = {"DCF估值", "估值敏感性", "其他药物", "里程碑收入", "假设依据", "数据来源与声明", "收入趋势图"}
    if sheet.title == "DCF估值":
        sheet.freeze_panes = "C4"
    elif sheet.title == "估值敏感性":
        sheet.freeze_panes = "B5"
    elif sheet.title == "其他药物":
        sheet.freeze_panes = "E4"
    elif sheet.title not in utility_sheets:
        sheet.freeze_panes = "B4"
    else:
        sheet.freeze_panes = "C4"
    for row in sheet.iter_rows():
        row_has_content = any(cell.value is not None for cell in row)
        if row_has_content:
            for cell in row:
                cell.border = THIN_BORDER
        for cell in row:
            if cell.value is not None:
                cell.border = THIN_BORDER
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                font = copy(cell.font)
                fill_rgb = str(cell.fill.fgColor.rgb or "").upper()
                is_burgundy_header = fill_rgb.endswith("A64045") or fill_rgb.endswith("7D3038")
                font.name = "Microsoft YaHei" if is_burgundy_header else "SimSun"
                if not font.sz:
                    font.sz = 10
                cell.font = font
    for col_idx in range(1, sheet.max_column + 1):
        max_len = 8
        for row_idx in range(1, min(sheet.max_row, 120) + 1):
            value = sheet.cell(row_idx, col_idx).value
            if value is not None:
                text = str(value)
                if text.startswith("="):
                    text = "0.0"
                max_len = max(max_len, min(len(text), 22))
        sheet.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 1, 24)
    _apply_compact_column_widths(sheet)
    if sheet.row_dimensions[1].height is None:
        sheet.row_dimensions[1].height = 28
    if sheet.title == "DCF估值":
        summary_widths = {
            1: 22,
            2: 16,
            sheet.max_column - 1: 30,
            sheet.max_column: 28,
        }
        merged_widths: dict[tuple[int, int], float] = {}
        for merged_range in sheet.merged_cells.ranges:
            if merged_range.min_row != merged_range.max_row:
                continue
            merged_widths[(merged_range.min_row, merged_range.min_col)] = sum(
                sheet.column_dimensions[get_column_letter(col_idx)].width or 12
                for col_idx in range(merged_range.min_col, merged_range.max_col + 1)
            )
        for row in sheet.iter_rows(
            min_row=1,
            max_row=sheet.max_row,
            min_col=1,
            max_col=sheet.max_column,
        ):
            for cell in row:
                if cell.value is None:
                    continue
                alignment = copy(cell.alignment)
                text = str(cell.value)
                display_width = sum(
                    2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
                    for char in text
                )
                available_width = merged_widths.get(
                    (cell.row, cell.column),
                    summary_widths.get(cell.column, 0),
                )
                if cell.column >= sheet.max_column - 1:
                    alignment.wrap_text = False
                else:
                    alignment.wrap_text = (
                        cell.row != 1
                        and cell.column in summary_widths
                        and display_width > available_width
                    )
                cell.alignment = alignment
        for row_idx in range(2, sheet.max_row + 1):
            wrapped_lines = 1
            for col_idx, width in summary_widths.items():
                cell = sheet.cell(row_idx, col_idx)
                if not cell.alignment.wrap_text or cell.value is None:
                    continue
                available_width = merged_widths.get((row_idx, col_idx), width)
                display_width = sum(
                    2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
                    for char in str(cell.value)
                )
                wrapped_lines = max(wrapped_lines, math.ceil(display_width / available_width))
            if wrapped_lines > 1:
                sheet.row_dimensions[row_idx].height = min(44, 18 * wrapped_lines)
    else:
        _apply_wrapped_row_heights(sheet)
    for row_idx in range(2, sheet.max_row + 1):
        if sheet.row_dimensions[row_idx].height is None:
            sheet.row_dimensions[row_idx].height = 20


def _unlock_used_range(sheet) -> None:
    for row in sheet.iter_rows(
        min_row=1,
        max_row=sheet.max_row,
        min_col=1,
        max_col=sheet.max_column,
    ):
        for cell in row:
            cell.protection = Protection(locked=False)


def _add_row_comments(sheet, row: int, start_col: int, end_col: int, text: str) -> None:
    if not text:
        return
    for col in range(start_col, end_col + 1):
        _add_cell_comment(sheet.cell(row, col), text)


def _add_cell_comment(cell, text: str) -> None:
    if not text:
        return
    if cell.comment and cell.comment.text:
        text = f"{cell.comment.text}\n\n{text}"
    cell.comment = Comment(text[:1800], "DrugAlpha DCF")


def _patient_source_note(flow: dict[str, Any]) -> str:
    source_summaries = _patient_source_summaries(flow)
    if source_summaries:
        return "；".join(source_summaries[:2])
    provenance = (flow.get("ai_prefill_provenance") or {}).get("base_patient_count_10k") or {}
    basis = str(provenance.get("basisComment") or "").strip()
    if basis:
        provider = "用户agent" if provenance.get("researchProvider") == "user_agent" else "DrugAlpha AI"
        if _patient_source_urls(flow):
            return f"{provider}公开资料预填：{basis}"
        return f"{provider}预填（未返回可核验链接，需复核）：{basis}"
    source_detail = str(flow.get("base_patient_source_detail") or "").strip()
    if source_detail:
        return source_detail
    metric = flow.get("base_patient_metric")
    if flow.get("requires_user_input"):
        return "当前缺少完整患者流数据，模型预填，需复核"
    if metric:
        return f"DrugAlpha疾病知识库：{metric}口径"
    return "DrugAlpha疾病知识库/模型预填，需复核"


def _biomarker_ratio_note(flow: dict[str, Any]) -> str:
    return str(flow.get("biomarker_ratio_source") or "Biomarker比例规则预填，可编辑")


def _patient_source_summaries(flow: dict[str, Any]) -> list[str]:
    summaries: list[str] = []
    for note in flow.get("source_notes") or []:
        if not isinstance(note, dict) or not _is_patient_source_note(note):
            continue
        raw_value = str(note.get("rawValue") or note.get("raw_value") or "").strip()
        source_labels = [
            str(item.get("label") or item.get("title") or "").strip()
            for item in note.get("sourceDetails") or note.get("source_details") or []
            if isinstance(item, dict) and str(item.get("label") or item.get("title") or "").strip()
        ]
        source_label = "、".join(dict.fromkeys(source_labels))
        summary = "：".join(part for part in (source_label, raw_value) if part)
        if summary and summary not in summaries:
            summaries.append(summary)
    return summaries


def _patient_source_urls(flow: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for note in flow.get("source_notes") or []:
        if not isinstance(note, dict) or not _is_patient_source_note(note):
            continue
        for item in note.get("sourceDetails") or note.get("source_details") or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or item.get("sourceUrl") or "").strip()
            if url and url not in urls:
                urls.append(url)
    provenance = (flow.get("ai_prefill_provenance") or {}).get("base_patient_count_10k") or {}
    for url in provenance.get("sourceUrls") or []:
        clean_url = str(url or "").strip()
        if clean_url and clean_url not in urls:
            urls.append(clean_url)
    return urls


def _biomarker_source_urls(flow: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for item in flow.get("biomarker_ratio_source_details") or []:
        if isinstance(item, dict):
            url = str(item.get("url") or item.get("sourceUrl") or "").strip()
            if url:
                urls.append(url)
    provenance = (flow.get("ai_prefill_provenance") or {}).get("biomarker_ratio") or {}
    urls.extend(str(url or "").strip() for url in provenance.get("sourceUrls") or [])
    return list(dict.fromkeys(url for url in urls if url))


def _is_patient_source_note(note: dict[str, Any]) -> bool:
    field = re.sub(r"[^a-z]", "", str(note.get("field") or "").lower())
    return not field or any(token in field for token in ("patient", "incidence", "prevalence", "population"))


def _apply_reference_links(cell, urls: list[str]) -> None:
    if not urls:
        return
    cell.hyperlink = urls[0]
    font = copy(cell.font)
    font.name = "SimSun"
    font.color = "0563C1"
    font.underline = "single"
    cell.font = font
    _add_cell_comment(cell, "参考链接：\n" + "\n".join(urls[:5]))


def _treatment_status_rule_note(flow: dict[str, Any]) -> str:
    rule_id = str(flow.get("treatment_status_rule_id") or "T0")
    label = str(flow.get("treatment_status_rule_label") or "无明确治疗状态限制")
    source = str(flow.get("target_treatment_ratio_source") or "").strip()
    return "｜".join(part for part in [f"规则{rule_id}", label, source, "可编辑"] if part)


def _biomarker_ratio_comment(flow: dict[str, Any]) -> str:
    review = "需要人工复核。" if flow.get("biomarker_ratio_requires_review") else ""
    return (
        f"Biomarker比例：{_biomarker_ratio_note(flow)}。{review}\n"
        "无明确Biomarker限定时按100%；已有具体阳性率时应采用对应市场和患者口径。"
    )


def _treatment_status_rule_comment(flow: dict[str, Any]) -> str:
    mode = str(flow.get("target_treatment_ratio_mode") or "fixed_rule")
    source_url = str(flow.get("target_treatment_ratio_source_url") or "").strip()
    rule_text = (
        "T0/T1/T2/T3/T4固定比例分别为100%/70%/50%/30%/20%；每个适应症只选择一个等级，不连乘多个等级。"
        if mode == "fixed_rule"
        else "非肿瘤严重度比例不套用T1的70%；优先使用疾病特异证据表，缺失时联网检索，30%仅为检索失败时的保守占位。"
    )
    return "\n".join(
        [
            f"目标治疗人群比例：规则 {_treatment_status_rule_note(flow)}。",
            f"匹配依据：{flow.get('treatment_status_rule_reason') or '默认规则'}。",
            rule_text,
            *( [f"来源：{source_url}"] if source_url else [] ),
            "若患者规模本身已包含相同治疗状态，应将本行改为100%，避免重复折算。",
        ]
    )


def _treatment_months_source_note(flow: dict[str, Any]) -> str:
    source = str(flow.get("treatment_months_source") or "DCF疗程规则").strip()
    detail = str(flow.get("treatment_months_source_detail") or "").strip()
    return "｜".join(part for part in [source, detail, "可逐年编辑"] if part)


def _treatment_months_mode_label(flow: dict[str, Any]) -> str:
    mode = str(flow.get("treatment_months_mode") or "line_rule_fallback")
    return {
        "same_drug_same_indication": "同药+同适应症PFS",
        "same_target_same_indication": "同靶点+同适应症PFS",
        "same_drug_same_indication_web": "DrugAlpha AI联网：同药精确PFS",
        "same_target_same_indication_web": "DrugAlpha AI联网：同靶点精确PFS",
        "same_drug_adjacent_line": "DrugAlpha：同药相邻线次PFS",
        "same_target_adjacent_line": "DrugAlpha：同靶点相邻线次PFS",
        "line_rule_fallback": "肿瘤线次规则兜底",
        "non_oncology_continuous": "非肿瘤长期维持",
        "non_oncology_short_course": "非肿瘤短疗程",
        "one_time_treatment": "一次性治疗规则",
    }.get(mode, mode)


def _treatment_months_comment(flow: dict[str, Any]) -> str:
    mode = str(flow.get("treatment_months_mode") or "line_rule_fallback")
    pfs = flow.get("treatment_months_reference_pfs")
    references = flow.get("treatment_months_references") or []
    lines = [
        f"预计用药时长来源：{_treatment_months_source_note(flow)}。",
    ]
    if pfs is not None:
        lines.append(f"参考mPFS：{float(pfs):.1f}个月；每个预测年度直接采用该值，不设置12个月上限，也不做跨年结转。")
    if mode in {"same_target_same_indication", "same_target_same_indication_web", "same_target_adjacent_line"}:
        lines.append("当前采用同靶点药物的相关适应症mPFS，药物机制/疗效差异可能较大，必须人工复核。")
    if mode in {"same_drug_adjacent_line", "same_target_adjacent_line"}:
        lines.append("当前为相邻线次参考，不做主观折价；患者特征和既往治疗差异可能影响mPFS，必须人工复核。")
    elif mode == "line_rule_fallback":
        lines.append("精确DrugAlpha数据、DrugAlpha AI联网证据及相邻线次数据均不可用，最终使用固定线次规则兜底。")
    elif mode == "non_oncology_short_course":
        lines.append("一次性/短疗程产品应同时按单疗程价格复核，不能直接套用长期月治疗费用。")
    for item in references[:5]:
        lines.append(f"临床参考：{_treatment_reference_text(item)}")
    return "\n".join(lines)


def _treatment_reference_text(item: dict[str, Any]) -> str:
    if item.get("referenceType") == "public_web" or item.get("sourceUrl"):
        return "｜".join(
            str(part)
            for part in [
                item.get("drugName"),
                item.get("sourceTitle"),
                f"mPFS {item.get('pfsMonths')}个月",
                item.get("lineOfTherapy"),
                item.get("sourceUrl"),
            ]
            if part not in {None, ""}
        )
    return "｜".join(
        str(part)
        for part in [
            item.get("drugName"),
            item.get("sheetName"),
            f"mPFS {item.get('pfsMonths')}个月",
            item.get("lineOfTherapy"),
            f"published entry {item.get('entryId')}" if item.get("entryId") is not None else None,
        ]
        if part not in {None, ""}
    )


def _annual_cost_calculation_note(flow: dict[str, Any]) -> str:
    if str(flow.get("price_basis") or "") == "per_treatment":
        return "单次治疗费用 × 计费系数"
    return "月治疗费用 × 预计用药时长"


def _risk_adjusted_calculation_note(market: dict[str, Any]) -> str:
    if str(market.get("market") or "china") == "china":
        return "销售额 × 上市概率 × 国内权益比例"
    return "销售额 × 上市概率 × 海外分成比例 × 汇率"


def _penetration_source_note(market: dict[str, Any], flow: dict[str, Any]) -> str:
    if market.get("peak_sales_anchor") is not None:
        return "由按市场空间分配后的 DrugAlpha 峰值销售锚点反推，可逐年编辑"
    if flow.get("requires_user_input"):
        return "缺少峰值销售或患者流锚点，需手动补充/AI 预填后复核"
    return "规则预填，可逐年编辑"


def _patient_comment(product: dict[str, Any], indication: dict[str, Any], market: dict[str, Any], flow: dict[str, Any]) -> str:
    pieces = [
        f"产品：{product.get('drug_name') or ''}",
        f"适应症：{indication.get('indication_text') or ''}",
        f"市场：{market.get('market') or ''}",
    ]
    if flow.get("base_patient_metric"):
        pieces.append(f"患者口径：{flow.get('base_patient_metric')}")
    if flow.get("requires_user_input"):
        pieces.append("来源说明：DrugAlpha 当前未找到完整患者流行病学数据，本行需用户补充或由 AI 预填后复核。")
    else:
        pieces.append("来源说明：来自 DrugAlpha 疾病知识库预填；可按报告口径调整。")
    return "\n".join(pieces)


def _trial_evidence_comment(indication: dict[str, Any]) -> str:
    evidence = indication.get("trial_evidence") or []
    if not evidence:
        return ""
    return _trial_evidence_summary(indication)


def _trial_evidence_summary(indication: dict[str, Any]) -> str:
    evidence = indication.get("trial_evidence") or []
    lines = [
        str(indication.get("representative_selection_basis") or "同一适应症的多个管线记录已合并。")
    ]
    for index, item in enumerate(evidence, start=1):
        lines.append(
            "｜".join(
                part
                for part in [
                    f"证据{index}",
                    str(item.get("trial_reg_no") or "无登记号"),
                    str(item.get("phase") or "阶段未提供"),
                    f"开始 {item.get('status_start_date')}" if item.get("status_start_date") else "",
                    f"sponsor {item.get('sponsor')}" if item.get("sponsor") else "",
                    str(item.get("primary_endpoint") or ""),
                    str(item.get("enrollment_text") or ""),
                ]
                if part
            )
        )
    return "\n".join(lines)


def _penetration_comment(market: dict[str, Any], flow: dict[str, Any]) -> str:
    peak = market.get("peak_sales_anchor")
    unit = market.get("peak_sales_anchor_unit") or ""
    if peak is not None:
        return (
            f"产品渗透率曲线：先将峰值销售锚点 {peak} {unit} 仅按各适应症市场空间分配，再以分配后锚点反推。\n"
            "市场空间=峰值年患者规模×Biomarker比例×目标治疗人群比例×治疗费用；不乘研发阶段或上市成功率。\n"
            "如市场竞争、医保准入、销售爬坡节奏不同，可直接逐年修改。"
        )
    if flow.get("requires_user_input"):
        return "产品渗透率曲线：缺少峰值销售或患者流锚点，当前为待补充假设；可由 AI 给出建议，但需用户复核。"
    return "产品渗透率曲线：规则预填，可逐年修改。"


def _dcf_assumption_comments(financial_assumptions: dict[str, Any] | None) -> dict[str, str]:
    latest = (financial_assumptions or {}).get("latestHistorical") or {}
    source = latest.get("source") or "默认规则"
    hist_year = latest.get("year") or "最近一年"
    combined_sales_admin = (financial_assumptions or {}).get("operatingExpenseMode") == "combined_sales_admin"
    return {
        "gross_margin": f"毛利率：以 {hist_year} 年历史毛利率为起点并向成熟毛利率过渡。来源：{source}。",
        "sales_expense_rate": (
            f"销售费用：{hist_year} 年仅采用 sales_ad_and_ga 合计口径，合计金额按三阶段曲线向成熟期36%费率锚点收敛并暂列销售费用；管理费用预填 0，两个金额均可编辑，费用率自动计算。来源：{source}。"
            if combined_sales_admin
            else f"销售费用：以 {hist_year} 年历史费用为起点，前3年温和增长、中间4年向成熟期30%费率锚点收敛；金额可编辑，费用率自动计算。来源：{source}。"
        ),
        "rd_expense_rate": f"研发费用：以 {hist_year} 年历史费用为起点，前3年温和增长、中间4年向成熟期15%费率锚点收敛；金额可编辑，费用率自动计算。来源：{source}。",
        "admin_expense_rate": (
            "管理费用：iFinD 仅披露 sales_ad_and_ga 合计口径，初始预填 0，避免重复扣减；如有需要可手工调整金额。"
            if combined_sales_admin
            else f"管理费用：以 {hist_year} 年历史费用为起点，前3年温和增长、中间4年向成熟期6%费率锚点收敛；金额可编辑，费用率自动计算。来源：{source}。"
        ),
        "tax_rate": "所得税率：亏损年份不直接采用历史有效税率，随商业化成熟逐步过渡至成熟税率。",
        "da_rate": f"折旧摊销：以 {hist_year} 年折旧摊销绝对值为基数外推；金额可编辑，比例自动计算。来源：{source}。",
        "capex_rate": f"资本开支：优先按历史资本开支绝对值外推；金额可编辑，比例自动计算，缺失时回退为 0。来源：{source}。",
        "nwc_rate": _nwc_curve_method_note(financial_assumptions),
        "wacc": "WACC/折现率：公司层面估值核心假设，建议结合融资成本、市场风险溢价、产品风险和可比公司调整。",
        "terminal_growth": "永续增长率：成熟期长期增长假设，需低于 WACC，并与行业长期增速匹配。",
        "share_count": "总股本：默认采用 iFinD total_shares 估值日数据；导出后可手工修改，AI 不自动覆盖。",
    }


def _nwc_curve_method_note(financial_assumptions: dict[str, Any] | None) -> str:
    metadata = (((financial_assumptions or {}).get("curveMetadata") or {}).get("nwc_rate") or {})
    if metadata.get("method") == "historical_operating_capital":
        observations = metadata.get("historicalObservations") or []
        bounds = metadata.get("rateBounds") or [-0.08, 0.08]
        return (
            "营运资本变动金额优先采用 iFinD 历史口径预填（变动字段缺失时采用连续年度营运资本余额差额）；"
            f"原始比例按 {float(bounds[0]):.0%} 至 {float(bounds[1]):.0%} 的风险边界约束，"
            f"再采用最近 {len(observations)} 个有效历史观察的中位数形成预测金额；金额可编辑，比例自动计算。"
        )
    return "iFinD 历史营运资本数据不足时按规则形成营运资本变动金额初稿；金额可编辑，比例自动计算。"


def _research_provider_label(ai_assumptions: dict[str, Any] | None) -> str:
    return "用户agent" if (ai_assumptions or {}).get("researchProvider") == "user_agent" else "DrugAlpha AI"


def _format_financial_review(ai_assumptions: dict[str, Any] | None) -> str:
    review = (ai_assumptions or {}).get("financialReview") or {}
    if not review:
        return ""
    lines = [
        f"{_research_provider_label(ai_assumptions)} 财务假设复核与未来曲线建议",
        f"置信度：{review.get('confidence') or '未提供'}",
        str(review.get("basisComment") or "").strip(),
    ]
    adjustment = (ai_assumptions or {}).get("financialAdjustment") or {}
    lines.append(
        f"校验结果：应用 {adjustment.get('appliedCount') or 0} 项，拒绝 {adjustment.get('rejectedCount') or 0} 项；历史财务和来源字段锁定。"
    )
    checks = _clean_financial_review_checks(review.get("checks"))
    if checks:
        lines.append("待确认项：")
        lines.extend(f"- {item}" for item in checks)
    return "\n".join(line for line in lines if line)


def _effective_financial_curves(
    financial_assumptions: dict[str, Any] | None,
    ai_assumptions: dict[str, Any] | None,
) -> dict[str, Any]:
    base = (financial_assumptions or {}).get("curves") or {}
    adjustment = (ai_assumptions or {}).get("financialAdjustment") or {}
    if adjustment.get("eligible") and isinstance(adjustment.get("appliedCurves"), dict):
        curves = dict(adjustment["appliedCurves"])
    else:
        curves = dict(base)
    if (financial_assumptions or {}).get("operatingExpenseMode") == "combined_sales_admin":
        curves["admin_expense_rate"] = [0.0 for _ in (base.get("admin_expense_rate") or [])]
    return curves


def _applied_ai_financial_fields(ai_assumptions: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    adjustment = (ai_assumptions or {}).get("financialAdjustment") or {}
    return {
        str(item.get("field")): item
        for item in adjustment.get("decisions") or []
        if isinstance(item, dict) and item.get("status") == "applied" and item.get("field")
    }


def _financial_curve_label(field: str) -> str:
    return {
        "gross_margin": "毛利率",
        "sales_expense_rate": "销售费用率",
        "rd_expense_rate": "研发费用率",
        "admin_expense_rate": "管理费用率",
        "tax_rate": "所得税率",
        "da_rate": "折旧摊销/收入",
        "capex_rate": "资本开支/收入",
        "nwc_rate": "营运资本占收入增量",
    }.get(field, field or "未知字段")


def _clean_financial_review_checks(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    placeholders = {"string", "逐条写出需人工确认的具体事项；没有则返回空数组"}
    return [
        str(item).strip()
        for item in value
        if str(item).strip() and str(item).strip().lower() not in placeholders
    ]


def _share_count_source_label(financial_assumptions: dict[str, Any] | None) -> str:
    if (financial_assumptions or {}).get("equityBridge"):
        return "估值基准日总股本（历史财务数据页）"
    if ((financial_assumptions or {}).get("sourceStatus") or {}).get("mcpHistory"):
        return "总股本（独立取证）"
    return "iFinD总股本"


def _financial_curve_source_label(
    field: str,
    financial_assumptions: dict[str, Any] | None,
    *,
    ai_applied: bool,
    combined_sales_admin: bool,
) -> str:
    assumptions = financial_assumptions or {}
    if ai_applied:
        return "DrugAlpha AI校验后调整"
    if field == "tax_rate":
        return "规则预填（税率逐步过渡）"
    if field == "nwc_rate":
        metadata = ((assumptions.get("curveMetadata") or {}).get("nwc_rate") or {})
        if metadata.get("method") == "historical_operating_capital":
            return "iFinD历史营运资本"
        return "规则预填（商业化爬坡）"
    if field == "sales_expense_rate" and combined_sales_admin:
        return "iFinD销售及管理费用合计预填至销售费用"
    if field == "admin_expense_rate" and combined_sales_admin:
        return "iFinD合计口径，管理费用预填0，未拆分"

    latest = assumptions.get("latestHistorical") or {}
    historical_key = {
        "gross_margin": "gross_margin_rate",
        "sales_expense_rate": "sales_admin_expense" if combined_sales_admin else "sales_expense",
        "rd_expense_rate": "rd_expense",
        "admin_expense_rate": "admin_expense",
        "da_rate": "depreciation_amortization",
        "capex_rate": "capex",
    }.get(field)
    if not historical_key or latest.get(historical_key) is None:
        return "缺失回退规则"

    source_key = "gross_profit" if field == "gross_margin" else historical_key
    source = (latest.get("field_sources") or {}).get(source_key) or latest.get("source") or ""
    provider = str(source) if (assumptions.get("sourceStatus") or {}).get("mcpHistory") else (
        "iFinD" if str(source).startswith("ifind_") else "AkShare备选"
    )
    method = "历史毛利率" if field == "gross_margin" else "历史金额三阶段预填"
    return f"{provider}{method}"


def _share_count_comment(financial_assumptions: dict[str, Any] | None) -> str:
    assumptions = financial_assumptions or {}
    if assumptions.get("equityBridge"):
        return f"总股本截至 {assumptions['equityBridge']['as_of']}，详见历史财务数据页。"
    if (assumptions.get("sourceStatus") or {}).get("mcpHistory"):
        return "本次财报输入不含估值日总股本，需单独取证。股权价值暂按企业价值列示。 "
    return "\n".join(
        [
            "总股本默认采用 iFinD total_shares；AI 不自动覆盖，导出后可手工修改。",
            f"数据来源：{assumptions.get('shareCountSource') or 'ifind_quantapi_http:total_shares'}",
            f"查询时点：{assumptions.get('shareCountAsOf') or '未返回'}",
            "当前版本暂不纳入净现金/净债务调整；股权价值暂按企业价值计算。",
        ]
    )


def _equity_basis_rows(financial_assumptions: dict[str, Any] | None) -> list[list[Any]]:
    assumptions = financial_assumptions or {}
    if assumptions.get("equityBridge"):
        return [
            ["总股本（亿股）", "='历史财务数据'!B42/100000000", _share_count_source_label(assumptions)],
            ["净现金/净债务调整", "='历史财务数据'!B41", "股权价值 = 企业价值 + 净现金。"],
        ]
    return [
        ["总股本（亿股）", assumptions.get("shareCount100m"), _share_count_source_label(assumptions)],
        ["净现金/净债务调整", "暂不纳入", "当前版本股权价值暂按企业价值计算；不读取或应用资产负债表净现金桥。"],
    ]


def _apply_compact_column_widths(sheet) -> None:
    product_description_col: int | None = None
    if sheet.title == "DCF估值":
        widths = {"A": 22, "B": 16}
        year_start_col = 3
        year_end_col = max(year_start_col, sheet.max_column - (3 if sheet.cell(SUMMARY_HEADER_ROW, sheet.max_column).value == "成功条件下单药DCF（亿元）" else 2))
        year_width = 11
    elif sheet.title == "假设依据":
        widths = {"A": 22, "B": 46}
        year_start_col = 3
        year_end_col = sheet.max_column
        year_width = 12
    elif sheet.title == "估值敏感性":
        widths = {"A": 22, "B": 13, "C": 13, "D": 13, "E": 13, "F": 13, "G": 24, "H": 16}
        year_start_col = 9
        year_end_col = 8
        year_width = 12
    elif sheet.title in {"数据来源与声明", "收入趋势图"}:
        widths = {"A": 20, "B": 54}
        year_start_col = 3
        year_end_col = sheet.max_column
        year_width = 12
    elif sheet.title == "历史财务数据":
        widths = {"A": 25, "B": 28}
        year_start_col = 3
        year_end_col = max(3, sheet.max_column - 1)
        year_width = 14
    elif sheet.title == "其他药物":
        widths = {"A": 24, "B": 34, "C": 22, "D": 26}
        year_start_col = 5
        year_end_col = sheet.max_column
        year_width = 11
    elif sheet.title == "里程碑收入":
        widths = {"A": 24, "B": 30}
        year_start_col = 3
        year_end_col = sheet.max_column
        year_width = 11
    else:
        product_description_col = next(
            (
                cell.column
                for cell in sheet[3]
                if str(cell.value or "").strip() in {"说明", "关键假设与依据"}
            ),
            max(sheet.max_column - 2, 2),
        )
        widths = {"A": 20}
        year_start_col = 2
        year_end_col = max(year_start_col, product_description_col - 1)
        year_width = 11
    for col, width in widths.items():
        sheet.column_dimensions[col].width = width
    for col_idx in range(year_start_col, year_end_col + 1):
        sheet.column_dimensions[get_column_letter(col_idx)].width = year_width
    if sheet.title == "DCF估值":
        sheet.column_dimensions[get_column_letter(year_end_col + 1)].width = 30
        sheet.column_dimensions[get_column_letter(year_end_col + 2)].width = 28
        if sheet.max_column > year_end_col + 2:
            sheet.column_dimensions[get_column_letter(year_end_col + 3)].width = 25
    if sheet.title == "假设依据":
        sheet.column_dimensions["C"].width = 18
        sheet.column_dimensions["F"].width = 36
        sheet.column_dimensions["H"].width = 36
    if sheet.title == "历史财务数据":
        sheet.column_dimensions[get_column_letter(sheet.max_column)].width = 42
    if product_description_col is not None:
        sheet.column_dimensions[get_column_letter(product_description_col)].width = 34
        if sheet.max_column >= product_description_col + 1:
            sheet.column_dimensions[get_column_letter(product_description_col + 1)].width = 18
        if sheet.max_column >= product_description_col + 2:
            sheet.column_dimensions[get_column_letter(product_description_col + 2)].width = 54


def _apply_wrapped_row_heights(sheet) -> None:
    maximum_height = 240 if sheet.title == "假设依据" else 120
    for row_idx in range(2, sheet.max_row + 1):
        if sheet.row_dimensions[row_idx].height is not None:
            continue
        needed_lines = 1
        for col_idx in range(1, sheet.max_column + 1):
            value = sheet.cell(row_idx, col_idx).value
            if not isinstance(value, str) or not value or value.startswith("="):
                continue
            width = sheet.column_dimensions[get_column_letter(col_idx)].width or 12
            line_count = 0
            for line in value.splitlines() or [value]:
                display_width = sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in line)
                line_count += max(1, math.ceil(display_width / max(width, 1)))
            needed_lines = max(needed_lines, line_count)
        if needed_lines > 1:
            sheet.row_dimensions[row_idx].height = min(maximum_height, max(20, needed_lines * 15))


def _list_year_value(value: Any, index: int) -> Any:
    if isinstance(value, list):
        if index >= len(value):
            return None
        return value[index]
    return value


def _history_value(row: dict[str, Any], key: str) -> Any:
    value = row.get(key)
    return "" if value is None else value


def _history_source_note(history: list[dict[str, Any]], key: str) -> str:
    labels = []
    for row in history:
        if row.get(key) is None:
            continue
        source = (row.get("field_sources") or {}).get(key) or row.get("source")
        text = str(source or "")
        if text.startswith("ifind_quantapi_http"):
            label = "iFinD QuantAPI / THS_BD"
        elif text.startswith("akshare_sina_a_share_fallback"):
            label = "AkShare（A股备选）"
        elif text.startswith("akshare_"):
            label = "AkShare（港股备选）"
        else:
            label = text
        if label and label not in labels:
            labels.append(label)
    return f"来源：{' + '.join(labels)}。" if labels else "来源待确认。"


def _history_depreciation_source_note(history: list[dict[str, Any]]) -> str:
    indicators = []
    for row in history:
        if row.get("depreciation_amortization") is None:
            continue
        indicator = row.get("depreciation_amortization_indicator_used")
        source = (row.get("field_sources") or {}).get("depreciation_amortization") or row.get("source")
        if indicator:
            label = f"{row.get('year')}年 {indicator}"
        elif str(source or "").startswith("akshare_"):
            label = f"{row.get('year')}年 AkShare备选"
        elif source and not str(source).startswith("ifind_"):
            label = f"{row.get('year')}年 {source}"
        else:
            label = f"{row.get('year')}年 来源待确认"
        if label not in indicators:
            indicators.append(label)
    priority = "取值优先级：p_depreciation_and_amortization > deprec_and_amorti > depreciation > AkShare；各字段不相加。"
    selected = f" 实际采用：{'；'.join(indicators)}。" if indicators else ""
    return priority + selected


def _curve_value(values: list[float], index: int) -> float:
    if not values:
        return 0.0
    if index >= len(values):
        return values[-1]
    return values[index]


def _market_economic_share(market: dict[str, Any]) -> float:
    if market.get("market") == "china":
        return 1.0 if market.get("equity_ratio") is None else market.get("equity_ratio")
    return 1.0 if market.get("revenue_share_ratio") is None else market.get("revenue_share_ratio")


def _success_probability_comment(
    indication: dict[str, Any],
    market: dict[str, Any],
    probability: float,
    source: str,
) -> str:
    phase = indication.get("phase") or "未知"
    if source == "valuation_tracking_overseas_success_probability":
        return (
            f"海外上市概率：{probability:.1%}，直接读取估值跟踪 successProbability。"
            f"当前研发阶段为 {phase}，阶段值不再额外相乘，避免重复折价。"
        )
    if market.get("market") == "us":
        return (
            f"海外上市概率：估值跟踪未提供有效 successProbability，按当前研发阶段 {phase} 回退为 {probability:.1%}。"
            "如后续在估值跟踪补充该字段，新生成模型将优先采用估值跟踪值。"
        )
    return (
        f"国内上市概率：按当前研发阶段 {phase} 固定预填为 {probability:.1%}。"
        "固定表为已上市100%、NDA/BLA 95%、3期90%、2期80%；Excel中仍可手动修改。"
    )


def _price_label(market: dict[str, Any], flow: dict[str, Any]) -> str:
    if str(flow.get("price_basis") or "per_month") == "per_treatment":
        return "单次治疗费用（万元）" if market.get("market") == "china" else "单次治疗费用（万美元）"
    return "月治疗费用（万元）" if market.get("market") == "china" else "月治疗费用（万美元）"


def _price_source_note(flow: dict[str, Any]) -> str:
    source = str(flow.get("price_source") or "").strip()
    evidence = (flow.get("treatment_cost_prefill") or {}).get("agent_evidence")
    if isinstance(evidence, dict) and evidence.get("basisComment"):
        return "用户agent基于说明书和价格测算｜待人工复核｜可逐年编辑"
    if "平台药物治疗费用测算" in source:
        return "来源于平台药物治疗费用测算模块｜可逐年编辑"
    return "费用假设，可逐年编辑"


def _price_rule_comment(flow: dict[str, Any], market: dict[str, Any]) -> str:
    unit = "万元/次" if str(flow.get("price_basis") or "") == "per_treatment" and market.get("market") == "china" else (
        "万美元/次" if str(flow.get("price_basis") or "") == "per_treatment" else (
            "万元/月" if market.get("market") == "china" else "万美元/月"
        )
    )
    source = str(flow.get("price_source") or "").strip()
    evidence = (flow.get("treatment_cost_prefill") or {}).get("agent_evidence")
    if isinstance(evidence, dict) and evidence.get("basisComment"):
        urls = evidence.get("sourceUrls") or []
        return (f"用户agent测算，待人工复核；单位：{unit}。\n"
                f"计算依据与假设：{evidence['basisComment']}\n"
                "来源：" + "\n".join(str(url) for url in urls))
    if "平台药物治疗费用测算" in source:
        return f"已引用平台药物治疗费用测算结果；单位：{unit}。可结合实际支付和用药方案逐年调整。"
    return f"费用为模型初始假设；单位：{unit}。请结合实际支付、挂网价格和用药强度复核后调整。"


def _treatment_units_label(flow: dict[str, Any]) -> str:
    if str(flow.get("price_basis") or "") == "per_treatment":
        return "计费系数（次）"
    if str(flow.get("treatment_months_mode") or "") == "non_oncology_short_course":
        return "完整疗程时长（月）"
    return "预计用药时长（月）"


def _treatment_duration_note(flow: dict[str, Any]) -> str:
    mode = str(flow.get("treatment_months_mode") or "line_rule_fallback")
    if mode == "non_oncology_continuous":
        return "按长期用药口径"
    if mode == "non_oncology_short_course":
        return "参考常见完整疗程"
    if mode == "one_time_treatment" or str(flow.get("price_basis") or "") == "per_treatment":
        return "按单次治疗计费"
    return "参考同类药物PFS"


def _annual_cost_label(market: dict[str, Any]) -> str:
    return "年治疗费用（万元）" if market.get("market") == "china" else "年治疗费用（万美元）"


def _gross_sales_label(market: dict[str, Any]) -> str:
    return "销售额（亿元）" if market.get("market") == "china" else "销售额（亿美元）"


def _share_label(market: dict[str, Any]) -> str:
    return "权益比例" if market.get("market") == "china" else "分成比例"


def _share_source_note(market: dict[str, Any]) -> str:
    if market.get("market") == "china":
        return (
            "估值跟踪国内 equityRatio，可编辑"
            if isinstance(market.get("equity_ratio"), (int, float))
            else "估值跟踪未提供 equityRatio，暂按100%，可编辑"
        )
    return (
        "估值跟踪海外 revenueShareRatio，可编辑"
        if isinstance(market.get("revenue_share_ratio"), (int, float))
        else "估值跟踪未提供 revenueShareRatio，暂按100%，可编辑"
    )


def _share_comment(market: dict[str, Any]) -> str:
    if market.get("market") == "china":
        if isinstance(market.get("equity_ratio"), (int, float)):
            return (
                f"国内权益比例：{float(market.get('equity_ratio')):.1%}，读取估值跟踪 domestic.equityRatio。"
                "风险调整收入=销售额×国内上市概率×权益比例。"
            )
        return "估值跟踪未提供国内权益比例，当前暂按100%；如存在授权或联合开发，请在Excel中修改并复核。"
    if isinstance(market.get("revenue_share_ratio"), (int, float)):
        return (
            f"海外分成比例：{float(market.get('revenue_share_ratio')):.1%}，读取估值跟踪 overseas.revenueShareRatio。"
            "风险调整收入=海外销售额×海外上市概率×分成比例×汇率。"
        )
    return "估值跟踪未提供海外分成比例，当前暂按100%；如存在授权或联合开发，请在Excel中修改并复核。"


def _diagnostic_text(flow: dict[str, Any], market: dict[str, Any]) -> str:
    notes = []
    if flow.get("requires_user_input"):
        notes.append("需要人工补充/复核患者流假设")
    if market.get("peak_sales_anchor") is None:
        notes.append("缺少 DrugAlpha 峰值销售锚点")
    if flow.get("biomarker_ratio_requires_review"):
        notes.append("Biomarker比例暂按100%，需补充具体数据")
    curve = [item for item in flow.get("penetration_curve") or [] if item is not None]
    if curve and max(curve) >= 0.65:
        notes.append("渗透率触及 65% 上限，需复核患者口径或价格")
    return "；".join(notes)
