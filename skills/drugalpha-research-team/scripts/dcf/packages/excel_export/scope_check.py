"""Check the deliverable against the independent, source-derived DCF scope."""

import hashlib
import json
import re
import unicodedata

from openpyxl import load_workbook


GENERATOR_PROPERTY = "DrugAlphaDCFGenerator"
REGIONS = {"china": "中国", "us": "海外"}
DEFAULT_SCOPE_GAP = "缺可量化的适应症、地区或权益输入，待核。"


def _key(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def generator_record(scope):
    pairs = sorted((_key(item["drug_name"]), item["market"]) for item in scope["valuation_scope"])
    data = json.dumps([_key(scope["company"]), pairs], ensure_ascii=False, separators=(",", ":"))
    return "drugalpha-independent-dcf-v1:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def validate_workbook_scope(workbook_path, scope, *, require_generator=False):
    expected = {}
    for item in scope.get("valuation_scope", []):
        if not item.get("drug_name") or item.get("market") not in REGIONS:
            raise ValueError("范围清单须逐项提供 drug_name 和 china/us 地区。")
        label = f"{item['drug_name']}（{REGIONS[item['market']]}）"
        key = _key(label)
        if key in expected:
            raise ValueError(f"范围清单存在重复的药物×地区：{label}")
        expected[key] = label
    if not expected or not scope.get("company"):
        raise ValueError("需要本次基于估值跟踪取得的完整入选清单，含 company 和 valuation_scope。")

    workbook = load_workbook(workbook_path, read_only=True)
    try:
        if "DCF估值" not in workbook.sheetnames:
            raise ValueError("Excel 缺少 DCF估值 主表。")
        rows = workbook["DCF估值"].iter_rows(values_only=True)
        title = next(rows, ())
        if _key(scope["company"]) not in _key(title[0] if title else None):
            raise ValueError("范围清单公司与 DCF 主表公司不一致。")
        next(rows, ())
        headers = next(rows, ())
        if "说明" not in headers:
            raise ValueError("DCF 主表缺少说明列。")
        note_col = headers.index("说明")
        conditional_col = next(
            (i for i, value in enumerate(headers) if value == "成功条件下单药DCF（亿元）"), None
        )
        year_cols = [i for i, value in enumerate(headers) if re.fullmatch(r"\d{4}E", str(value))]
        if not year_cols:
            raise ValueError("DCF 主表缺少年度收入列。")

        actual = {}
        duplicate = set()
        for row in rows:
            if row[0] == "里程碑收入":
                break
            key = _key(row[0])
            if key in expected:
                if key in actual:
                    duplicate.add(key)
                actual[key] = row

        errors = []
        for key, label in expected.items():
            if key in duplicate:
                errors.append(f"{label}重复")
                continue
            row = actual.get(key)
            if row is None:
                errors.append(f"{label}缺行")
                continue
            note = str(row[note_col] or "")
            annual = [row[i] for i in year_cols]
            conditional = row[conditional_col] if conditional_col is not None else None
            has_conditional = isinstance(conditional, str) and conditional.startswith("=")
            if DEFAULT_SCOPE_GAP in note:
                errors.append(f"{label}须先补证测算或补充具体缺口理由")
            elif "成功概率待定" in note or "风险调整后贡献待定" in note:
                if not has_conditional:
                    errors.append(f"{label}缺成功条件下的单药DCF公式")
                if "风险调整后贡献待定" in note and (
                    "成功条件" not in str(row[1] or "")
                    or any(not isinstance(value, str) or not value.startswith("=") for value in annual)
                ):
                    errors.append(f"{label}须标注成功条件，并逐年引用成功条件归属收入")
            elif all(value in (None, "") for value in annual):
                if "未量化范围" not in note:
                    errors.append(f"{label}年度收入留空，缺少未量化原因")
            elif any(not isinstance(value, str) or not value.startswith("=") for value in annual):
                errors.append(f"{label}年度归属收入须引用测算公式")
        if errors:
            raise ValueError("DCF 入选范围验收未通过：" + "；".join(errors))
        if require_generator:
            record = next((item.value for item in workbook.custom_doc_props if item.name == GENERATOR_PROPERTY), None)
            if record != generator_record(scope):
                raise ValueError("Excel 缺少与本次入选范围一致的内置 DCF 生成记录。请用 run_dcf.py --output-dir 生成。")
    finally:
        workbook.close()
    return len(expected)

