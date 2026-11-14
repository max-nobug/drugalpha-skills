"""Original DCF financial review request and validation, without model calls."""
from __future__ import annotations
from copy import deepcopy
import json
import math

CURVE_RULES = {
    "gross_margin": {"min": 0.30, "max": 0.98, "max_step": 0.03, "max_delta": 0.05},
    "sales_expense_rate": {"min": 0.02, "max": 0.45, "max_step": 0.05, "max_delta": 0.15},
    "rd_expense_rate": {"min": 0.02, "max": 0.55, "max_step": 0.05, "max_delta": 0.10},
    "admin_expense_rate": {"min": 0.02, "max": 0.18, "max_step": 0.03, "max_delta": 0.05},
    "tax_rate": {"min": 0.00, "max": 0.25, "max_step": 0.05, "max_delta": 0.05},
    "da_rate": {"min": 0.00, "max": 0.08, "max_step": 0.03, "max_delta": 0.04},
    "capex_rate": {"min": 0.00, "max": 0.12, "max_step": 0.04, "max_delta": 0.06},
    "nwc_rate": {"min": -0.08, "max": 0.08, "max_step": 0.04, "max_delta": 0.04},
}


def build_financial_review_request(draft: dict, assumptions: dict) -> tuple[list[dict], dict]:
    latest = assumptions.get("latestHistorical") or {}
    curve_rules = deepcopy(CURVE_RULES)
    if assumptions.get("operatingExpenseMode") == "combined_sales_admin":
        curve_rules["sales_expense_rate"] = {"min": 0.02, "max": 0.63, "max_step": 0.05, "max_delta": 0.15}
        curve_rules.pop("admin_expense_rate", None)
    messages = [
        {
            "role": "system",
            "content": (
                "你是创新药DCF财务假设助手。历史财务和来源字段绝对不可修改。"
                "你可以对未来经营比率曲线提出调整，但只能调整给定字段，必须使用0到1的小数，"
                "保持逐年平滑并锚定历史与规则曲线。只对有充分依据的曲线给出medium/high置信度建议；"
                "不确定时给low，系统不会应用。只输出JSON对象。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({
                "company": draft.get("company_name"),
                "forecastYears": draft.get("forecast_years"),
                "historySource": (assumptions.get("sourceStatus") or {}).get("historyProvider"),
                "operatingExpenseMode": assumptions.get("operatingExpenseMode"),
                "expensePresentationNote": "combined_sales_admin 时 sales_expense_rate 代表销售及管理费用合计率，admin_expense_rate 锁定为0且不可调整。",
                "latestHistorical": latest,
                "curves": assumptions.get("curves"),
                "netCashAdjustment": (
                    "净现金桥由已核实的基准日数据确定，经营曲线校验不调整该桥。"
                    if assumptions.get("equityBridge") else "未提供净现金桥。"
                ),
                "methodNotes": assumptions.get("methodNotes"),
                "editableForecastFields": list(curve_rules),
                "validationBounds": curve_rules,
                "requiredSchema": {
                    "field": "financial_assumptions",
                    "suggestedValue": None,
                    "basisComment": "中文复核结论，包含主要异常、局限和待确认项",
                    "confidence": "low|medium|high",
                    "needsHumanReview": True,
                    "aiMode": "ai_prefill_validated",
                    "checks": ["逐条写出需人工确认的具体事项；没有则返回空数组"],
                    "suggestions": [{
                        "field": "必须是 editableForecastFields 之一",
                        "suggestedCurve": ["与 forecastYears 等长的0到1小数"],
                        "confidence": "low|medium|high",
                        "reason": "说明为什么调整以及与历史/规则曲线的关系",
                    }],
                },
            }, ensure_ascii=False),
        },
    ]
    return messages, curve_rules


def apply_external_financial_review(draft: dict, assumptions: dict, review: dict) -> dict:
    """Validate user-agent review using the original financial curve rules.

    Only suggestions are interpreted; history, company and source ownership
    remain server inputs. No provider or model response is trusted here.
    """
    if not isinstance(review, dict) or not str(review.get("basisComment") or "").strip():
        raise ValueError("Financial review conclusion required")
    _, rules = build_financial_review_request(draft, assumptions)
    result = deepcopy(review)
    result.update(field="financial_assumptions", suggestedValue=None,
                  needsHumanReview=True, aiMode="ai_prefill_validated")
    result["checks"] = [str(v).strip() for v in result.get("checks", []) if str(v).strip()] if isinstance(result.get("checks"), list) else []
    adjustment = _validate_adjustments(
        suggestions=result.get("suggestions"), original_curves=assumptions.get("curves") or {},
        years=assumptions.get("years") or draft.get("forecast_years") or [], curve_rules=rules,
    )
    result["suggestions"] = adjustment["normalizedSuggestions"]
    return {"companyName": draft.get("company_name"), "sourceSnapshotId": draft.get("source_snapshot_id"),
            "financialReview": result, "financialAdjustment": adjustment,
            "reviewProvider": "user_agent", "platformAiUsed": False}


def _validate_adjustments(
    suggestions: object,
    original_curves: dict[str, object],
    years: list[object],
    curve_rules: dict[str, dict[str, float]] | None = None,
) -> dict:
    curve_rules = curve_rules or CURVE_RULES
    normalized = []
    applied_curves = deepcopy(original_curves)
    decisions = []
    expected_length = len(years)
    for item in suggestions if isinstance(suggestions, list) else []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        confidence = str(item.get("confidence") or "low").strip().lower()
        reason = str(item.get("reason") or "").strip()
        curve = _numeric_curve(item.get("suggestedCurve"))
        normalized_item = {
            "field": field,
            "suggestedCurve": curve,
            "confidence": confidence,
            "reason": reason,
        }
        normalized.append(normalized_item)
        errors = _curve_errors(field, curve, confidence, original_curves, expected_length, curve_rules)
        original = _numeric_curve(original_curves.get(field))
        status = "rejected" if errors else "applied"
        if not errors:
            applied_curves[field] = [round(value, 4) for value in curve]
        decisions.append({
            **normalized_item,
            "status": status,
            "validationErrors": errors,
            "originalCurve": original,
            "appliedCurve": applied_curves.get(field) if field in curve_rules else original,
        })
    applied_count = sum(1 for item in decisions if item["status"] == "applied")
    return {
        "mode": "ai_prefill_validated",
        "historicalAndBalanceSheetLocked": True,
        "eligible": applied_count > 0,
        "appliedCount": applied_count,
        "rejectedCount": sum(1 for item in decisions if item["status"] == "rejected"),
        "originalCurves": deepcopy(original_curves),
        "appliedCurves": applied_curves,
        "decisions": decisions,
        "normalizedSuggestions": normalized,
    }


def _curve_errors(
    field: str,
    curve: list[float],
    confidence: str,
    original_curves: dict[str, object],
    expected_length: int,
    curve_rules: dict[str, dict[str, float]],
) -> list[str]:
    if field not in curve_rules:
        return ["字段不在允许调整范围"]
    errors = []
    if confidence not in {"medium", "high"}:
        errors.append("字段置信度低于medium")
    if len(curve) != expected_length or not curve:
        errors.append(f"曲线长度应为{expected_length}")
        return errors
    rule = curve_rules[field]
    if any(not math.isfinite(value) or value < rule["min"] or value > rule["max"] for value in curve):
        errors.append(f"超出允许区间{rule['min']:.0%}-{rule['max']:.0%}")
    if any(abs(curve[idx] - curve[idx - 1]) > rule["max_step"] + 1e-9 for idx in range(1, len(curve))):
        errors.append(f"年度变动超过{rule['max_step']:.0%}")
    original = _numeric_curve(original_curves.get(field))
    if len(original) == len(curve) and any(abs(curve[idx] - original[idx]) > rule["max_delta"] + 1e-9 for idx in range(len(curve))):
        errors.append(f"偏离规则曲线超过{rule['max_delta']:.0%}")
    return errors


def _numeric_curve(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            return []
    return result
