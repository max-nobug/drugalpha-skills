"""Read-only overview of this module's existing cash-flow discounting rules.

No source aggregation, model generation, defaults, calculation or persistence.
The complete patient-flow and sales-forecast algorithm is outside this overview.
"""
import json


def get_dcf_framework():
    return {
        "frameworkVersion": "dcf-cash-flow-overview-v1",
        "scope": "cash_flow_discounting_overview",
        "currency": "CNY",
        "moneyUnit": "CNY_100M",
        "formulas": [
            {"id": "free_cash_flow", "label": "原模块简化现金流", "expression": "FCF = 收入 × max(毛利率 - 销售费用率 - 研发费用率 - 管理费用率, 0) × (1 - 税率)"},
            {"id": "discount", "label": "预测期折现", "expression": "第 i 项年度现金流现值 = FCF[i] ÷ (1 + 折现率)^(i + 1)，i 从 0 开始；第一期为 1，不按年份数字相减计算"},
            {"id": "terminal", "label": "原模块终值", "expression": "当折现率 > 永续增长率且最后一期 FCF > 0 时，终值 = 最后一期 FCF × (1 + 永续增长率) ÷ (折现率 - 永续增长率)；否则原模块终值为 0"},
            {"id": "terminal_pv", "label": "终值折现", "expression": "终值现值 = 终值 ÷ 最后一期折现因子；没有预测期时原模块为 0"},
            {"id": "enterprise_value", "label": "企业价值", "expression": "企业价值 = 预测期现金流现值合计 + 终值现值，原模块金额输出保留 4 位小数"},
        ],
        "inputRequirements": [
            "明确且按时间顺序排列的年度现金流及对应年份；不得把销售峰值或 PS 估值总额当作现金流",
            "若需从收入计算现金流，还需明确年度收入、毛利率、销售/研发/管理费用率及税率",
            "明确折现率和永续增长率；不得把原模块默认数值当作用户已确认假设",
            "人民币亿元口径一致；国内/海外收入须先按原模块明确的权益、分成、成功概率与汇率口径处理，不能重复调整",
            "确认净现金/净债务、股数等数据是否具备；企业价值不能直接声称为每股目标价",
        ],
        "limitations": [
            "仅为现金流和折现公式概览，不包含完整患者流、适应症去重、上市年份、爬坡、峰值分配或校准算法",
            "不读取模型、用户历史或来源资料，不生成/保存/导出 DCF，不调用平台 AI",
            "原模块终值为 0 的边界是计算规则，不表示经济价值已确认；不满足条件时须说明限制并核对假设",
            "公式结果为企业价值；现有 PS/PE 估值与预测销售峰值并非完整 DCF 假设集",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(get_dcf_framework(), ensure_ascii=False))
