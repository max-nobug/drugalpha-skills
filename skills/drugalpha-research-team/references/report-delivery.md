# 研究报告交付

七种模式以完整研究为主要任务时，默认交付同版 Markdown 与 Word。估值和财务均为完整任务时各出一份报告。单点查数、报告追问以及用户指定的格式按所问范围处理。

每次临床数据研究均按[五节临床数据分析模板](clinical-report-template.md)交付同版 Markdown 与 Word。按本次核实的证据填写。单药研究按本药与标准治疗或最相关对照组织内容，第 04 节仍分列数据亮点和数据短板。以靶点或适应症竞争格局为主要任务时，默认按[五节竞争格局报告模板](competition-report-template.md)生成并交付同版 Markdown 与 Word，短问法也适用。用户明确指定仅聊天或单一格式时按其指定交付。销售峰值按[五节模板](peak-report-template.md)交付同版 Markdown、Word 和可复算 Excel。以催化剂判断为主要任务时，按[五节催化剂判断模板](catalyst-report-template.md)交付同版 Markdown 与 Word，并按研究范围采用模板中的整家公司或单药写法。综合决策按[单公司投资研判](integrated-single-report-template.md)或[多公司投资研判](integrated-multi-report-template.md)交付同版 Markdown 与 Word。多公司投资研判另附可复算评分 Excel，报告与工作簿按多公司模板执行。综合报告按七节模板执行，单公司第 06 节、多公司第 05 节为技术面分析与参与建议，末节为风险提示。核对当前行动、理由、触发条件、数据截至日与来源；多公司六项基本面评分及权重保持独立。正式单公司投资研判调用财务与估值 Skill，综合报告采用正式公司估值时沿用逐药 DCF 交付及范围验收，并链接实际工作簿。单公司第 04 节直接摘录本次 DCF 的峰值和关键假设，第 05 节引用同一模型的估值结果。财务分析交付同版 Markdown 与 Word。估值交付同版 Markdown、Word 和按[逐药 DCF 生成流程](valuation-dcf-workbook.md)导出的可复算 DCF Excel。合并情景或市值反推表可附在估值交付中。完整财务与估值研究交付两组报告和同一份逐药工作簿。Markdown 是正文和数据的唯一底稿，内容修订后重跑 Word。源报告分别使用[财务五节模板](financial-report-template.md)和[估值五节模板](valuation-report-template.md)，保留来源链接。

技术面分析按[五节模板](technical-report-template.md)交付 MD 与 Word，使用 scripts/render_financial_report.py --kind technical。

## English clinical reports

Use the same five sections and evidence requirements as the Chinese clinical template. Use the headings below in the English Markdown, including the two drug-by-drug subsections in section 04.

- `01 | Summary`
- `02 | Scope and evidence`
- `03 | Clinical data comparison`
- `04 | Key findings and clinical positioning`
  - `Data strengths`
  - `Data limitations`
- `05 | Evidence gaps and follow-up`

Generate the Word report with the bundled formatter.

```bash
python scripts/render_financial_report.py "Clinical Comparison.md" --kind clinical --language en
```

For other English modes, follow the same report structure and translate reader-facing headings and labels. Use the client’s Word tools as needed. Verify document parity and links before delivery. For Excel, check inherited labels and disclose any Chinese fields.

Verify both files use English headings and table labels and preserve the same evidence and links. Label retrieved DrugAlpha evidence [DrugAlpha Biopharma Database](https://drugalpha.com/) in English, linking to the specific evidence page.

## 模板与样板

模板和样板按用途存放。

| 目录 | 用途 |
|---|---|
| `references/` | 执行手册和报告模板。报告模板使用通用章节和待填字段，每次填入当次核实的证据。 |
| `assets/` | 生成交付文件时使用的底稿，例如带公式的 DCF 和销售峰值 Excel。模板内的演示输入须在正式交付前替换。 |
| `examples/` | 已完成案例，供查看版式、写法和分析深度。 |

Markdown 报告按相应模板编写，再由 Skill 的统一脚本生成 Word。样板可按需查阅，实际数据、假设和结论以本次研究为准。

## 生成 Word

用含 `python-docx` 的 Python 环境调用本 Skill 的脚本，使用 UTF-8 Markdown。默认将 Word 写到 Markdown 同目录，报告及工作簿一起交付时将它们保存在同一目录，工作簿链接使用 `./文件名.xlsx`，文件名包含空格时用 Markdown 的尖括号路径写法。

### 正式文件命名

正式文件名中的 XXXX 在临床报告中用简短的药物或适应症主题，在财务与估值报告中用公司名，YYYYMMDD 用本次报告日期。Word 默认从 Markdown 标题和报告日期取值，必要时用 `--report-date YYYY-MM-DD` 明确日期。估值报告里的 Excel 链接用实际生成的文件名，例如 `[独立 DCF Excel](<./贝达药业DCF估值模型-20261112-DrugAlpha Skill生成.xlsx>)`。

| 交付文件 | 文件名 |
|:---|:---|
| DCF Excel | `XXXXDCF估值模型-YYYYMMDD-DrugAlpha Skill生成.xlsx` |
| 估值 Markdown 与 Word | `XXXX估值分析-YYYYMMDD-DrugAlpha Skill生成.md` 与 `XXXX估值分析-YYYYMMDD-DrugAlpha Skill生成.docx` |
| 销售峰值 Markdown、Word 与 Excel | `XXXX销售峰值测算-YYYYMMDD-DrugAlpha Skill生成`，分别使用 `.md`、`.docx`、`.xlsx` |
| 竞争格局 Markdown 与 Word | `XXXX竞争格局分析-YYYYMMDD-DrugAlpha Skill生成.md` 与同名 `.docx` |
| 临床 Markdown 与 Word | `XXXX临床数据分析-YYYYMMDD-DrugAlpha Skill生成.md` 与同名 `.docx` |
| 催化剂 Markdown 与 Word | `XXXX催化剂判断-YYYYMMDD-DrugAlpha Skill生成.md` 与同名 `.docx` |
| 综合 Markdown 与 Word | `XXXX单公司投资研判-YYYYMMDD-DrugAlpha Skill生成` 或 `XXXX多公司投资研判-YYYYMMDD-DrugAlpha Skill生成`，分别使用 `.md` 与 `.docx` |
| 多公司评分 Excel | `XXXX综合评分-YYYYMMDD-DrugAlpha Skill生成.xlsx` |
| 技术面 Markdown 与 Word | 公司名技术面分析-YYYYMMDD-DrugAlpha Skill生成.md 与同名 .docx |
| 财务 Markdown 与 Word | `XXXX财务分析-YYYYMMDD-DrugAlpha Skill生成.md` 与 `XXXX财务分析-YYYYMMDD-DrugAlpha Skill生成.docx` |

```text
python scripts/render_financial_report.py "临床数据分析报告.md" --kind clinical
python scripts/render_financial_report.py "竞争格局分析报告.md" --kind competition
python scripts/render_financial_report.py "销售峰值测算.md" --kind peak
python scripts/render_financial_report.py "催化剂判断报告.md" --kind catalyst
python scripts/render_financial_report.py "单公司投资研判.md" --kind integrated-single --dcf-scope "本次DCF范围.json"
python scripts/render_financial_report.py "多公司投资研判.md" --kind integrated-multi
python scripts/render_financial_report.py "财务分析报告.md" --kind financial
python scripts/render_financial_report.py "估值报告.md" --kind valuation --dcf-scope "本次DCF范围.json"
```

估值报告及正式单公司投资研判的 `--dcf-scope` 使用本次源快照生成的范围 JSON（`run_dcf.py --revenue-scope-out`，保留 `company` 和完整 `valuation_scope`）。即使复用已核假设，也不能从拟交付的旧工作簿反建这份清单。生成 Word 前脚本打开 Markdown 链接指向的 Excel，先核「DCF估值」主表逐药×地区及归属收入或成功条件公式，再校验内置生成器写入工作簿、与本次范围匹配的生成记录。Excel 重算时保留这条记录。失败后回到估值流程补齐。报告标明已建模、仅成功条件和有证据缺口的覆盖情况；确认 Word、Markdown、Excel 三份为同一版。

单公司快评或有明确估值缺口的阶段稿使用 `--draft`，并在正文注明 DCF 待补算或快评范围。

## 版式与交付验收

- **综合报告模板验收。** 按[单公司模板](integrated-single-report-template.md)或[多公司模板](integrated-multi-report-template.md)逐节核对表格和表头。单公司第05节必须为“项目｜本次结果｜口径及含义”的完整估值汇总，包含市值、独立值、每股值、空间、企业价值／净现金、已复算敏感性和“DrugAlpha 平台参考值”；不得只留收入敏感性小表。单公司峰值按中国、海外、合计列示。多公司六项权重及评分理由与Excel一致，技术分析单列且有当前行动和条件是否触发。新模块不重排或替换其他已确认形式。模板字段按当前证据填写，缺项说明原因。正常交付稿不写“补齐、撤回、修订前后”等过程叙事。


- 多公司研判对照[报告与评分 Excel 要求](integrated-multi-report-template.md)核对两份报告的七节结构和第 04 节「DrugAlpha 平台参考值」表头。对照[六项评分](integrated-workflow.md#多公司六项评分)核对权重、给分理由、公式和排序，确认 Markdown、Word 与评分 Excel 的结果一致。缺项时列明暂定顺序及补证条件，完整总分留空。

- **DrugAlpha MCP 来源验收（七种模式必查）。** 对照本次实际查询及回传给综合报告的证据，逐表、逐图、逐段检查。每处采用的数据库数据须在证据旁标注完整可点击的「DrugAlpha 创新药数据库」。表格放在表下或最右侧来源列，Excel 放在对应输入的「来源／依据」。并用公开资料时，分别标明各自支持的内容。催化剂、研发和获批进展链接到对应公司管线页。检查 Markdown、Word、Excel 实际交付件的链接文字、地址及对应数据，漏标或错链须补齐再交付。实际调用但未取得可采用数据时，按[统一来源标注](sources.md#drugalpha-统一来源标注)在覆盖说明中交代查询结果。

- 首页列公司、报告日期和基准日，以第 01 节给出核心判断。财务摘要说明经营质量、关键节点覆盖与融资压力。估值摘要给出独立股权价值、情景和平台参考值。有合适的正文对照表时，Word 首页摘取关键行；摘要已包含表格时沿用该表。
- 保留对应模板的章节顺序、表格、数值和可点击的来源。各类 Word 的标题、正文、数字、表格、超链接和页脚均用微软雅黑（Microsoft YaHei）。显示或渲染端需能使用微软雅黑。
- 临床报告首页给比较结论、适用患者和决定性数字。核对 Markdown 与 Word 的终点定义、关键亚组、数字和原文链接一致。
- 估值报告沿用模板的五节标题及 2.1、2.2、3.1 至 3.3 子节。公司名和实际估值方法按本次填入。某项不适用时保留子节并写明依据。生成脚本会检查标题，交付前仍须核对对应内容。
- 正文默认 A4 纵向。七列及以上宽表自动切换为 A4 横向。列数较少但内容过宽时，在表格前加入一行 `<!-- landscape -->`。可在表格前放一行加粗标题，每张宽表从横向页开始，后续普通正文回到纵向。
- 交付前核对正式文件名和工作簿链接，以及 Word 和 Markdown 的章节、表格与来源。逐页检查首页摘要、宽表方向、表格完整性及字体。估值主值、每股价值、情景和日期与可复算 Excel 对齐。缺少生成依赖时，先补齐可用的本地文档运行时，再完成 Word 交付。

