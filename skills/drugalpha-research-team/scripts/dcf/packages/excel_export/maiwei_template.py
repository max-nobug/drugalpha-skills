"""Fill the compact independent DCF drug sheets from the bundled workbook."""

from __future__ import annotations

from copy import copy
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from unicodedata import east_asian_width
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree
from zipfile import ZipFile

from openpyxl.cell.cell import MergedCell
from openpyxl.chart.text import RichText
from openpyxl.drawing.text import CharacterProperties, Font as DrawingFont, Paragraph, ParagraphProperties
from openpyxl.formula.translate import Translator
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from ..dcf_core.independent_revenue import PATIENT_BASIS_FIELDS


TEMPLATE_PATH = Path(__file__).resolve().parents[4] / "assets" / "dcf-valuation-template.xlsx"
PROTOTYPE_NAMES = {"china": "国内药物", "us": "海外药物"}
FIRST_BLOCK_ROW = 4
BLOCK_ROWS = 19
GROSS_SALES_ROW = 16
ATTRIBUTABLE_ROW = 20
FIRST_TOTAL_ROW = 23
FIRST_YEAR_COL = 2
LAST_YEAR_COL = 11
NOTE_FONT_NAME = "宋体"
NOTE_FONT_SIZE = 11
NOTE_FONT_COLOR = "222222"
NOTE_LINK_COLOR = "0563C1"


def _literal(sheet, row, column, value):
    cell = sheet.cell(row, column, value)
    if isinstance(value, str):
        cell.data_type = "s"


_URL = re.compile(r"https?://[^\s；，。）、）<>]+")


def _without_links(note):
    return _URL.sub("", note).strip()


def _note_height(sheet, row, col, *notes):
    lines = []
    for column, note in enumerate(notes, col):
        width = sheet.column_dimensions[get_column_letter(column)].width or 30
        lines.append(sum(
            max(1, 1 + sum(2 if east_asian_width(c) in {"W", "F"} else 1 for c in line) // width)
            for line in (note or "").split("\n")
        ))
    sheet.row_dimensions[row].height = min(409, 7 + 15 * max(lines))


def _dark_note_background(cell):
    color = cell.fill.fgColor
    return color.type == "rgb" and color.rgb.upper()[-6:] in {"A64045", "7D3038"}


def _style_note(cell):
    linked = bool(cell.hyperlink)
    color = "FFFFFF" if _dark_note_background(cell) else NOTE_LINK_COLOR if linked else NOTE_FONT_COLOR
    cell.font = Font(
        name=NOTE_FONT_NAME, size=NOTE_FONT_SIZE, color=color, underline="single" if linked else None,
    )
    cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)


def format_dcf_note_columns(workbook, company_key=None):
    """Normalize explanatory text in the tables and standalone notes."""
    explanation_headers = {"说明", "备注", "口径说明", "内容", "适应症/来源说明", "治疗状态/适用说明/示例"}
    source_headers = {
        "来源／依据", "来源 / 依据", "来源/依据", "来源/说明", "来源／说明",
        "原始来源", "关键假设与依据", "临床/公开来源记录", "临床证据/代表记录选择依据", "AI依据",
    }
    headers = explanation_headers | source_headers
    for sheet in workbook.worksheets:
        note_cols = {2} if sheet.title == "数据来源与声明" else set()
        source_cols = set()
        for cells in sheet.iter_rows():
            for cell in cells:
                if isinstance(cell.value, str) and cell.value in headers:
                    note_cols.add(cell.column)
                    if cell.value in source_headers:
                        source_cols.add(cell.column)
            label = str(cells[0].value or "")
            for cell in cells:
                if (
                    isinstance(cell, MergedCell) or cell.column not in note_cols
                    or not isinstance(cell.value, str) or cell.value in headers
                    or (cell.data_type == "f" and not cell.value.startswith('="'))
                    or (_dark_note_background(cell) and not any(item.data_type == "f" for item in cells))
                ):
                    continue
                if cell.column in source_cols:
                    _link_database_source(cell, label, company_key)
                _style_note(cell)

        if sheet.title == "DCF估值":
            for merged in sheet.merged_cells.ranges:
                if merged.min_row > 3 and merged.min_col == 1 and merged.max_col == sheet.max_column:
                    cell = sheet.cell(merged.min_row, 1)
                    if isinstance(cell.value, str) and not _dark_note_background(cell):
                        _style_note(cell)
        if sheet.title == "估值敏感性":
            for coordinate in ("A12", "E16", "E17"):
                cell = sheet[coordinate]
                if isinstance(cell.value, str) and cell.value not in headers:
                    _style_note(cell)
        if sheet.title == "历史财务数据":
            for coordinate in ("D3", "B4", "B43"):
                cell = sheet[coordinate]
                if isinstance(cell.value, str):
                    _style_note(cell)


def format_saved_dcf_comments(path):
    """Set the font on legacy Excel notes, which openpyxl writes as plain text."""
    path = Path(path)
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    tag = f"{{{namespace}}}"
    with TemporaryDirectory(dir=path.parent) as directory:
        updated = Path(directory) / path.name
        with ZipFile(path) as original:
            comment_parts = [name for name in original.namelist() if re.fullmatch(r"xl/comments/comment\d+\.xml", name)]
            if not comment_parts:
                return
            with ZipFile(updated, "w") as output:
                for entry in original.infolist():
                    data = original.read(entry)
                    if entry.filename in comment_parts:
                        root = ElementTree.fromstring(data)
                        for body in root.iter(tag + "text"):
                            plain = body.find(tag + "t")
                            if plain is None:
                                continue
                            body.remove(plain)
                            run = ElementTree.SubElement(body, tag + "r")
                            style = ElementTree.SubElement(run, tag + "rPr")
                            ElementTree.SubElement(style, tag + "rFont", val=NOTE_FONT_NAME)
                            ElementTree.SubElement(style, tag + "sz", val=str(NOTE_FONT_SIZE))
                            ElementTree.SubElement(style, tag + "color", rgb="FF" + NOTE_FONT_COLOR)
                            run.append(plain)
                        data = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
                    output.writestr(entry, data)
        updated.replace(path)


def format_dcf_typography(workbook):
    """Use one body font across sheets, including imported and appended sections."""
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                font = copy(cell.font)
                if _dark_note_background(cell) and font.name != NOTE_FONT_NAME:
                    font.name = "Microsoft YaHei"
                else:
                    font.name = NOTE_FONT_NAME
                    font.sz = NOTE_FONT_SIZE
                font.scheme = None
                font.charset = None
                font.family = None
                cell.font = font

        for chart in sheet._charts:
            for axis in (getattr(chart, "x_axis", None), getattr(chart, "y_axis", None)):
                if axis is None:
                    continue
                if axis.txPr is None:
                    axis.txPr = RichText(p=[Paragraph()])
                _set_chart_font(axis.txPr)
            for title in (chart.title, getattr(chart.x_axis, "title", None), getattr(chart.y_axis, "title", None)):
                if title is not None and title.tx is not None and title.tx.rich is not None:
                    _set_chart_font(title.tx.rich)


def _set_chart_font(text):
    for paragraph in text.p:
        if paragraph.pPr is None:
            paragraph.pPr = ParagraphProperties()
        if paragraph.pPr.defRPr is None:
            paragraph.pPr.defRPr = CharacterProperties()
        properties = [paragraph.pPr.defRPr]
        properties.extend(run.rPr for run in paragraph.r if run.rPr is not None)
        if paragraph.endParaRPr is not None:
            properties.append(paragraph.endParaRPr)
        for style in properties:
            style.latin = DrawingFont(typeface=NOTE_FONT_NAME)
            style.ea = DrawingFont(typeface=NOTE_FONT_NAME)
            style.sz = NOTE_FONT_SIZE * 100


def _link_database_source(cell, label, company_key):
    note = cell.value or ""
    source = re.search(r"(DrugAlpha(?:\s*数据库)?|iFinD)\s*MCP", note, re.I)
    if not source or re.match(
        r"[\s｜|：:]*[^；;。]{0,12}?(未取得|未返回|未回传|未覆盖|无可用|未命中|调用失败|价格服务未|缺全年)",
        note[source.end():],
    ):
        return

    origin = "https://drugalpha.com"
    links = ([cell.hyperlink.target] if cell.hyperlink and cell.hyperlink.target else []) + _URL.findall(note)
    database_links = [link for link in links if urlsplit(link).hostname in {"drugalpha.com", "www.drugalpha.com"}]
    evidence = re.split(r"[；;]", note[source.end():], maxsplit=1)[0]
    if source[1].lower() == "ifind":
        target = "https://www.51ifind.cn/"
    elif label.startswith(("月治疗费用", "月净价")):
        target = origin + "/ai-assistant?module=treatmentCost"
    elif label.startswith(("上市", "达峰概率", "海外达峰概率")) or re.search(r"管线|催化剂|研发进度|开发进度", evidence):
        pipeline_prefix = "/pipeline-tracking/companies/"
        target = next((link for link in database_links if urlsplit(link).path.startswith(pipeline_prefix)), None)
        if target is None:
            target = origin + (pipeline_prefix + quote(str(company_key), safe="") if company_key else "/pipeline-tracking")
    elif "渗透率" in label and "竞争格局" in evidence:
        target = next((link for link in database_links if urlsplit(link).path.startswith("/competitive-landscape")), origin + "/competitive-landscape")
    elif database_links:
        target = database_links[0]
    elif "知识库" in evidence:
        target = origin + "/knowledge"
    elif "竞争格局" in evidence:
        target = origin + "/competitive-landscape"
    elif "临床" in evidence:
        target = origin + "/clinical-database"
    else:
        target = origin
    cell.value = _without_links(note).rstrip("；;")
    cell.hyperlink = target


def write_note_columns(sheet, row, col, explanation, evidence="", *, base=None):
    """Write model choices on the left and evidence on the right."""
    explanation = "；".join(part for part in (base, explanation) if part)
    links = _URL.findall(evidence)
    text = evidence
    if len(set(links)) == 1:
        text = _without_links(evidence) or links[0]
    for column, value in ((col, explanation), (col + 1, text)):
        _literal(sheet, row, column, value)
        cell = sheet.cell(row, column)
        if column == col + 1 and len(set(links)) == 1:
            cell.hyperlink = links[0]
        _style_note(cell)
    _note_height(sheet, row, col, explanation, text)


def write_target_patient_ratio_note(sheet, row, explanation=""):
    """Show the formula and live values from the same cells as the patient model."""
    factors = [sheet.cell(row, col) for col in (14, 15, 16)]
    formula_label = "目标治疗人群比例 = " + " × ".join(
        PATIENT_BASIS_FIELDS[key] for key in ("clinical_fit_ratio", "target_line_ratio", "access_ratio")
    )
    terms = [f'TEXT({cell.coordinate},"0.0#%")' for cell in factors]
    note = f'"{formula_label}"&CHAR(10)&' + '&" × "&'.join(terms)
    note += f'&" = "&TEXT(B{row},"0.0%")'
    if explanation:
        # Excel limits each quoted formula literal to 255 characters.
        fragments = [explanation[i:i + 100] for i in range(0, len(explanation), 100)]
        literals = ['"' + fragment.replace('"', '""') + '"' for fragment in fragments]
        note += '&CHAR(10)&' + '&'.join(literals)
    cell = sheet.cell(row, 12)
    cell.value = "=" + note
    _style_note(cell)
    display = "\n".join(filter(None, (
        formula_label,
        " × ".join(f"{cell.value:.2%}" if isinstance(cell.value, (int, float)) else "100.00%"
                   for cell in factors) + " = 100.0%",
        explanation,
    )))
    evidence = sheet.cell(row, 13).value
    _note_height(sheet, row, 12, display, evidence)


def market_notes(market, fields):
    """Return descriptions and attributable sources, including old mixed-string inputs."""
    descriptions = market.get("revenue_input_assumptions")
    sources = market["revenue_input_sources"]
    evidence = market.get("revenue_input_evidence", {})
    if descriptions is None:
        desc, cited = [], []
        for key in fields:
            note = sources.get(key, "")
            match = re.match(r"DrugAlpha\s*数据库\s*MCP(?:[｜|][^。；，;：:]+)?", note)
            if match:
                cited.append(match.group(0))
                desc.append(_without_links(note[match.end():]).lstrip("。；，： "))
            else:
                desc.append(_without_links(note))
            cited.extend(_URL.findall(note))
    else:
        desc = [descriptions.get(key, "") for key in fields]
        cited = [evidence.get(key, "") for key in fields]
    explanation = "；".join(dict.fromkeys(part for part in desc if part))
    citation = "；".join(dict.fromkeys(part for part in cited if part))
    return explanation, citation


def _copy_rows(source, destination, first_row, count, offset):
    for row in range(first_row, first_row + count):
        for cell in source[row]:
            if isinstance(cell, MergedCell):
                continue
            target = destination.cell(row + offset, cell.column)
            if cell.data_type == "f":
                target.value = Translator(cell.value, origin=cell.coordinate).translate_formula(target.coordinate)
            else:
                target.value = cell.value
                if cell.data_type == "s":
                    target.data_type = "s"
            target._style = copy(cell._style)
        destination.row_dimensions[row + offset].height = source.row_dimensions[row].height


def write_compact_drug_sheet(workbook, name, product, entries, years, market_key):
    """Return the sheet, main revenue row, and optional success-condition revenue row."""
    if len(years) != LAST_YEAR_COL - FIRST_YEAR_COL + 1:
        raise ValueError("The compact DCF workbook uses ten forecast years")
    if not all(market["patient_flow"].get("patient_basis") for _, market in entries):
        raise ValueError(f"All indications on {name} must use patient_basis for the compact layout")
    currencies = {market["currency"] for _, market in entries}
    europe_ratios = {market["europe_ratio"] for _, market in entries}
    if len(currencies) != 1 or len(europe_ratios) != 1:
        raise ValueError(f"Indications on {name} must use a common currency and Europe/US ratio")
    currency = currencies.pop()
    europe_ratio = europe_ratios.pop()
    overseas = market_key == "us"
    prototype = workbook[PROTOTYPE_NAMES[market_key]]
    sheet = workbook.copy_worksheet(prototype)
    sheet.title = name
    sheet.freeze_panes = "B4"
    sheet.sheet_view.showGridLines = False
    sheet.delete_rows(FIRST_TOTAL_ROW, sheet.max_row - FIRST_TOTAL_ROW + 1)
    _literal(sheet, 1, 1, f"{product.get('drug_name') or name}（{'海外' if overseas else '中国'}） | {product.get('target') or ''}")
    for column, year in enumerate(years, FIRST_YEAR_COL):
        _literal(sheet, 3, column, f"{year}E")

    for index, (indication, market) in enumerate(entries):
        offset = index * BLOCK_ROWS
        if offset:
            _copy_rows(prototype, sheet, FIRST_BLOCK_ROW, BLOCK_ROWS, offset)
            sheet.merge_cells(start_row=FIRST_BLOCK_ROW + offset, start_column=1, end_row=FIRST_BLOCK_ROW + offset, end_column=13)
        _write_inputs(sheet, offset, indication, market, currency, overseas)

    total_start = FIRST_TOTAL_ROW + BLOCK_ROWS * (len(entries) - 1)
    total_count = 6 if overseas else 2
    _copy_rows(prototype, sheet, FIRST_TOTAL_ROW, total_count, total_start - FIRST_TOTAL_ROW)
    if overseas:
        ratio_row = total_start + 1
        sheet.cell(ratio_row, 18, europe_ratio)
        notes = [market_notes(market, ("europe_ratio",)) for _, market in entries]
        explanation, evidence = ("；".join(dict.fromkeys(part for part in column if part)) for column in zip(*notes))
        scope_note = "欧洲未纳入本次估值，比例为 0。纳入欧洲情景时按美国的 50% 测算。"
        sales_note = "欧洲销售额＝美国销售额 × 欧洲／美国比例"
        write_note_columns(sheet, ratio_row, 12, explanation, evidence,
                           base=scope_note if europe_ratio == 0 else sales_note)
        _literal(sheet, total_start + 4, 12, scope_note if europe_ratio == 0
                 else "美国风险/权益调整收入 × 欧洲 / 美国比例")
    conditional_indices = [i for i, (_, market) in enumerate(entries) if market.get("conditional_only")]
    modeled_indices = [i for i in range(len(entries)) if i not in conditional_indices]
    conditional_row = total_start + total_count if conditional_indices else None
    if conditional_row:
        _literal(sheet, conditional_row, 1, "成功条件归属收入（人民币亿元）")
        write_note_columns(sheet, conditional_row, 12, "仅汇总成功概率待定的适应症，按成功条件计算。")
    for column in range(FIRST_YEAR_COL, LAST_YEAR_COL + 1):
        letter = get_column_letter(column)
        for base_row, target_row, indices in (
            (GROSS_SALES_ROW, total_start, range(len(entries))),
            (ATTRIBUTABLE_ROW, total_start + (3 if overseas else 1), modeled_indices),
        ):
            cells = ",".join(f"{letter}{base_row + BLOCK_ROWS * i}" for i in indices)
            sheet.cell(target_row, column, f"=SUM({cells})" if cells else None)
        if conditional_row:
            cells = ",".join(f"{letter}{ATTRIBUTABLE_ROW + BLOCK_ROWS * i}" for i in conditional_indices)
            regional_factor = f"*(1+$R${ratio_row})" if overseas else ""
            sheet.cell(conditional_row, column, f"=SUM({cells}){regional_factor}")
            sheet.cell(conditional_row, column).number_format = "#,##0.0"
        if overseas:
            for offset, formula in (
                (1, f"={letter}{total_start}*$R${ratio_row}"),
                (2, f"=SUM({letter}{total_start}:{letter}{total_start + 1})"),
                (4, f"={letter}{total_start + 3}*$R${ratio_row}"),
                (5, f"=SUM({letter}{total_start + 3}:{letter}{total_start + 4})"),
            ):
                sheet.cell(total_start + offset, column, formula)
    return sheet, total_start + total_count - 1, conditional_row


def _write_inputs(sheet, offset, indication, market, currency, overseas):
    flow = market["patient_flow"]
    basis = flow["patient_basis"]
    source = market["revenue_input_sources"]
    _literal(sheet, 4 + offset, 1, f"{indication['indication_text']} | {'美国基准' if overseas else '中国'}")
    currency_name = {"CNY": "元", "USD": "美元"}.get(currency, currency)
    for row, label in {
        13: f"月治疗费用（万{currency_name}）",
        15: f"全疗程费用（万{currency_name}）",
        16: f"终端销售（亿{currency_name}）",
        18: "分成比例" if overseas else "归属比例",
    }.items():
        _literal(sheet, row + offset, 1, label)
    if market.get("conditional_only"):
        _literal(sheet, 17 + offset, 1, "成功条件权重（概率待定）")
        _literal(sheet, 20 + offset, 1, "成功条件归属收入（人民币亿元）")
    for column, key in enumerate(("clinical_fit_ratio", "target_line_ratio", "access_ratio"), 14):
        sheet.cell(9 + offset, column, basis[key])
    sheet.cell(20 + offset, 17, market["economic_ownership"])
    sheet.cell(21 + offset, 2, market["launch_year"])

    use_direct_penetration = "product_penetration" in source
    notes = {
        6: ("patient_population",),
        8: ("biomarker_ratio",),
        9: ("clinical_fit_ratio", "target_line_ratio", "access_ratio"),
        11: ("product_penetration",) if use_direct_penetration else ("class_penetration", "product_class_share"),
        13: ("monthly_net_price",),
        14: ("treatment_months",),
        17: ("success_probability",),
        18: ("economic_share",),
        19: ("exchange_rate",),
        20: ("economic_ownership",),
        21: ("launch_year",),
    }
    for row, fields in notes.items():
        explanation, evidence = market_notes(market, fields)
        if row == 11 and market.get("penetration_basis") != "observed":
            explanation = "【自主假设】" + explanation
        if row == 19 and not overseas and not explanation:
            explanation = "人民币计价"
        target_row = row + offset
        if row == 9:
            write_note_columns(sheet, target_row, 12, "", evidence)
            write_target_patient_ratio_note(sheet, target_row, explanation)
        else:
            write_note_columns(sheet, target_row, 12, explanation, evidence, base=sheet.cell(target_row, 12).value)
    share_key = "revenue_share_ratio" if overseas else "equity_ratio"
    for index, population in enumerate(basis["patient_population"]):
        column = FIRST_YEAR_COL + index
        penetration = flow["penetration_curve"][index] * flow["product_share_curve"][index]
        for row, value in {
            6: population / 10000,
            8: basis["biomarker_ratio"],
            11: penetration,
            13: flow["monthly_price"][index],
            14: flow["treatment_months"][index],
            17: 1 if market.get("conditional_only") else market["success_probability"],
            18: market[share_key],
            19: market["exchange_rate"],
        }.items():
            sheet.cell(row + offset, column, value)
        price = flow["monthly_price"][index]
        if 0 < price < 0.05:
            sheet.cell(13 + offset, column).number_format = "#,##0.0000"
