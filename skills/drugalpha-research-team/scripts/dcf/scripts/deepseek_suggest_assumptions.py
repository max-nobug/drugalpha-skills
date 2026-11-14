"""Ask DeepSeek to propose explanations for missing editable DCF assumptions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.ai_assumptions.deepseek_client import chat_completion, extract_text
from packages.source_adapters.io_utils import write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", required=True, help="Path to DCF draft model JSON.")
    parser.add_argument("--out", help="Output JSON path under DCF project.")
    parser.add_argument("--max-gaps", type=int, default=12, help="Maximum assumption gaps to send.")
    args = parser.parse_args()

    draft_path = Path(args.draft)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    gaps = _collect_gaps(draft, max_gaps=args.max_gaps)
    messages = _build_messages(draft, gaps)
    response = chat_completion(messages, temperature=0.1, max_tokens=6000)
    text = extract_text(response)

    output = {
        "sourceDraft": str(draft_path),
        "companyName": draft.get("company_name"),
        "model": response.get("model"),
        "gapCountSent": len(gaps),
        "gaps": gaps,
        "proposalText": text,
        "usage": response.get("usage"),
        "disclaimer": "AI 建议仅用于生成可编辑假设草稿，不构成投资建议；请研究员复核数据来源、口径和适用性。",
    }
    output_path = Path(args.out) if args.out else Path("data") / "ai_assumptions" / f"{draft_path.stem}_deepseek.json"
    write_json(output_path, output)
    print(json.dumps({"outputPath": str(output_path), "gapCountSent": len(gaps), "model": response.get("model")}, ensure_ascii=False, indent=2))
    return 0


def _collect_gaps(draft: dict[str, Any], *, max_gaps: int) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for product in draft.get("products") or []:
        for indication in product.get("indications") or []:
            for market in indication.get("markets") or []:
                flow = market.get("patient_flow") or {}
                missing: list[str] = []
                if flow.get("base_patient_count_10k") is None:
                    missing.append("base_patient_count_10k")
                if not flow.get("population_filters"):
                    missing.append("population_filters")
                if all(value is None for value in flow.get("penetration_curve") or []):
                    missing.append("penetration_curve")
                if market.get("peak_sales_anchor") is None:
                    missing.append("peak_sales_anchor")
                if not missing:
                    continue
                gaps.append(
                    {
                        "drugName": product.get("drug_name"),
                        "target": product.get("target"),
                        "indication": indication.get("indication_text"),
                        "phase": indication.get("phase"),
                        "market": market.get("market"),
                        "trialRegNo": indication.get("trial_reg_no"),
                        "sponsor": indication.get("sponsor"),
                        "overseasEligibilityReason": indication.get("overseas_eligibility_reason"),
                        "missingFields": missing,
                    }
                )
                if len(gaps) >= max_gaps:
                    return gaps
    return gaps


def _build_messages(draft: dict[str, Any], gaps: list[dict[str, Any]]) -> list[dict[str, str]]:
    system = (
        "你是创新药投研建模助手。你的任务是为 DCF 患者流模型的缺失字段提出可编辑假设草稿，"
        "并清楚说明依据、口径和不确定性。不要声称自己已访问实时数据库；如果需要外部验证，请明确写为待核实。"
        "不要输出推理过程，只输出可解析的紧凑 JSON。"
    )
    user = {
        "company": draft.get("company_name"),
        "forecastYears": draft.get("forecast_years"),
        "task": (
            "请针对下列缺失项输出 JSON 数组。每个对象包含 drugName、indication、market、field、"
            "suggestedValue 或 suggestedCurve、basisComment、confidence(low/medium/high)、needsHumanReview。"
            "basisComment 后续会写入 Excel 注释框，请用中文、简洁、可审计。"
        ),
        "gaps": gaps,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


if __name__ == "__main__":
    raise SystemExit(main())
