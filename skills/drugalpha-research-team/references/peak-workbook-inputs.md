# 峰值工作簿输入说明

运行 `python scripts/build_peak_sales.py inputs.json output.xlsx`，依赖为 `openpyxl>=3.1,<4`，安装与检查见 [Skill 入口](../SKILL.md#生成文件前按任务检查-python-依赖)。生成后用 Excel 完整复算、逐项核实来源，再交付。所有可编辑参数集中在「测算假设」页。

- 顶层为 `product`（药名）、`first_year`（十年预测的首年）、`case`（默认中性）和 `regions`（地区列表）。地区名称须唯一且可用作 Excel 页名。
- 地区对象填 `name`、`currency`、`price`、`indications`。需要跨币种汇总时填 `fx`（人民币／一单位当地币）和 `fx_source`。各独立测算地区在地区页以本币列示。海外采用 DCF 口径时填 `europe_proxy: true`，录入名为「美国」的地区，输出为「海外」工作表和汇总行，销售等于美国当年终端销售 × 1.5，口径写在表下。欧洲无需单独录入。
- `price` 填 `package`（包装价）、`units`（包装单位数）、`daily`（日用单位数）、`note`、`source`、`dose_note`、`dose_source`。该入口适合固定日剂量、持续用药和地区内共同价格基准。不同适应症差异定价、固定疗程、负荷／维持或跨年暴露影响显著时，在对应药物页替换价格或收入行，保留公式并按真实收费方式补明细。
- 适应症对象填 `label`、`population`（首年人数）、`growth`、`biomarker`、`fit`（适应症基础人群占比）、`line`、`access`。后五项用小数。已有同口径目标线人群时，对已包含的筛选项填 1 并在说明中标明分母。按悲观、中性、乐观顺序填写三项数组 `class_penetration`、`class_share`、`months`、`launch`、`maturity_years`，最后一项是上市年到成熟年的间隔。已有可核实的直接产品渗透率依据时，填 `product_penetration`（三种情景的成熟产品渗透率数组），省略 `class_penetration` 和 `class_share`，在采用说明中标明分母及来源。输入 `prior`（零起始的前一疗线序号，可省略）会扣除该前线的上年同药暴露。确认同药再治疗不适用及转线假设后使用。
- 每项填对应的说明和来源：`population_note/source`、`biomarker_note/source`、`pool_note/source`、`duration_note/clinical_source`、`adoption_note/source`、`share_note/source`、`launch_note/pipeline_source`（斜线为两个独立字段）。来源写名称和真实 URL，DrugAlpha 使用完整「DrugAlpha 创新药数据库」标签。对仍缺直接观测的参数标注自主假设及依据。
- 地区页先核年度终端销售，再核预测期最高值和年份。成熟期按末年患者池与稳定渗透假设估算。多地区汇总按同一年换算后加总。地区峰值发生在不同年份时，合计峰值从同年销售序列重新计算。
