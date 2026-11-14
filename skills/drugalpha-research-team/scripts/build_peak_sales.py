"""Create a regional terminal-sales workbook from the DCF drug-page prototype."""

import argparse
from copy import copy
import json
from pathlib import Path
import sys

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter as col_letter
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parent / "dcf"))
from packages.excel_export.maiwei_template import (
    BLOCK_ROWS, TEMPLATE_PATH, _copy_rows, format_dcf_typography,
    write_note_columns, write_target_patient_ratio_note,
)

OVERSEAS_NOTE = "海外主要考虑欧美市场。以美国市场为测算基础，欧洲按美国当年终端销售额的 50% 折算，海外销售为两者之和。"

CASE_NAMES = ("悲观", "中性", "乐观")
CASE_FIELDS = (
    ("同类药物渗透率（成熟期）", "class_penetration", "0%"),
    ("该药物在同类药物中份额（成熟期）", "class_share", "0%"),
    ("预计用药时长（月）", "months", "0.0"),
    ("预计上市年", "launch", "0"),
    ("从首年到成熟的年数", "maturity_years", "0"),
)


def set_input(sheet, row, column, value, fmt=None):
    cell = sheet.cell(row, column)
    cell.value = value
    if fmt:
        cell.number_format = fmt
    return cell


def assumption_block(sheet, prototype, title, rows):
    first = sheet.max_row + 2
    for col, value in enumerate((title, *CASE_NAMES, "说明", "来源／依据"), 1):
        cell = sheet.cell(first, col, value)
        cell._style = copy(prototype["A3"]._style)
    for i, (name, values, fmt, note, source) in enumerate(rows, 1):
        row = first + i
        sheet.cell(row, 1, name)
        for col, value in enumerate(values, 2):
            cell = set_input(sheet, row, col, value)
            cell._style = copy(prototype["B10" if cell.data_type == "f" else "B8"]._style)
            cell.number_format = fmt
        write_note_columns(sheet, row, 5, note, source)
    return first


def write_region(workbook, prototype, product, region, years, assumptions, europe_ratio=None):
    currency = region["currency"]
    sheet = workbook.copy_worksheet(prototype)
    sheet.title = "海外" if europe_ratio else region["name"]
    sales_factor = f"*(1+{europe_ratio})" if europe_ratio else ""
    sheet.freeze_panes = "B4"
    sheet.sheet_view.showGridLines = False
    sheet["A1"] = f"{product}（{sheet.title}）｜产品终端销售"
    sheet["A2"], sheet["B2"] = "情景", "='测算假设'!B2"
    for c, year in enumerate(years, 2):
        sheet.cell(3, c).value = f"{year}E"
    sheet.delete_rows(23, sheet.max_row - 22)
    entries = region["indications"]
    count = len(entries)
    total = 4 + count * BLOCK_ROWS
    for index in range(1, count):
        offset = index * BLOCK_ROWS
        _copy_rows(prototype, sheet, 4, BLOCK_ROWS, offset)
        sheet.merge_cells(start_row=4 + offset, start_column=1, end_row=4 + offset, end_column=13)
    currency_label = "元" if currency == "CNY" else currency
    price = region["price"]
    price_start = assumptions.max_row + 2
    price_rows = [
        (f"参照包装价（{currency_label}）", [price["package"]], "#,##0.00", price["note"], price["source"]),
        ("每包装单位数", [price["units"]], "0", "", ""),
        ("每日单位数", [price["daily"]], "0", price["dose_note"], price["dose_source"]),
        ("净价系数", [price.get("factor", 1)], "0%", price.get("factor_note", ""), ""),
        (f"足年治疗费用（{currency_label}）",
         [f"=B{price_start+1}/B{price_start+2}*B{price_start+3}*365*B{price_start+4}"], "#,##0.00", "", ""),
        (f"月治疗费用（{currency_label}）", [f"=B{price_start+5}/12"], "#,##0.00", "足年费用 ÷ 12", ""),
        ("每单位本币折人民币", [region.get("fx", 1)], "0.0000", "仅用于地区汇总。", region.get("fx_source", "")),
    ]
    assumption_block(assumptions, prototype, f"{sheet.title}｜共同参数（数值见 B 列）", price_rows)
    fx_ref = f"'测算假设'!$B${price_start+7}"
    mature_exposures = []
    for index, item in enumerate(entries):
        off = index * BLOCK_ROWS

        sheet.cell(4 + off, 1).value = f'{item["label"]}｜{sheet.title}'
        sheet.cell(5 + off, 1).value = "患者流与收入测算"
        sheet.cell(6 + off, 1).value = "患者规模（万人）"
        sheet.cell(8 + off, 1).value = "标志物比例"
        sheet.cell(13 + off, 1).value = f"月治疗费用（万{currency_label}）"
        sheet.cell(15 + off, 1).value = f"全疗程费用（万{currency_label}）"
        sheet.cell(16 + off, 1).value = f"终端销售（亿{currency_label}）"
        for r, label in {
            17: "同类药物渗透率", 18: "该药物在同类药物中份额",
            19: "同药既往暴露排除比例", 20: "去重后可治疗患者（万人）",
            21: "预计上市年", 22: "成熟期销售额（按末年患者池）",
        }.items():
            sheet.cell(r + off, 1).value = label
            for c in range(2, 12):
                cell = sheet.cell(r + off, c)
                cell.value = None
                cell._style = copy(prototype.cell(10, c)._style)
        for r in (6, 8, 9, 11, 13, 14, 17, 18, 19, 20, 21, 22):
            sheet.cell(r + off, 12).value = None
            sheet.cell(r + off, 13).value = None
            sheet.cell(r + off, 13).hyperlink = None
        for r, desc, source in (
            (6, item["population_note"], item["population_source"]),
            (8, item["biomarker_note"], item["biomarker_source"]),
            (11, "同类药物渗透率 × 该药物在同类药物中份额。", ""),
            (13, "来自「测算假设」页的本地区月费。", ""),
            (14, item["duration_note"], item["clinical_source"]),
            (17, item["adoption_note"], item["adoption_source"]),
            (18, item["share_note"], item["share_source"]),
            (19, "同药前线暴露比例代理。以相应前一疗线的上年产品渗透率估算，假定转线率与既往是否用本品无关。", ""),
            (20, "可治疗患者 ×（1－同药既往暴露排除比例）。", ""),
            (21, item["launch_note"], item["pipeline_source"]),
            (22, f"单位为亿{currency_label}。用末年患者池和稳定采用率计算，并用成熟期前线采用率去重。", ""),
        ):
            write_note_columns(sheet, r + off, 12, desc, source)
        write_note_columns(sheet, 9 + off, 12, "", item["pool_source"])
        fixed_rows = [
            ("首年患者规模（人）", [item["population"]], "#,##0", item["population_note"], item["population_source"]),
            ("患者年增长率", [item["growth"]], "0.0%", "研究假设", ""),
            ("标志物比例", [item["biomarker"]], "0.0%", item["biomarker_note"], item["biomarker_source"]),
            ("适应症基础人群占比", [item["fit"]], "0.0%", item["pool_note"], item["pool_source"]),
            ("到达目标疗线比例", [item["line"]], "0.0%", "分母为适应症基础人群。", item["pool_source"]),
            ("检测及治疗可及比例", [item["access"]], "0.0%", "分母为已到达目标疗线的患者。", ""),
        ]
        fixed = assumption_block(assumptions, prototype, f'{sheet.title}｜{item["label"]}（数值见 B 列）', fixed_rows)

        def fixed_ref(offset):
            return f"'测算假设'!$B${fixed+offset}"

        for c, i in enumerate((4, 5, 6), 14):
            set_input(sheet, 9 + off, c, f"={fixed_ref(i)}", "0.0%")
        write_target_patient_ratio_note(sheet, 9 + off, item["pool_note"])
        write_note_columns(sheet, 12 + off, 12, "去重后可治疗患者 × 产品渗透率，上市前为零。")
        direct = "product_penetration" in item
        scenario_notes = (
            (item["adoption_note"], item["adoption_source"]),
            (item["share_note"], item["share_source"]),
            (item["duration_note"], item["clinical_source"]),
            (item["launch_note"], item["pipeline_source"]),
            ("研究假设。达到成熟采用率的年数，自上市当年起算。", ""),
        )
        scenario_rows = []
        for i, ((label, key, fmt), (note, evidence)) in enumerate(zip(CASE_FIELDS, scenario_notes)):
            case_key = "product_penetration" if direct and i == 0 else key
            values = [1, 1, 1] if direct and i == 1 else item[case_key]
            if len(values) != len(CASE_NAMES):
                raise ValueError(f"{item['label']} 的 {case_key} 须提供三种情景")
            if direct and i < 2:
                label = ("产品渗透率（成熟期）", "直接产品渗透率计算因子")[i]
            scenario_rows.append((label, values, fmt, note, evidence))
        block = assumption_block(assumptions, prototype, f'{sheet.title}｜{item["label"]}情景', scenario_rows)
        if direct:
            write_note_columns(sheet, 11 + off, 12, item["adoption_note"], item["adoption_source"])
            for row in (17, 18):
                sheet.row_dimensions[row + off].hidden = True

        def case(i):
            return f"INDEX('测算假设'!$B${block+i}:$D${block+i},1,MATCH($B$2,'测算假设'!$B${block}:$D${block},0))"

        set_input(sheet, 21 + off, 2, f"={case(4)}", "0")
        prior = item.get("prior")
        if prior is not None and not 0 <= prior < index:
            raise ValueError("先录入前线适应症，再用 prior 指向其序号")
        for c, year in enumerate(years, 2):
            letter = col_letter(c)
            previous = col_letter(c - 1)
            prior_dedup = f"{previous}{19+prior*BLOCK_ROWS}" if prior is not None and c > 2 else "0"
            prior_adoption = f"{previous}{11+prior*BLOCK_ROWS}" if prior is not None and c > 2 else "0"
            source = f"{prior_dedup}+(1-{prior_dedup})*{prior_adoption}"
            maturity = f'MIN(1,(VALUE(LEFT({letter}$3,4))-$B${21+off}+1)/MAX(1,{case(5)}+1))'
            values = {
                6: f"={fixed_ref(1)}/10000" if c == 2 else f"={previous}{6+off}*(1+{letter}{7+off})",
                7: f"={fixed_ref(2)}",
                8: f"={fixed_ref(3)}",
                11: f"={letter}{17+off}*{letter}{18+off}",
                12: f'=IF(VALUE(LEFT({letter}$3,4))<$B${21+off},0,{letter}{20+off}*{letter}{11+off})',
                13: f"='测算假设'!$B${price_start+6}/10000",
                14: f"={case(3)}",
                17: f'=IF(VALUE(LEFT({letter}$3,4))<$B${21+off},0,{case(1)}*{maturity})',
                18: f"={case(2)}",
                19: f"={source}",
                20: f"={letter}{10+off}*(1-{letter}{19+off})",
            }
            if europe_ratio:
                values[16] = sheet.cell(16 + off, c).value + sales_factor
            for row, value in values.items():
                fmt = "0.0%" if row in (7, 8, 11, 17, 18, 19) else "#,##0.00"
                set_input(sheet, row + off, c, value, fmt)
        last = col_letter(11)
        prior_mature = "0" if prior is None else mature_exposures[prior]
        mature_adoption = f"({case(1)}*{case(2)})"
        mature_exposures.append(f"({prior_mature}+(1-{prior_mature})*{mature_adoption})")
        set_input(sheet, 22 + off, 2,
                  f"={last}{10+off}*(1-{prior_mature})*{mature_adoption}*{last}{15+off}{sales_factor}", "0.00")
        for row in (6, 7, 8, 11, 13, 14):
            for c in range(2, 12):
                cell = sheet.cell(row + off, c)
                fmt = cell.number_format
                cell._style = copy(prototype.cell(10, c)._style)
                cell.number_format = fmt
    for c in ("N", "O", "P", "Q", "R"):
        sheet.column_dimensions[c].hidden = True
    sheet.print_options.horizontalCentered = True
    sheet.print_title_rows = "1:3"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    for idx in range(count):
        sheet.row_dimensions[5 + idx * BLOCK_ROWS].height = 23
    for row, label in (
        (total, f"年度终端销售合计（亿{currency_label}）"),
        (total + 1, f"预测期内最高值（亿{currency_label}）"),
        (total + 2, "最高值年份（并列取最早）"),
        (total + 3, f"成熟期销售（亿{currency_label}，末年患者池）"),
        (total + 4, "末年销售状态"),
    ):
        sheet.cell(row, 1).value = label
        for c in range(1, 14):
            sheet.cell(row, c)._style = copy(prototype.cell(23 if row == total else 10, c)._style)
        sheet.row_dimensions[row].height = 27
    for c in range(2, 12):
        letter = col_letter(c)
        refs = ",".join(f"{letter}{16 + i*BLOCK_ROWS}" for i in range(count))
        set_input(sheet, total, c, f"=SUM({refs})", "0.00")
    set_input(sheet, total + 1, 2, f"=MAX(B{total}:K{total})", "0.00")
    set_input(sheet, total + 2, 2, f'=IF(B{total+1}>0,INDEX(B3:K3,1,MATCH(B{total+1},B{total}:K{total},0)),"—")')
    mature = ",".join(f"B{22+i*BLOCK_ROWS}" for i in range(count))
    set_input(sheet, total + 3, 2, f"=SUM({mature})", "0.00")
    sheet["D2"], sheet["E2"] = "期内最高", f"=B{total+1}"
    sheet["F2"], sheet["G2"] = "最高年份", f"=B{total+2}"
    sheet["H2"], sheet["I2"] = "成熟期", f"=B{total+3}"
    sheet["E2"].number_format = sheet["I2"].number_format = "0.00"
    set_input(sheet, total + 4, 2, f'=IF(K{total}>J{total},"仍在增长","未继续增长")')
    if europe_ratio:
        sheet.merge_cells(start_row=total + 5, start_column=1, end_row=total + 5, end_column=13)
        sheet.cell(total + 5, 1, OVERSEAS_NOTE)
        sheet.row_dimensions[total + 5].height = 30
        for idx in range(count):
            off = idx * BLOCK_ROWS
            write_note_columns(sheet, 16 + off, 12, "美国用药人数 × 全疗程费用 ×（1＋欧洲折算比例）。")
            write_note_columns(sheet, 22 + off, 12, "以末年美国患者池和稳定采用率估算，再纳入欧洲折算。")
    return sheet, total, fx_ref


def build(spec, out):
    first = spec["first_year"]
    years = list(range(first, first+10))
    regions = spec["regions"]
    if not regions or len({r["name"] for r in regions}) != len(regions):
        raise ValueError("须提供至少一个地区且名称唯一")
    w = load_workbook(TEMPLATE_PATH)
    prototype = w["国内药物"]
    discarded = list(w.worksheets)
    europe_proxy = spec.get("europe_proxy", False)
    if europe_proxy and ("美国" not in {r["name"] for r in regions} or
                         any(r["name"].startswith(("欧洲", "欧盟")) for r in regions)):
        raise ValueError("欧洲折算须包含美国独立测算，且欧洲不能重复录入")
    summary = w.create_sheet("地区汇总") if len(regions) > 1 or europe_proxy else None
    assumptions = w.create_sheet("测算假设")
    assumptions.append([f"{spec['product']}｜测算假设"])
    assumptions.merge_cells("A1:F1")
    assumptions["A1"]._style = copy(prototype["A1"]._style)
    assumptions.row_dimensions[1].height = 28
    assumptions.append(["情景", spec.get("case", "中性")])
    assumptions["B2"]._style = copy(prototype["B8"]._style)
    assumptions.freeze_panes = "B3"
    assumptions.sheet_view.showGridLines = False
    for c, width in zip("ABCDEF", (40, 15, 15, 15, 67, 34)):
        assumptions.column_dimensions[c].width = width
    europe_ratio = None
    if europe_proxy:
        ratio_row = assumption_block(assumptions, prototype, "海外折算（数值见 B 列）", [
            ("欧洲／美国销售比例", [0.5], "0%", OVERSEAS_NOTE, ""),
        ])
        europe_ratio = f"'测算假设'!$B${ratio_row+1}"
    results = []
    for region in regions:
        if not region["indications"]:
            raise ValueError("每个地区至少录入一个已量化适应症")
        ratio = europe_ratio if region["name"] == "美国" else None
        results.append(write_region(w, prototype, spec["product"], region, years, assumptions, ratio))
    for sheet in discarded:
        w.remove(sheet)
    w.move_sheet(assumptions, offset=len(w.worksheets) - w.index(assumptions) - 1)
    if summary:
        summary.append(["各地区终端销售与同年汇总（人民币亿元）"])
        summary.append(["情景", "='测算假设'!B2"])
        summary.merge_cells("A1:P1")
        summary["A1"]._style = copy(prototype["A1"]._style)
        summary.row_dimensions[1].height = 28
        summary.append(["地区", *[f"{y}E" for y in years], "本币峰值（亿）", "峰值年", "币种", "每单位当地币折人民币", "汇率来源"])
        for idx, ((sheet, row, fx_ref), region) in enumerate(zip(results, regions), 4):
            name = sheet.title.replace("'", "''")
            if region["currency"] != "CNY" and (not region.get("fx_source") or region.get("fx", 0) <= 0):
                raise ValueError("非人民币地区汇总须有汇率来源")
            summary.append([sheet.title, *[f"='{name}'!{col_letter(c)}{row}*$O${idx}" for c in range(2, 12)],
                            f"='{name}'!B{row+1}", f"='{name}'!B{row+2}", region["currency"], f"={fx_ref}", "见测算假设"])
        total = 4 + len(results)
        summary.append(["同年销售合计", *[f"=SUM({col_letter(c)}4:{col_letter(c)}{total-1})" for c in range(2, 12)]])
        if europe_proxy:
            summary.merge_cells(start_row=total + 3, start_column=1, end_row=total + 3, end_column=16)
            summary.cell(total + 3, 1, OVERSEAS_NOTE)
            summary.row_dimensions[total + 3].height = 29
        summary.cell(total + 1, 1, "合计峰值")
        summary.cell(total + 1, 2, f"=MAX(B{total}:K{total})").number_format = "0.00"
        summary.cell(total + 2, 1, "合计峰值年")
        summary.cell(total + 2, 2, f'=IF(B{total+1}>0,INDEX(B3:K3,1,MATCH(B{total+1},B{total}:K{total},0)),"—")')
        for c in range(1,17):
            summary.column_dimensions[col_letter(c)].width = 19 if c != 1 else 35
            summary.cell(3,c)._style = copy(prototype.cell(3,1)._style)
        for row in summary.iter_rows(min_row=4, max_row=total, min_col=2, max_col=12):
            for cell in row:
                cell.number_format = "0.00"
        summary.freeze_panes = "B4"
        summary.sheet_view.showGridLines = False
    validation = DataValidation(type="list", formula1='"悲观,中性,乐观"', allow_blank=False)
    validation.showErrorMessage = True
    assumptions.add_data_validation(validation)
    validation.add("B2")
    w.active = 0
    format_dcf_typography(w)
    w.save(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(json.loads(args.inputs.read_text(encoding="utf-8-sig")), args.output)
