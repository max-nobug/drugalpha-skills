from pathlib import Path
from datetime import date
import re
import argparse
import os
import json
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from docx import Document
from docx.enum.section import WD_SECTION, WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Mm, Pt, RGBColor

doc = Document()
doc.core_properties.author = 'DrugAlpha 投研'
font = 'Microsoft YaHei'
pattern = re.compile(r'\[([^\]]+)\]\((<[^>]+>|(?:[^()\s]|\([^()\s]*\))+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*')

def set_font(target, size, color='252B35', bold=False):
    target.font.name = font
    target.font.size = Pt(size)
    target.font.bold = bold
    target.font.color.rgb = RGBColor.from_string(color)
    rpr = target.element.get_or_add_rPr() if hasattr(target, 'element') else target._element.get_or_add_rPr()
    rpr.rFonts.set(qn('w:eastAsia'), font)

def style(name, size, color, bold, before, after, spacing):
    s = doc.styles[name]
    set_font(s, size, color, bold)
    p = s.paragraph_format
    p.space_before, p.space_after, p.line_spacing = Pt(before), Pt(after), spacing
    p.widow_control = True

style('Normal', 10, '252B35', False, 0, 6, Pt(15))
style('Title', 27, '000000', True, 0, 14, 1.15)
style('Subtitle', 9, '596575', False, 0, 22, 1.25)
style('Heading 1', 17, '000000', True, 0, 8, 1.14)
style('Heading 2', 13, '000000', True, 8, 6, 1.13)
style('Heading 3', 12, '000000', True, 8, 6, 1.16)
style('Footer', 8, '657184', False, 0, 0, 1)
for name in ('Title','Subtitle','Heading 1','Heading 2','Heading 3'):
    props = doc.styles[name].element.get_or_add_pPr()
    for border in props.findall(qn('w:pBdr')):
        props.remove(border)

def setup(section, landscape=False):
    section.orientation = WD_ORIENT.LANDSCAPE if landscape else WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = (Mm(297), Mm(210)) if landscape else (Mm(210), Mm(297))
    section.left_margin = section.right_margin = Mm(20)
    section.top_margin = Mm(15 if landscape else 16)
    section.bottom_margin = Mm(15 if landscape else 15)
    section.footer_distance = Mm(8)

setup(doc.sections[0])
footer = doc.sections[0].footer.paragraphs[0]
footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
field = OxmlElement('w:fldSimple')
field.set(qn('w:instr'), 'PAGE')

def add_link(paragraph, label, url, size):
    url = url.strip('<>')
    if url.startswith(('./', '../')):
        parts = urlsplit(url)
        if source_dir != output_dir:
            target = (source_dir / unquote(parts.path)).resolve()
            try:
                path = Path(os.path.relpath(target, output_dir)).as_posix()
            except ValueError:
                url = target.as_uri()
            else:
                path = path if path.startswith('../') else './' + path
                url = urlunsplit(('', '', quote(path, safe='/.-_'), parts.query, parts.fragment))
        else:
            url = urlunsplit(('', '', quote(parts.path, safe='/.-_%'), parts.query, parts.fragment))
    link = OxmlElement('w:hyperlink')
    link.set(qn('r:id'), paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True))
    run = OxmlElement('w:r')
    props = OxmlElement('w:rPr')
    for key, attrs in (
        ('rFonts', {'ascii': font, 'hAnsi': font, 'eastAsia': font, 'cs': font}),
        ('color', {'val': '145885'}),
        ('sz', {'val': str(int(size * 2))}),
        ('u', {'val': 'single'}),
    ):
        node = OxmlElement('w:' + key)
        for k, v in attrs.items():
            node.set(qn('w:' + k), v)
        props.append(node)
    txt = OxmlElement('w:t')
    txt.text = label
    run.append(props)
    run.append(txt)
    link.append(run)
    paragraph._p.append(link)

def inline(p, value, size=None, color=None, bold=None):
    def write(t, strong=None, italic=False):
        if not t:
            return
        r = p.add_run(t)
        if size is not None:
            set_font(r, size, color or '252B35', bold or bool(strong))
        elif strong is not None:
            r.bold = strong
        if italic:
            r.italic = True
    pos = 0
    for m in pattern.finditer(value):
        write(value[pos:m.start()])
        if m.group(1):
            add_link(p, m.group(1), m.group(2), size or 9)
        elif m.group(3):
            write(m.group(3), True)
        else:
            write(m.group(4), italic=True)
        pos = m.end()
    write(value[pos:])

def border(parent, name, attributes):
    item = OxmlElement('w:' + name)
    for k,v in attributes.items():
        item.set(qn('w:' + k), str(v))
    parent.append(item)
    return item

def make_table(rows, widths, numeric=(), wide=False):
    t = doc.add_table(rows=0, cols=len(widths))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for col, width in zip(t.columns, widths):
        col.width = Mm(width)
    borders = border(t._tbl.tblPr, 'tblBorders', {})
    for edge in ('top','left','bottom','right','insideH','insideV'):
        border(borders, edge, {'val':'single','sz':4,'color':'D8DFE8'})
    margins = border(t._tbl.tblPr,'tblCellMar',{})
    for edge, value in (('top',45 if wide else 42),('bottom',45 if wide else 42),('left',95),('right',95)):
        border(margins, edge, {'w':value,'type':'dxa'})
    for ri, values in enumerate(rows):
        row = t.add_row()
        border(row._tr.get_or_add_trPr(), 'cantSplit', {})
        if ri == 0:
            border(row._tr.get_or_add_trPr(), 'tblHeader', {'val': 'true'})
        subtotal = values[0].startswith('**')
        for ci, (c, val) in enumerate(zip(row.cells, values)):
            c.width = Mm(widths[ci])
            c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            bg = 'E1EAF4' if ri == 0 else 'EAF1F8' if subtotal else 'F6F8FB' if ri % 2 == 0 else 'FFFFFF'
            border(c._tc.get_or_add_tcPr(),'shd',{'fill':bg})
            p = c.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = Pt(14 if wide else 13)
            if ci in numeric:
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            inline(p,val,10 if wide else 9,'142A44' if ri == 0 or subtotal else '252B35',ri==0 or subtotal)
    return t

def new_section(landscape):
    section = doc.add_section(WD_SECTION.NEW_PAGE)
    setup(section, landscape)
    p = doc.paragraphs[-1]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = Pt(2)


TABLE_LAYOUTS = {
    '指标': (40, 26, 26, 78),
    '桥接项目': (48, 30, 92),
    '测算项': (27, 67, 38, 38),
    '检查时点': (64, 21, 21, 64),
    '事项': (32, 71, 67),
    '情景': (68, 51, 51),
    '核心资产': (34, 57, 79),
    '资产': (40, 29, 21, 29, 17, 34),
    '估值口径': (70, 50, 50),
    '风险': (42, 66, 62),
}
HORIZONTAL = '<!-- landscape -->'
ROW_SPLIT = re.compile(r'(?<!\\)\|')
SEPARATOR = re.compile(r':?-{3,}:?')
HEADING = re.compile(r'^(#{1,3})\s+(.+)$')
MAJOR_HEADING = re.compile(r'^0([1-6])(?:\s|[｜|])')

def table_rows(lines):
    cells = [[part.strip().replace(r'\|', '|') for part in ROW_SPLIT.split(line.strip().strip('|'))] for line in lines]
    if len(cells) < 2 or not all(SEPARATOR.fullmatch(part) for part in cells[1]):
        raise ValueError('表格第二行需要 Markdown 表头分隔符。')
    if any(len(row) != len(cells[0]) for row in cells):
        raise ValueError('Markdown 表格各行列数不同，请检查未转义的竖线。')
    return [cells[0], *cells[2:]], tuple(i for i, cell in enumerate(cells[1]) if cell.endswith(':'))

def blocks(text):
    lines = text.splitlines()
    i = 0
    wide = False
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue
        if line == HORIZONTAL:
            wide = True
            continue
        heading = HEADING.match(line)
        if heading:
            yield f'h{len(heading[1])}', heading[2], False
        elif line.startswith('|'):
            raw = [line]
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                raw.append(lines[i].strip())
                i += 1
            rows, numeric = table_rows(raw)
            yield 'table', (rows, numeric), wide or len(rows[0]) >= 7
            wide = False
        else:
            yield 'text', line, False
    if wide:
        raise ValueError('横向页标记后缺少表格。')

def layout(rows, numeric, wide):
    count = len(rows[0])
    if wide:
        first = 56 if count > 2 else 95
        widths = (first,) + ((257 - first) / (count - 1),) * (count - 1)
    elif rows[0][0] == '指标' and count == 4 and '单季' in rows[0][-1]:
        widths = (56, 36, 36, 42)
        numeric = (1, 2, 3)
    elif rows[0][0] == '项目' and count == 3:
        widths = (40, 60, 70)
    else:
        widths = TABLE_LAYOUTS.get(rows[0][0], ())
        if len(widths) != count:
            widths = (170 / count,) * count
    return widths, numeric

def summary_table(report_blocks, kind):
    for typ, content, wide in report_blocks:
        if typ != 'table' or wide:
            continue
        rows, _ = content
        if kind == 'valuation' and rows[0][0] == '估值口径':
            return '估值情景与市场对照', rows[:5], (70, 50, 50)
        if kind == 'financial' and rows[0][0] == '检查时点' and len(rows[0]) >= 3:
            items = rows[1:]
            selected = [items[i] for i in (1, 3, 4, 5)] if len(items) == 6 else items[:4]
            return '关键时点资金余额', [['观察时点', '基础情景', '压力情景'], *[r[:3] for r in selected]], (90, 40, 40)
    return None

def add_summary(report_blocks, kind):
    data = summary_table(report_blocks, kind)
    if data:
        label, rows, widths = data
        doc.add_paragraph(label, style='Heading 2')
        make_table(rows, widths, (1, 2))
        if kind == 'financial':
            p = doc.add_paragraph('单位为人民币亿元。')
            p.paragraph_format.space_before = Pt(7)

def set_all_fonts():
    # Keep the same family in every Word script range, including inherited styles.
    roots = [doc.styles.element, doc._element, doc.sections[0].footer._element]
    for root in roots:
        for element in root.xpath('.//w:rFonts'):
            for attr in tuple(element.attrib):
                del element.attrib[attr]
            for attr in ('ascii', 'hAnsi', 'eastAsia', 'cs'):
                element.set(qn('w:' + attr), font)

def report_title(raw):
    title = re.sub(r'[（(][0-9A-Z.\-]+[）)]', '', raw)
    return title.strip()

def validate_valuation_outline(report_blocks):
    expected = [
        ('h2', r'01\|摘要'),
        ('h2', r'02\|.+独立估值分析\((?:DCF|rNPV)\)'),
        ('h3', r'2\.1\|估值方法与结果'),
        ('h3', r'2\.2\|核心资产与估值假设'),
        ('h2', r'03\|参考估值:DrugAlpha平台估值'),
        ('h3', r'3\.1\|国内估值'),
        ('h3', r'3\.2\|海外估值'),
        ('h3', r'3\.3\|与独立估值的差异'),
        ('h2', r'04\|估值空间与市场隐含预期'),
        ('h2', r'05\|风险提示'),
    ]
    punctuation = str.maketrans('｜：（）', '|:()')
    outline = [(kind, re.sub(r'\s+', '', heading.translate(punctuation)))
               for kind, heading, _ in report_blocks if kind in ('h2', 'h3')]
    if len(outline) != len(expected) or any(
        kind != expected_kind or not re.fullmatch(pattern, heading)
        for (kind, heading), (expected_kind, pattern) in zip(outline, expected)
    ):
        raise ValueError('估值报告标题和子节须与 references/valuation-report-template.md 一致。')

CLINICAL_SECTIONS = (
    '01｜摘要',
    '02｜研究范围与证据',
    '03｜核心临床数据对比',
    '04｜关键发现与临床竞争定位',
    '05｜证据局限与后续验证',
)

CLINICAL_SECTIONS_EN = (
    '01 | Summary',
    '02 | Scope and evidence',
    '03 | Clinical data comparison',
    '04 | Key findings and clinical positioning',
    '05 | Evidence gaps and follow-up',
)

PEAK_SECTIONS = (
    '01｜摘要',
    '02｜测算范围与患者池',
    '03｜渗透率、份额、价格与疗程',
    '04｜逐年销售与适应症贡献',
    '05｜敏感性与验证节点',
)

CATALYST_SECTIONS = (
    '01｜核心判断',
    '02｜关键催化剂',
    '03｜决定性结果与判断门槛',
    '04｜同期竞品与时间变化',
    '05｜风险与待验证事项',
)

TECHNICAL_SECTIONS = ('01｜核心判断', '02｜趋势与价格结构', '03｜关键价位与验证条件', '04｜情景判断与后续观察', '05｜风险与适用边界')

INTEGRATED_SECTIONS = {
    'integrated-single': (
        '01｜核心判断', '02｜未来核心催化剂', '03｜核心资产与竞争地位',
        '04｜销售峰值与关键假设', '05｜独立估值与市场隐含预期', '06｜风险提示',
    ),
    'integrated-multi': (
        '01｜筛选结论', '02｜核心催化剂横向对比', '03｜排序依据与主要约束',
        '04｜估值与市场预期', '05｜后续研究清单', '06｜风险提示',
    ),
}

COMPETITION_SECTIONS = (
    '01｜摘要',
    '02｜竞品全景',
    '03｜分人群竞争格局',
    '04｜竞争判断',
    '05｜可能改变格局的事件与证据缺口',
)
COMPETITION_PRODUCT_COLUMNS = ('公司', '药物', '靶点／机制', '适应症', '研发阶段')

def validate_competition_outline(report_blocks):
    sections = [index for index, (typ, _, _) in enumerate(report_blocks) if typ == 'h2']
    if tuple(report_blocks[index][1].strip() for index in sections) != COMPETITION_SECTIONS:
        raise ValueError('竞争格局报告须使用模板的五节标题。')
    overview = report_blocks[sections[1] + 1:sections[2]]
    tables = [value[0] for typ, value, _ in overview if typ == 'table']
    if not tables or tuple(tables[0][0]) != COMPETITION_PRODUCT_COLUMNS or len(tables[0]) < 2:
        raise ValueError('第 02 节须先列含产品行的五列竞品主表。')

def render(source_path, out_path, kind, language="zh"):
    global source_dir, output_dir
    source_dir, output_dir = source_path.resolve().parent, out_path.resolve().parent
    report_blocks = list(blocks(source_path.read_text(encoding='utf-8-sig')))
    titles = [x for typ, x, _ in report_blocks if typ == 'h1']
    majors = [int(m[1]) for typ, x, _ in report_blocks if typ == 'h2' and (m := MAJOR_HEADING.match(x))]
    section_count = len(INTEGRATED_SECTIONS[kind]) if kind in INTEGRATED_SECTIONS else 5
    if len(titles) != 1 or majors != list(range(1, section_count + 1)):
        raise ValueError(f'需要一个报告标题，以及按顺序排列的 01 至 {section_count:02d} 节。')
    if kind in INTEGRATED_SECTIONS:
        sections = tuple(value.strip() for typ, value, _ in report_blocks if typ == 'h2')
        if kind == 'integrated-multi' and sections[0] == '01｜比较结论':
            sections = ('01｜筛选结论', *sections[1:])
        if sections != INTEGRATED_SECTIONS[kind]:
            raise ValueError('综合决策报告须使用对应的章节模板。')
    if kind == 'valuation':
        validate_valuation_outline(report_blocks)
    if kind == 'competition':
        validate_competition_outline(report_blocks)
    if kind == 'peak' and tuple(value.strip() for typ, value, _ in report_blocks if typ == 'h2') != PEAK_SECTIONS:
        raise ValueError('销售峰值报告须使用模板的五节标题。')
    if kind == 'catalyst' and tuple(value.strip() for typ, value, _ in report_blocks if typ == 'h2') != CATALYST_SECTIONS:
        raise ValueError('催化剂报告须使用模板的五节标题。')
    if kind == 'technical' and tuple(v.strip() for typ, v, _ in report_blocks if typ == 'h2') != TECHNICAL_SECTIONS:
        raise ValueError('技术面报告须使用五节模板。')
    if kind == 'clinical':
        sections = tuple(value.strip() for typ, value, _ in report_blocks if typ == 'h2')
        expected_sections = CLINICAL_SECTIONS_EN if language == 'en' else CLINICAL_SECTIONS
        if sections != expected_sections:
            raise ValueError('临床报告五节标题须与 references/clinical-report-template.md 一致。')
        section_headings = [index for index, (typ, _, _) in enumerate(report_blocks) if typ == 'h2']
        findings = report_blocks[section_headings[3] + 1:section_headings[4]]
        expected_subheadings = (('Data strengths', 'Data limitations') if language == 'en'
                                else ('数据亮点', '数据短板'))
        subheadings = [(index, value) for index, (typ, value, _) in enumerate(findings)
                       if typ == 'h3']
        if tuple(value for _, value in subheadings) != expected_subheadings:
            raise ValueError('临床报告第 04 节须依次分列三级标题「数据亮点」和「数据短板」。')
        first, second = (index for index, _ in subheadings)
        for label, content in zip(expected_subheadings, (findings[first + 1:second],
                                                        findings[second + 1:])):
            if not any(typ in ('text', 'table') and value for typ, value, _ in content):
                raise ValueError(f'临床报告第 04 节「{label}」须有逐药内容。')
    title = report_title(titles[0])
    security = re.search(r'[（(]([0-9A-Z.\-]+)[）)]', titles[0])
    doc.core_properties.title = title
    if language == "en":
        doc.core_properties.author = "DrugAlpha Research"
        doc.core_properties.language = "en"
    footer.add_run(title + '   ·   ')
    footer._p.append(field)
    landscape = False
    last_wide = False
    summary_has_table = False
    major = None
    i = 0
    while i < len(report_blocks):
        typ, value, wide = report_blocks[i]
        next_block = report_blocks[i + 1] if i + 1 < len(report_blocks) else ('', None, False)
        i += 1
        caption = typ == 'text' and value.startswith('**') and value.endswith('**') and next_block[2]
        if caption or (typ == 'table' and wide):
            if not landscape:
                new_section(True)
                landscape = True
            if caption:
                p = doc.add_paragraph(value[2:-2].replace('（亿元）', '  亿元'), style='Heading 1')
                p.paragraph_format.page_break_before = last_wide
                p.paragraph_format.keep_with_next = True
                if kind in ('financial', 'valuation'):
                    subtitle = '逐季现金收支与到期债务' if kind == 'financial' else '估值测算明细'
                    doc.add_paragraph(subtitle, style='Subtitle').paragraph_format.space_after = Pt(8)
                last_wide = False
                continue
            if last_wide:
                doc.add_page_break()
        elif landscape and not (typ == 'text' and value.startswith('*期末余额')):
            new_section(False)
            landscape = False
            if typ == 'table' and kind == 'financial':
                doc.add_paragraph('现金覆盖与融资判断', style='Heading 2')

        last_wide = typ == 'table' and wide
        if typ == 'h1':
            doc.add_paragraph(title, style='Title')
        elif typ == 'h2':
            previous = major
            match = MAJOR_HEADING.match(value)
            major = int(match[1]) if match else None
            if previous == 1 and not summary_has_table:
                add_summary(report_blocks, kind)
            p = doc.add_paragraph(value.replace('｜', '  '), style='Heading 1')
            page_starts = {'financial': {2, 3}, 'valuation': {2, 3, 4}, 'clinical': {2}, 'competition': {2}, 'peak': set(), 'catalyst': set()}
            p.paragraph_format.page_break_before = major in page_starts.get(kind, set())
        elif typ == 'h3':
            p = doc.add_paragraph(value.replace('｜', '  '), style='Heading 2')
            p.paragraph_format.page_break_before = kind == 'financial' and value.startswith('4.2')
        elif typ == 'table':
            rows, numeric = value
            widths, numeric = layout(rows, numeric, wide)
            make_table(rows, widths, numeric, wide)
            summary_has_table = summary_has_table or major == 1
            doc.add_paragraph().paragraph_format.line_spacing = Pt(3)
            doc.paragraphs[-1].paragraph_format.space_after = Pt(3)
        else:
            if value.startswith(('报告日期', 'Report date')):
                if security:
                    value = security[1] + '  |  ' + value
                doc.add_paragraph(value, style='Subtitle').paragraph_format.space_after = Pt(12)
                continue
            p = doc.add_paragraph()
            if value.startswith('- '):
                p.paragraph_format.left_indent = Mm(4)
                value = '•  ' + value[2:]
            inline(p, value, 9 if landscape else None)

    set_all_fonts()
    doc.save(out_path)

def validate_valuation_delivery(markdown_path, scope_path):
    from dcf.packages.excel_export.scope_check import validate_workbook_scope

    source = markdown_path.read_text(encoding="utf-8-sig")
    workbook_paths = set()
    for match in pattern.finditer(source):
        url = match.group(2)
        if not url:
            continue
        path = urlsplit(url.strip("<>"))
        if path.scheme or path.netloc:
            continue
        relative = Path(unquote(path.path))
        if relative.suffix.lower() == ".xlsx" and not relative.is_absolute():
            workbook_paths.add((markdown_path.parent / relative).resolve())
    if len(workbook_paths) != 1:
        raise ValueError("估值 Markdown 须用相对链接指向一份正式 DCF Excel。")
    scope = json.loads(scope_path.read_text(encoding="utf-8-sig"))
    return validate_workbook_scope(workbook_paths.pop(), scope, require_generator=True)


def main():
    parser = argparse.ArgumentParser(description='将临床、竞争格局、销售峰值、催化剂、财务、估值或综合决策 Markdown 排版为 Word。')
    parser.add_argument('markdown', type=Path)
    parser.add_argument('--kind', required=True, choices=('financial', 'valuation', 'clinical', 'competition', 'peak', 'catalyst', 'technical', *INTEGRATED_SECTIONS))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--language', choices=('zh', 'en'), default='zh',
                        help='Report language. English formatting is available for clinical reports.')
    parser.add_argument('--report-date', type=date.fromisoformat, help='报告日期 YYYY-MM-DD，默认读取 Markdown 中的报告日期')
    parser.add_argument('--dcf-scope', type=Path, help='本次估值跟踪的独立范围 JSON，正式估值报告必填')
    parser.add_argument('--draft', action='store_true', help='生成明确标注范围的单公司快评或独立DCF待补阶段稿')
    args = parser.parse_args()
    if args.language == "en" and args.kind != "clinical":
        parser.error("--language en currently supports clinical reports.")
    if args.draft and args.kind != 'integrated-single':
        parser.error('--draft 仅用于单公司投资研判。')
    if args.draft:
        draft_text = args.markdown.read_text(encoding='utf-8-sig')
        if not any(label in draft_text for label in ('快评', 'DCF 待补算', 'DCF待补算')):
            parser.error('阶段稿须注明快评范围或 DCF 待补算。')
    if args.kind == 'valuation' or (args.kind == 'integrated-single' and not args.draft):
        if args.dcf_scope is None:
            parser.error('正式估值或单公司投资研判需要 --dcf-scope，以核对正式 Excel 的全部入选药物×地区。')
        try:
            validate_valuation_delivery(args.markdown, args.dcf_scope)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    out = args.output
    if out is None:
        report_blocks = list(blocks(args.markdown.read_text(encoding='utf-8-sig')))
        title = next((value for kind, value, _ in report_blocks if kind == 'h1'), '')
        subject = re.sub(r'\s*(?:财务分析|估值(?:分析)?|临床数据分析|竞争格局分析|销售峰值测算|催化剂判断|技术面分析|单公司投资研判|多公司投资研判)(?:报告)?\s*$', '', report_title(title))
        subject = re.sub(r'[<>:"/\\|?*]', '', subject).strip()
        if not subject:
            parser.error('报告首行标题须包含公司名称或临床对比主题。')
        report_date = args.report_date
        if report_date is None:
            metadata = next((value for kind, value, _ in report_blocks if kind == 'text'
                             and value.startswith(('报告日期', '报告日', 'Report date'))), '')
            match = re.search(r'(\d{4})\s*[-年]\s*(\d{1,2})\s*[-月]\s*(\d{1,2})', metadata)
            if not match:
                parser.error('请在 Markdown 中写明报告日期，或使用 --report-date 指定。')
            try:
                report_date = date(*map(int, match.groups()))
            except ValueError as exc:
                parser.error(str(exc))
        labels = {'financial': '财务分析', 'valuation': '估值分析', 'clinical': '临床数据分析', 'competition': '竞争格局分析', 'peak': '销售峰值测算', 'catalyst': '催化剂判断', 'technical': '技术面分析', 'integrated-single': '单公司投资研判', 'integrated-multi': '多公司投资研判'}
        if args.language == 'en':
            out = args.markdown.with_suffix('.docx')
        else:
            out = args.markdown.with_name(f'{subject}{labels[args.kind]}-{report_date:%Y%m%d}-DrugAlpha Skill生成.docx')
    if out.suffix.lower() != '.docx':
        parser.error('输出文件必须使用 .docx 后缀。')
    render(args.markdown, out, args.kind, args.language)

if __name__ == '__main__':
    main()

