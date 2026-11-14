"""Indicator-level prompt templates for AI-assisted DCF assumptions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndicatorPrompt:
    field: str
    title: str
    instruction: str
    output_hint: str


INDICATOR_PROMPTS: dict[str, IndicatorPrompt] = {
    "base_patient_count_10k": IndicatorPrompt(
        field="base_patient_count_10k",
        title="患者规模",
        instruction=(
            "判断该适应症在指定市场的患者规模，优先使用 DrugAlpha 疾病知识库给出的发病/患病人数；"
            "若缺失，必须从 context.publicSearchResults 中选择可核验的流行病学来源，"
            "给出治疗状态筛选前的宽口径基准患者池。"
            "不要在患者规模中预先乘入 Biomarker 阳性率、一线/二线/三线比例或既往治疗条件，"
            "这些条件由 Excel 中独立的 Biomarker比例和目标治疗人群比例处理。"
        ),
        output_hint=(
            "suggestedValue 使用万人口径数字，例如 120.0 表示 120 万人；"
            "basisComment 必须说明地区、年份、发病/患病口径，且未包含 Biomarker 和治疗状态筛选；"
            "如采用publicSearchResults中的资料，sourceUrls必须返回对应链接；"
            "确实没有可核验链接时可给低置信度初稿，但必须在basisComment说明并要求人工复核。"
        ),
    ),
    "biomarker_ratio": IndicatorPrompt(
        field="biomarker_ratio",
        title="Biomarker比例",
        instruction=(
            "仅处理 currentPatientFlow.biomarkerRatioRequiresReview=true 的适应症。"
            "疾病知识库未找到与当前 Biomarker 限定精确匹配的比例；"
            "必须从 context.publicSearchResults 中选择同时匹配疾病、市场和 Biomarker 定义（含阈值/阳性/高表达）的可核验公开来源。"
            "不得用总人群、其他癌种、不同阈值或不同检测定义的比例替代。证据无法还原分母或定义不一致时不要给值。"
        ),
        output_hint=(
            "suggestedValue 使用0-1比例；basisComment写明地区、疾病分母、Biomarker检测定义/阈值与年份；"
            "sourceUrls仅能从publicSearchResults.url选择且至少返回1条。没有精确证据时不要输出该任务。"
        ),
    ),
    "target_treatment_ratio": IndicatorPrompt(
        field="target_treatment_ratio",
        title="目标治疗人群比例",
        instruction=(
            "仅处理非肿瘤疾病的中重度、轻中度、重度、活动性等严重度限定。"
            "必须从 context.publicSearchResults 中选择与疾病、市场、严重度定义和分母一致的证据，"
            "估计该严重度人群占全部已诊断患者的比例；医院就诊队列不得直接冒充一般人群。"
        ),
        output_hint=(
            "suggestedValue 使用0-1比例；basisComment必须写明地区、分母、严重度量表/阈值和样本来源；"
            "sourceUrls仅能从publicSearchResults.url选择。证据不一致或无法还原分母时不要给值。"
        ),
    ),
    "population_filters": IndicatorPrompt(
        field="population_filters",
        title="可治疗/适用人群比例",
        instruction=(
            "拆解从总患者到可治疗患者的人群漏斗，包括诊断率、治疗线数、biomarker、疾病严重程度、"
            "医保/支付可及性和临床适用性。若 DrugAlpha 已有人群分层，应优先引用并说明。"
        ),
        output_hint="suggestedValue 使用 0-1 比例；basisComment 写明主要过滤项和乘积口径。",
    ),
    "patient_growth_rate": IndicatorPrompt(
        field="patient_growth_rate",
        title="患者增长率",
        instruction=(
            "判断该适应症患者规模在预测期内的年增长率。应结合疾病发病/患病趋势、诊断率提升、"
            "人口结构变化、筛查渗透和适应症扩展；若缺少明确依据，给出保守低个位数增长率并说明不确定性。"
        ),
        output_hint="suggestedValue 使用 0-1 年增长率，例如 0.005 表示 0.5%/年。",
    ),
    "penetration_curve": IndicatorPrompt(
        field="penetration_curve",
        title="渗透率曲线",
        instruction=(
            "先根据竞争格局、同类药上市放量、价格/医保准入和产品差异化判断成熟/峰值渗透率，"
            "再沿用当前市场的标准销售爬坡：国内10%/30%/55%/75%/90%/100%，"
            "海外8%/25%/50%/70%/85%/100%；默认峰值年为上市年+7年，上市前为0，"
            "峰值年及其后1年维持100%，再按每年3%自然下降；该下降不代表专利悬崖。"
            "研发阶段只影响上市概率，不用于调低商业化爬坡。若已有峰值销售锚点，"
            "需要检查患者数×价格×渗透率是否大致匹配。"
        ),
        output_hint="suggestedCurve 输出与 forecastYears 等长的 0-1 数组。",
    ),
    "monthly_price": IndicatorPrompt(
        field="monthly_price",
        title="治疗费用/价格",
        instruction=(
            "先读取currentPatientFlow中的drugCostCategory和priceBasis。"
            "priceBasis为per_month时估计月治疗费用，为per_treatment时估计单次/疗程费用。"
            "中国市场考虑医保谈判、国产同类药、完整疗程费用和支付能力；"
            "海外市场考虑美国/海外标价、折扣和公司分成口径。"
        ),
        output_hint=(
            "priceBasis=per_month时，中国市场suggestedValue用万元/月、海外用万美元/月；"
            "priceBasis=per_treatment时，中国市场用万元/次、海外用万美元/次。"
        ),
    ),
    "treatment_months": IndicatorPrompt(
        field="treatment_months",
        title="年用药时长",
        instruction=(
            "肿瘤药物优先查询同药同适应症同线次的mPFS，其次查询同靶点药物；必须保留公开来源。"
            "没有精确证据时不得臆测，交由DrugAlpha相邻线次或固定规则兜底。"
            "非肿瘤长期用药按年度用药月数，有限疗程按完整疗程月数，CAR-T/基因治疗计费系数固定为1。"
        ),
        output_hint=(
            "肿瘤suggestedValue使用mPFS月数且不设置12个月上限；"
            "非肿瘤有限疗程使用完整疗程月数；CAR-T/基因治疗固定为1。必须人工复核。"
        ),
    ),
    "peak_sales_anchor": IndicatorPrompt(
        field="peak_sales_anchor",
        title="峰值销售锚点",
        instruction=(
            "若 DrugAlpha 估值跟踪缺少峰值销售，请根据患者规模、价格、渗透率和竞争格局给出峰值销售初稿；"
            "若已有锚点，则做合理性校验并指出偏高/偏低风险。"
        ),
        output_hint="中国市场 suggestedValue 用亿元；海外市场用亿美元。",
    ),
    "market_scope": IndicatorPrompt(
        field="market_scope",
        title="市场纳入口径",
        instruction=(
            "判断该产品/适应症是否应纳入国内或海外测算。海外优先依据 DrugAlpha 海外估值表、"
            "sponsor、NCT/CTR 注册、3期及以上阶段、授权/权益信息；缺少证据时给出需人工复核说明。"
        ),
        output_hint="suggestedValue 使用 true/false；basisComment 写明纳入或排除原因。",
    ),
    "financial_assumptions": IndicatorPrompt(
        field="financial_assumptions",
        title="公司经营费用/现金流假设",
        instruction=(
            "结合历史财务数据和创新药商业化阶段，给出毛利率、销售费用率、研发费用率、管理费用率、"
            "税率、折旧摊销、资本开支、营运资本假设。优先用费用绝对值外推，再用费用率做合理性约束。"
        ),
        output_hint="suggestedValue 可为对象，包含 gross_margin、sales_expense_rate、rd_expense_rate 等字段。",
    ),
}


def get_prompt(field: str) -> IndicatorPrompt:
    return INDICATOR_PROMPTS.get(
        field,
        IndicatorPrompt(
            field=field,
            title=field,
            instruction="请基于 DrugAlpha 已有上下文和投研常识，为该 DCF 指标给出可编辑假设草稿。",
            output_hint="输出需符合统一 JSON schema。",
        ),
    )
