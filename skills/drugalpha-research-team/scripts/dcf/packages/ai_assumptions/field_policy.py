"""AI intervention policy for DCF model fields.

Fields are split into three modes:
- source_locked: factual/source fields, AI may not overwrite.
- ai_prefill: editable assumptions where AI can generate an initial value.
- ai_check_only: fields where AI should only sanity-check existing values.
"""

from __future__ import annotations

from dataclasses import dataclass


SOURCE_LOCKED = "source_locked"
AI_PREFILL = "ai_prefill"
AI_CHECK_ONLY = "ai_check_only"


@dataclass(frozen=True)
class FieldPolicy:
    field: str
    mode: str
    reason: str


FIELD_POLICIES: dict[str, FieldPolicy] = {
    "company_name": FieldPolicy("company_name", SOURCE_LOCKED, "公司名称来自 DrugAlpha 源数据。"),
    "drug_name": FieldPolicy("drug_name", SOURCE_LOCKED, "药物名称来自 DrugAlpha 管线数据。"),
    "target": FieldPolicy("target", SOURCE_LOCKED, "靶点来自 DrugAlpha 管线数据。"),
    "indication_text": FieldPolicy("indication_text", SOURCE_LOCKED, "适应症来自 DrugAlpha 管线数据。"),
    "phase": FieldPolicy("phase", SOURCE_LOCKED, "研发阶段来自 DrugAlpha 管线数据。"),
    "trial_reg_no": FieldPolicy("trial_reg_no", SOURCE_LOCKED, "登记号来自 DrugAlpha 管线数据。"),
    "sponsor": FieldPolicy("sponsor", SOURCE_LOCKED, "sponsor 来自 DrugAlpha 管线数据。"),
    "domestic_peak_sales_cny_100m": FieldPolicy("domestic_peak_sales_cny_100m", SOURCE_LOCKED, "国内峰值销售优先来自 DrugAlpha 估值跟踪。"),
    "overseas_peak_sales_usd_100m": FieldPolicy("overseas_peak_sales_usd_100m", SOURCE_LOCKED, "海外峰值销售优先来自 DrugAlpha 估值跟踪。"),
    "base_patient_count_10k": FieldPolicy("base_patient_count_10k", AI_PREFILL, "患者规模缺失或口径不清时适合 AI 生成初稿。"),
    "biomarker_ratio": FieldPolicy("biomarker_ratio", AI_PREFILL, "疾病知识库未能精确匹配 Biomarker 比例时，仅可由带公开来源链接的联网检索建议补充。"),
    "target_treatment_ratio": FieldPolicy("target_treatment_ratio", AI_PREFILL, "非肿瘤严重度比例优先查自免证据表；无精确口径时由联网检索结果生成可编辑初稿。"),
    "patient_growth_rate": FieldPolicy("patient_growth_rate", AI_PREFILL, "患者增长率通常来自流行病学趋势和诊断率变化，适合 AI 生成保守初稿。"),
    "population_filters": FieldPolicy("population_filters", AI_PREFILL, "可治疗人群比例通常需要疾病分层和投研判断。"),
    "penetration_curve": FieldPolicy("penetration_curve", AI_PREFILL, "渗透率爬坡高度依赖竞争格局和商业化节奏。"),
    "monthly_price": FieldPolicy("monthly_price", AI_PREFILL, "价格/年治疗费用适合由 AI 给同类药和支付口径初稿。"),
    "treatment_months": FieldPolicy("treatment_months", AI_PREFILL, "疗程/用药时长需要结合疾病属性和临床实践。"),
    "peak_sales_anchor": FieldPolicy("peak_sales_anchor", AI_PREFILL, "DrugAlpha 缺少峰值销售时可由 AI 给初稿；已有锚点则只做校验。"),
    "market_scope": FieldPolicy("market_scope", AI_CHECK_ONLY, "海外/国内纳入应由规则和源数据决定，AI 只解释与校验。"),
    "financial_assumptions": FieldPolicy("financial_assumptions", AI_CHECK_ONLY, "财务假设优先由历史数据和规则生成，AI 做合理性校验。"),
    "valuation_sanity_check": FieldPolicy("valuation_sanity_check", AI_CHECK_ONLY, "估值结果由公式计算，AI 只检查患者数、价格、渗透率和峰值销售是否矛盾。"),
}


def get_policy(field: str) -> FieldPolicy:
    return FIELD_POLICIES.get(
        field,
        FieldPolicy(field, AI_CHECK_ONLY, "未知字段默认不允许 AI 直接覆盖，仅做合理性校验。"),
    )


def ai_prefill_fields() -> list[str]:
    return [field for field, policy in FIELD_POLICIES.items() if policy.mode == AI_PREFILL]


def ai_check_only_fields() -> list[str]:
    return [field for field, policy in FIELD_POLICIES.items() if policy.mode == AI_CHECK_ONLY]
