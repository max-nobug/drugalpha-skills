"""Generate and validate DeepSeek adjustments to forecast financial assumptions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.ai_assumptions.deepseek_client import chat_completion, extract_text
from packages.source_adapters.io_utils import write_json


from packages.financial_data.review import (
    CURVE_RULES, build_financial_review_request, _validate_adjustments,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", required=True)
    parser.add_argument("--financial-assumptions", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
    assumptions = json.loads(Path(args.financial_assumptions).read_text(encoding="utf-8"))
    messages, curve_rules = build_financial_review_request(draft, assumptions)
    response = chat_completion(messages, temperature=0.1, max_tokens=2800)
    review = _parse_object(extract_text(response))
    if isinstance(review.get("financial_assumptions"), dict):
        review = review["financial_assumptions"]
    if not str(review.get("basisComment") or "").strip():
        retry_messages = messages + [{
            "role": "user",
            "content": (
                "必须返回非空的 basisComment，并结合上述历史费用率和预测曲线"
                "给出具体复核结论；不要只复述 requiredSchema。"
            ),
        }]
        retry_response = chat_completion(retry_messages, temperature=0.1, max_tokens=2800)
        retry_review = _parse_object(extract_text(retry_response))
        if isinstance(retry_review.get("financial_assumptions"), dict):
            retry_review = retry_review["financial_assumptions"]
        if str(retry_review.get("basisComment") or "").strip():
            response = retry_response
            review = retry_review
    review.setdefault("field", "financial_assumptions")
    review["suggestedValue"] = None
    review["needsHumanReview"] = True
    review["aiMode"] = "ai_prefill_validated"
    review["checks"] = _clean_checks(review.get("checks"))
    if not str(review.get("basisComment") or "").strip():
        review["basisComment"] = "DrugAlpha AI 未返回有效复核结论；当前财务曲线仍按历史财务规则生成，需人工复核。"
    adjustment = _validate_adjustments(
        suggestions=review.get("suggestions"),
        original_curves=assumptions.get("curves") or {},
        years=assumptions.get("years") or draft.get("forecast_years") or [],
        curve_rules=curve_rules,
    )
    review["suggestions"] = adjustment["normalizedSuggestions"]
    payload = {
        "companyName": draft.get("company_name"),
        "sourceSnapshotId": draft.get("source_snapshot_id"),
        "financialReview": review,
        "financialAdjustment": adjustment,
        "model": response.get("model"),
        "disclaimer": "DrugAlpha AI仅可调整未来经营曲线；历史财务和来源字段锁定；当前版本不纳入净现金/净债务调整。建议通过硬规则校验后才写入Excel。",
    }
    write_json(Path(args.out), payload)
    print(json.dumps({
        "ok": True,
        "outputPath": args.out,
        "model": response.get("model"),
        "appliedCount": adjustment["appliedCount"],
        "rejectedCount": adjustment["rejectedCount"],
    }, ensure_ascii=False))


def _parse_object(text: str) -> dict:
    cleaned = (text or "").strip()
    if "```" in cleaned:
        cleaned = cleaned.split("```", 2)[1]
        cleaned = cleaned.removeprefix("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        payload = {"basisComment": text.strip(), "confidence": "low", "checks": []}
    return payload if isinstance(payload, dict) else {"basisComment": text.strip(), "confidence": "low", "checks": []}


def _clean_checks(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    placeholders = {"string", "逐条写出需人工确认的具体事项；没有则返回空数组"}
    return [
        str(item).strip()
        for item in value
        if str(item).strip() and str(item).strip().lower() not in placeholders
    ]




if __name__ == "__main__":
    main()
