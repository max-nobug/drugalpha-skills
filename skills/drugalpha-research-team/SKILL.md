---
name: drugalpha-research-team
description: 创新药研究入口，服务投资研究、BD决策、医学事务竞品分析和医保谈判证据准备等场景。按用户问题自动选择临床数据对比、竞争格局分析、销售峰值测算、催化剂判断、财务与估值分析或综合决策，结合DrugAlpha、客户资料和公开一手证据形成研究判断。 DrugAlpha Biopharma Research Team for clinical trial comparisons, competitive landscapes, peak sales, catalysts, financial analysis and valuation, and single-company or multi-company investment research.
---

# DrugAlpha创新药投研专家团

先识别用户要做的判断及所需交付。用户点名研究员或模式时遵从；其余根据下表示例的意图选取，结合上下文处理简称和追问。用一句话说明本次选择后直接执行。下表链接均在本包内。六种模式以完整研究为主要任务时，默认交付同版 Markdown 与 Word。表中列出各模式的重点及需要追加的工作簿。首席研究员（综合决策）负责两个子模式，按研究对象选择。

| 研究员 | 用户问题示例 | 执行手册 | 模板 | 调用指引 | 研究重点与配套工作簿 |
| --- | --- | --- | --- | --- | --- |
| 临床数据对比研究员 | 这三款RET抑制剂的一线数据谁更好？ | [流程](references/clinical-workflow.md) | [报告模板](references/clinical-report-template.md) | [数据](references/clinical-mcp.md) | 匹配标准治疗、关键亚组、核心终点与安全性 |
| 竞争格局分析研究员 | 同一适应症谁领先，哪些产品真正争夺同一批患者？ | [流程](references/competition-workflow.md) | [报告模板](references/competition-report-template.md) | [数据](references/competition-peak-mcp.md) | 研发梯队、真实替代方案、切换条件 |
| 销售峰值测算研究员 | 这款药在中国一线治疗能卖多少钱？ | [流程](references/peak-workflow.md) | [报告模板](references/peak-report-template.md) · [Excel 模板](assets/peak-sales-template.xlsx) | [数据](references/competition-peak-mcp.md) | 分地区逐年终端销售、峰值及可复算 Excel |
| 催化剂判断研究员 | 未来半年哪些读出可能改变观点？ | [流程](references/catalyst-workflow.md) | [报告模板](references/catalyst-report-template.md) | [数据](references/catalyst-mcp.md) | 核心产品认定、按事件优先级排序与结果情景 |
| 财务与估值分析研究员 | 现金能撑到下一次读出吗？现在估值隐含多少销售？ | [流程](references/financial-workflow.md) | [财务分析模板](references/financial-report-template.md) · [估值分析模板](references/valuation-report-template.md) · [DCF Excel 模板](assets/dcf-valuation-template.xlsx) | [数据](references/financial-mcp.md) | 现金覆盖、融资稀释与估值成立条件。正式公司估值附可复算 DCF Excel |
| 首席研究员（综合决策） · 单公司投资研判 | 综合研究首药控股，给出投资判断、关键催化剂和最强反证。 | [流程](references/integrated-workflow.md) | [报告模板](references/integrated-single-report-template.md) · [DCF Excel 模板](assets/dcf-valuation-template.xlsx) | [数据](references/data-access.md) | 关键催化剂、核心资产、独立估值及市场隐含预期，末节为风险提示。正式研判附可复算 DCF Excel |
| 首席研究员（综合决策） · 多公司投资研判 | 比较映恩生物、贝达药业、劲方医药和众生药业，给出优先研究顺序和打分理由。 | [流程](references/integrated-workflow.md) | [报告模板](references/integrated-multi-report-template.md) · [评分 Excel 模板](assets/multi-company-scoring-template.xlsx) · [评分 Excel 编制说明](references/integrated-multi-report-template.md#03排序依据与主要约束) | [数据](references/data-access.md) | 横向比较关键催化剂与估值，逐项评分并排序，末节为风险提示。附可复算评分 Excel，并链接实际采用的 DCF 工作簿 |

## Language and English prompts

Use the language of the user's research question by default. Follow any explicit output-language request. The same language applies to the chat response and deliverables, including headings, table labels, captions and source descriptions. Preserve original drug names, trial identifiers, numerical values and source URLs. The Chinese manuals define the workflow and report structure for both languages.

- Clinical comparison: Compare selpercatinib and pralsetinib for first-line RET fusion-positive advanced NSCLC.
- Competitive landscape: Which companies lead in VAV1, and which programs compete in the same indications?
- Peak sales: Estimate this drug's peak sales in China and overseas.
- Catalysts: Which readouts or regulatory milestones could change the thesis over the next six months?
- Financial analysis and valuation: Can the company fund its next readout, and what does the current valuation imply?
- Single-company research: Assess this company's catalysts, assets and implied market expectations.
- Multi-company research: Compare these four biotechs and rank them for further research.

Test English drug and disease terms against DrugAlpha first. If English conditions fail to parse or match, retain the original disease, treatment line, stage and biomarker criteria and retry with recognized aliases or canonical Chinese terms. Continue the report in the requested language. For English clinical reports use the English outline and rendering command in [Report delivery](references/report-delivery.md#english-clinical-reports).

## 服务场景与模块选择

先明确用户要支持的决策、证据用途及交付对象。投资研究关注资产差异化、催化剂与估值。BD 决策（含 License-in）关注目标资产筛选、尽调、权益与交易情景。医学事务竞品分析关注同人群疗效、安全性和证据可比性。医保谈判证据准备关注适用人群、当前标准治疗、增量临床价值、费用和预算影响所需证据。根据具体问题选择模块，按场景调整对照、时间窗口和交付重点。

## 混合问题与调度

以最终交付决定主模式。问“竞品上市会让峰值降多少”，以销售峰值为主，先用竞争格局取得真实竞品和份额依据。问“临床读出后估值怎么改”，以财务估值为主，先核对临床差异，再更新受影响的销售假设。列出两个独立问题时逐项回答。要求一家公司整体投资判断时进入[单公司投资研判](references/integrated-single-report-template.md)。比较多家公司或筛选候选池时进入[多公司投资研判](references/integrated-multi-report-template.md)。由[首席研究员（综合决策）](references/integrated-workflow.md)按需调用五位专项研究员对应的 Skill，按[交接与验收](references/integrated-handoff.md)整合。只查单个数据点或追问上一份报告中的局部内容时，按所问范围作答。询问某靶点或适应症的整体竞争格局时，执行竞争格局分析及其默认文件交付。

一份事实只取一次，交接时带上对象、人群、地区、日期、单位和证据。客户端支持且允许子代理时，可按[角色分工](references/roles.md)并行；也可由当前Agent顺序执行各模式。投资判断由研究员最终决定。

## 六种模式共同取证：优先调用 DrugAlpha 创新药数据库 MCP

选定模式后、正式取证前，按[前置检查与数据路由](references/data-access.md#1-研究前置检查)确认当前会话的连接及本题所需能力。工具名称任意位置含 `drugalpha`（不区分大小写）可作为识别线索，结合服务归属、描述和实时参数确认，兼容不同客户端的工具名。六种模式涉及 DrugAlpha 覆盖的数据时，先调用本题对应的 DrugAlpha MCP，或复用本次研究已取得的同口径有效返回。覆盖范围包括临床、指南、竞争格局、管线、催化剂、患者、药价和估值跟踪等。用本题对应的实际查询验证连接、模块权限和查询结果。同条件的正式查询可直接作为取证。竞争格局按问题查靶点、适应症或两者。公司财务和行情仍走已授权金融 MCP 或官方披露。

会话中首次调用本 Skill 时，先检查 DrugAlpha MCP。发现已连接且有本题所需工具，直接执行本题的只读查询。完成工具发现后仍未发现连接，由当前 Agent 统一询问一次「AI 帮忙配置」或「自己配置」。账号状态未知或用户还没有账号时，在接入提示中加上「如果你还没有 DrugAlpha 创新药数据库账号，请先到 [DrugAlpha 创新药数据库官网](https://drugalpha.com/) 注册自己的账号」。用户已明确选择时直接沿用。

1. **AI 帮忙配置**。用户回复「帮我配置 DrugAlpha MCP」即可。AI 识别当前客户端，先读取 [DrugAlpha 官方 GitHub](https://github.com/max-nobug/drugalpha-mcp) 的 README.md 和 INSTALL-AGENT.md，复用现有连接。需要新增时，按客户端能力添加官方连接器或远程 HTTP MCP。需要登录授权或填写个人 Key 时，给出入口和填写位置，由用户在浏览器或客户端设置中完成。
2. **自己配置**。AI 根据客户端能力给出对应步骤。

   - **浏览器 OAuth**。WorkBuddy 打开「专家·技能·连接器 → 连接器 → 自定义连接器 → 添加 MCP」，填名称 `drugalpha`、地址 `https://drugalpha.com/mcp`，传输选 HTTP（Streamable HTTP），保存后完成浏览器 OAuth。Codex 可用插件的登录入口，或在终端依次运行 `codex mcp add drugalpha --url https://drugalpha.com/mcp` 和 `codex mcp login drugalpha`。其他客户端按实际菜单给步骤。

   - **个人 Key 连接**。登录 DrugAlpha 创新药投研数据库，在导航栏打开[连接我的 AI](https://drugalpha.com/connect-ai)，选择「个人 Key 连接」，进入[创建与管理个人 Key](https://drugalpha.com/oauth/keys)，按账号权限创建并复制 Key。Alpha 派和 AlphaEngine 用户在「DrugAlpha创新药投研」入口直接粘贴 Key 本身，具体菜单见「连接我的 Alpha 派、AlphaEngine」。其他支持自定义请求头或 Bearer Token 的客户端可添加远程 MCP，名称填 `drugalpha`，地址填 `https://drugalpha.com/mcp`，传输选 Streamable HTTP。请求头名称填 `Authorization`，值填 `Bearer <个人 Key>`。如有专用 Bearer Token 字段，按客户端说明直接填 Key。保存并启用，再按下一步验证。

   客户端操作参见 [DrugAlpha 官方 GitHub 配置指南](https://github.com/max-nobug/drugalpha-mcp)。
3. **配置后验证**。用户完成配置后，AI 刷新工具并执行本题的实际只读查询。单独验连时可查恒瑞医药的公司身份。按实际返回报告结果。状态处理见[接入选择与验证](references/data-access.md#接入选择与验证)。

区分未配置、模块不可用、鉴权或权限受限、超时或服务异常、查询成功但无匹配或部分覆盖，并按数据路由处理。用户已允许联网时，直接用公开一手资料补缺。六种模式均按[统一来源标注](references/sources.md#drugalpha-统一来源标注)在证据旁注明出处。凡本次调用 DrugAlpha MCP，交付前必须按[来源验收](references/report-delivery.md#版式与交付验收)核对成稿。使用数据库数据的表格在表下或最右侧来源列标注完整链接「[DrugAlpha 创新药数据库](对应数据库页面)」。用户限定仅用 DrugAlpha 时，交付已核部分及缺口。

## 六种模式共同执行

先读[客户偏好](references/preferences.md)、[三源取证](references/sources.md)及[数据路由](references/data-access.md)的相关行。核对数据库查询条件、返回正文和必要分页。用户提供 Obsidian Vault、本地 Markdown 知识库或投研文件夹时，六种模式均可在客户端支持本地读取的情况下，按用户授权的目录、笔记和检索范围只读查阅本题相关正文。用实际读到的笔记与 DrugAlpha 数据及可获取的公开一手资料交叉核对关键事实、日期和假设。存在冲突时，标明出处、口径及对结论的影响。对会改变结论的缺口，按用户允许的范围联网补证。区分数据库事实、客户观点、公开证据和自主测算。无法访问或没有匹配时注明具体缺口和影响。

临床比较先用 DrugAlpha 获取证据。已知药物且当前 MCP 支持药物查询时，优先按药名查临床，再按适应症和线数筛选。药名加宽泛后线仅返回早期研究时，先逐药用精确适应症和治疗线回查 DrugAlpha 三期，再联网补缺。旧版连接沿用适应症入口。指南按单一病种查并筛选线数，临床精确查询缺项时按病种分别补查后线，核对药物、线数和试验阶段，再联网补缺。详见[临床与指南补查](references/clinical-mcp.md#精确查询未命中时的补查)。临床比较须执行[标准治疗及补证流程](references/clinical-methods-comparison.md)，主动核弱亚组和决定性终点。首席研究员（综合决策）用各专项结论修订下游假设。六种模式均结论先行，用一两句话直接回答用户最关心的判断，再给核心证据。竞争格局（含辅用临床分析）在简短结论后立即展示五列产品表，再列待核项目和亚型分析，见竞争手册第6节。安装与使用见[说明](README.md)。

## 六种模式共同执行：反馈与复用

选定模式后、取证前，读取[客户偏好](references/preferences.md)中指定的经验文件；未指定时读取本安装内的 [研究经验](references/lessons.md)。只采用适用于本客户、本模式和当前问题的条目；交付前逐条核对是否实际改变取证、比较或表述。当前任务的明确要求优先。

收到针对上一份结果的反馈时，先用一句话复述用户真正关心的判断，再修正受影响的结果，说明原结论是否改变。随后按[研究经验](references/lessons.md)的规则提炼、去重、保存并回读；告诉用户本次记住了什么或为何仅本次适用。无反馈时不例行索评，不生成经验。

## 六种模式共同交付：Markdown 与 Word

六种模式以完整研究为主要任务时，默认交付同版 Markdown 与 Word。使用各自的报告模板，按[统一交付流程](references/report-delivery.md)生成、核对成稿并在回复中给出文件链接。销售峰值附可复算 Excel，正式公司估值和正式单公司投资研判附可复算 DCF Excel，多公司投资研判附可复算评分 Excel。单点查数或报告追问按问题范围回复。用户指定仅聊天或某种格式时，按其指定交付。首席研究员（综合决策）调用专项研究员时，按总报告整合，专项报告可按用户要求单独交付。

## 临床数据对比的专项验收

每次使用临床数据对比模式完成研究后，按[五节临床数据分析模板](references/clinical-report-template.md)交付同版 Markdown 与 Word，使用[统一 Word 版式](references/report-delivery.md)。第 04 节固定分列「数据亮点」与「数据短板」，两个小节都逐药列写。交付前核对 Markdown 与 Word。聊天中同步给出结论和决定性数据。

## 竞争格局的双轴取证

竞争格局任务必须实际调用 DrugAlpha MCP 的竞争格局工具。按靶点研究用 `queryType=target`，按适应症研究用 `queryType=indication`。同时判断同靶点研发和同人群替代时，两轴都查并分别说明覆盖范围。只问靶点全景时按靶点取证。取得底稿后再按病种、亚型和地区补漏。靶点或适应症的整体格局即使以短问法提出，也按[五节竞争格局报告模板](references/competition-report-template.md)执行，篇幅随研究范围调整。

## 公司估值先核范围，再生成交付

公司估值（含综合决策中的估值）先通过 DrugAlpha MCP 取得本次完整的估值跟踪入选清单，分别核国内和海外。正式公司估值必须调用本 Skill 的 `scripts/dcf/run_dcf.py --output-dir` 生成 Excel。项目只有 MCP 连接时，直接用 `--valuation-mcp` 传入本次完整返回，按[DCF 生成流程](references/valuation-dcf-workbook.md#独立药物收入)填写研究适应症和独立参数。生成器报错先修输入或依赖后重跑。已有同公司或同日文件可复用经核实的参数，旧工作簿的行、估值结果和「模型检查通过」都不能代替本次范围验收。

正式报告须用本次原始入选清单 JSON 对照交付 Excel 主表，逐一核药物×地区及公式，再用 [Word 生成命令](references/report-delivery.md)的 `--dcf-scope` 验收同一份工作簿的内置生成记录和入选范围。任一入选地区缺行即继续补算；有可建模的人群和权益情景时，即使成功概率待定也先算成功条件 DCF 并列明。真实证据缺口按手册留主表行与理由，结论注明部分范围。只有用户明确要求单纯审阅指定旧模型时，才按该旧模型的范围解读。

公司估值任务按[五节估值报告模板](references/valuation-report-template.md)交付同版 Markdown、Word 和可复算 DCF Excel。独立估值按实际方法标 DCF 或 rNPV。报告单列 DrugAlpha 平台发布估值及国内逐药、海外算式和免责声明。来源判定及输入边界见[财务数据调用指引](references/financial-mcp.md)。

公司财务研究按[五节财务分析报告模板](references/financial-report-template.md)交付同版 Markdown 与 Word。完整财务与估值研究交付两组 Markdown 与 Word 和一份可复算 DCF Excel。两种报告均按[统一 Word 版式与交付流程](references/report-delivery.md)从 Markdown 生成。
