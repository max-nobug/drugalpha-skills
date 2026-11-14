# 竞争格局与销售峰值：DrugAlpha MCP调用指引

依据2026-10-16本会话实际工具说明。下列是调用模板，不代表已执行查询或已取得业务数据。每任务先核当前工具/schema/权限；可能显示 mcp__drugalpha__ 或 mcp__codex_apps__drugalpha_ 前缀，同名能力不要重复查。不内嵌凭据、历史实体标识或后台路径，不读取业务库绕过接口。

## 1. 按四条研究主线路由

| 研究需求 | 工具与调用顺序 | 关键边界 |
|---|---|---|
| 公司/药物身份和别名 | resolve_entities：query名称、types为company/drug数组 | 不解析疾病/靶点；歧义或截断需澄清，不猜候选 |
| 当前公司/药物管线与进度 | resolve_entities → get_pipeline | subject仅用本次实体type/key；管线行不是独立药物数，历史记录不自动代表当前在研 |
| 同靶点研发格局 | get_competitive_landscape：queryType=target，view=assets | query原样保留靶点限定；不用resolve_entities解析靶点；资产数量用assetCount，items非独立药物数 |
| 同适应症真实竞争 | get_competitive_landscape：queryType=indication；assets找资产，sources看原始条件及研究编号 | 保留原患者条件，assets与sources数量不能混用；只返回管线，不自动带临床/指南 |
| 临床差异/指南定位 | 优先复用临床交接；必要get_clinical_evidence/get_guidelines | 适应症query原文、view=sources；不自行从靶点或阶段判断临床最优，详见clinical-mcp.md |
| 疾病患者背景 | get_epidemiology目录 → 本次目录主题页 | 目录不等于正文；不把大疾病人数改称亚型患者，不自动乘比例或拼地区/年份 |
| 同类海外已实现销售 | get_overseas_drug_sales | 名称用q；多药/公司/地区筛选按本工具当前目录。年度数据不是季度；品牌合列不能拆 |
| 公司报告/事件原文 | get_company_research_inputs | query公司名直查；公告事件传用户主题并读正文；近期窗口不保证所有历史公告覆盖 |
| 药品适应症/用法原文 | get_drug_instructions：query候选 → sourceId正文 | 二选一；本次来源标识，不伪称完整官方认证，不把一品牌说明书用于全部厂家 |
| 价格 | get_drug_prices：query药名 | 最多20候选；按客户已授权偏好自动选可核规格，否则确认；不盲取首项；来源价非自付价或企业净价 |
| 药品费用公式背景 | get_treatment_cost_framework：空对象 | 只读框架，不接受患者输入、不替agent算钱，非完整可执行算法 |
| 公司资产数量辅助 | get_pipeline_portfolio：companies视图可query公司名 | 可选辅助，不能替逐条管线/位次核查；scopeSummary为所选市场汇总，不当单公司汇总 |

本次未见独立“国内单品销售”“医院准入”“医保限定支付”“企业净价”“市占率”工具，缺口按[联网指引](competition-peak-evidence.md)补；不能编工具名或把其他模块结果冒充这些数据。按[全能力路由](data-access.md)执行：竞争/领先性必须以适应症竞争入口取候选底稿，再核同靶点、具体适应症阶段及真正重叠人群；市场空间必须读流行病学正文并取相关同类实际销售或明确覆盖缺口，最后给可复算量化结果。仅当用户要求对照且已完成独立测算时，消费财务成员共享的授权公司估值；单成员任务可自行窄查，无需额外启动财务成员。个人模型优先、保留排除项和来源；估值/平台峰值是待检验假设，不是实际销售或总市场。不能因已有预测而省略独立患者与市场测算。

## 药物销售及 DCF 参数的第一数据源

独立峰值测算与 DCF 按药物、适应症、地区、疗线和标志物人群逐项先查 DrugAlpha MCP。复用本次任务已取得且口径匹配的记录。实际读取对应模块返回并纳入参数底稿，估值跟踪只用于确定纳入范围。

| 模型参数 | 首查 DrugAlpha MCP | 模型取值 |
|---|---|---|
| 患者规模、Biomarker 比例及筛选比例 | `get_epidemiology` 先取目录再读命中主题正文；必要时用 `get_guidelines`、`get_clinical_evidence` 核适用人群 | 核对地区、统计年、发病或患病、检测阈值及分母。试验筛选阳性率只能按其分母使用。确无标志物筛选要求时按 100% 计，注明标签或开发人群依据。 |
| 治疗费用 | `get_drug_prices`、`get_drug_instructions`、`get_treatment_cost_framework` | 优先本品；本品未上市或缺本地价格时先在平台查同市场、同场景可比药。按规格、剂量和频次计算足年费用，再除以 12 得月费，区分支付价与企业净收入。 |
| 肿瘤用药时长 | `get_clinical_evidence` | 主动查目标药物、同方案同线同亚型的 mPFS，同时检查实际用药时长或停药时间是否已报告。实际付费/用药时长有匹配证据时优先采用；持续治疗至进展且缺实际时长时，用匹配 mPFS 作疗程代理，标注截止日、成熟度及调整依据。mPFS 未达到时保留该状态。 |
| 已开展、申报或获批的适应症 | `resolve_entities` → `get_pipeline` | 逐适应症、地区、线数核已启动试验、申报、受理或获批状态。 |
| 预计开展或扩展的适应症 | `resolve_entities` → `get_catalysts` | 查公司、相关药物与权益合作方，核拟开展的研究及地区。单列计划及时间精度，已开展与计划阶段分别记录。 |
| 逐年产品渗透率 | `get_competitive_landscape` 先用适应症查询，必要时补同靶点；结合 `get_clinical_evidence`、`get_guidelines` | 核对同人群真实竞品、研发位次、标准治疗、切换理由与准入。渗透率是基于竞争证据的自主估计，列明可治疗患者分母、依据和区间。 |

只有该参数在 MCP 缺失、覆盖或权限受限、已过时，或队列、地区、单位等不匹配且回退后仍不可用时，才对该缺口定向联网检索。优先监管、公司及合作方、登记研究或论文原文；MCP 返回的原始出处用于核对其定义。已取得的合格数据继续用于模型。Excel 对应行的「来源／依据」列记实际采用值、地区/人群、数据期或截止日、单位/分母及引用；直接取数或推导时标明实际数据库与方式，例如「DrugAlpha 数据库 MCP（疾病知识库）」或「DrugAlpha 数据库 MCP（价格锚）＋模型换算」；公开补证写具体机构或原始披露名称，自主假设单独注明。凡用公开补证，写明该参数为何未能从 MCP 取得可用依据。

## 2. 统一返回核查

1. 先判调用错误、权限、空结果及未覆盖，失败不填0。鉴权按当前连接提示处理，不索取令牌/账号密码。
2. 按适应症/靶点查询核data.query.ready、unresolved、targetScope/条件/标志物配对和匹配解释。ready=false时澄清，不删条件放宽；存在可选条件/放宽匹配时明确哪些人群限制未严格覆盖，并回原文核适用性。
3. 保留每记录来源、阶段/数据期、单位及缺失；平台发布/抓取/查询时间不等于进展发生日、统计年或价格日期。没有链接只说来源未提供链接，不能给它猜一个地址；另行联网核验作为独立来源。
4. 有pagination.hasMore用本次nextCursor继续；query、筛选、view及limit不变。覆盖限制仍保留；complete_within_scope不是全市场完整。工具返回截断时缩小有明确目的的范围，不短时批量遍历全库。
5. 保存本次原返回及研究提取表，公开只展示名称、事实、来源和必要限制，不展示内部ID/游标/凭据。联网新增不能并入平台原计数或假装MCP返回。

## 3. 公司/药物管线：先身份再进度

示例为参数形式，不代表真实已查；研究时替换为真实对象。

```json
{"query":"恒瑞医药","types":["company"],"limit":10}
```

确认本次唯一候选后：

```json
{"subject":{"type":"company","key":"<本次确认entity.key>"},"page":{"limit":20}}
```

药物同理，解析types=["drug"]、subject.type=drug。subject不是字符串、完整entity或含label的对象。默认先不筛阶段/适应症；确需phaseKeywords/indicationKeywords时每组最多5个来源原文关键词，同组并集、跨组交集，无同义词扩展，缺字段会被排除。分页继续保留筛选。

核phase、indication、trialRegNo、progressUpdates与每条evidence。进展列标签可能仅季度/报告期，不合成具体事件日期。正式位次需回注册和监管核，不把快照最新当资产进度领先。

## 4. 双轴竞争格局

竞争格局任务必须实际调用 `get_competitive_landscape`。靶点全景走 `target/assets`，同适应症竞争走 `indication/assets`。涉及两类判断时分别调用。需要核对适应症原文、匹配说明和研究编号时，再查同一适应症的 `sources`。连接无法使用或只返回部分覆盖时，在交付中标明实际查询状态及缺口。

同靶点示例：

```json
{"query":"KRAS G12D","queryType":"target","view":"assets","page":{"limit":20}}
```

同患者竞争示例：

```json
{"query":"胰腺癌后线","queryType":"indication","view":"assets","page":{"limit":20}}
```

需要来源及登记号时，将同一query的view改sources，作为另一视图独立分页。靶点模式只支持assets，不能以sources冒取来源；适应症默认assets。结果分别用于“同靶点研发队列”与“同患者替代格局”，不从一个靶点全景直接给同线数临床竞争排名。

平台原计数只指其发布资产范围；本次任务需要联网补充缺失候选时另建来源表，标明新增。仅要求单模块查询时不顺带查临床/指南或扩大范围。

## 5. 患者背景：先目录后正文

```json
{"page":{"limit":20}}
```

读完get_epidemiology目录，按本次页面标题/概述确认主题后：

```json
{"areaKey":"<本次目录areaKey>","groupKey":"<本次目录groupKey>"}
```

不能复用其他模块目录或猜标识；有多个可能主题需澄清。保留原始年份、地区、新发/患病人数或率、单位、预测标记及逐指标来源。页面名NSCLC不表示其中肺癌总体数字已成为NSCLC人数，更不代表G12D后线可服务人数；另行细分测算按执行手册步骤四取证。

## 6. 同类海外销售：先按名称查，必要才读目录

名称可直接走q，不必为一个产品遍历全部目录：

```json
{"view":"sales","q":"Keytruda","fromYear":2024,"toYear":2025,"page":{"limit":20}}
```

需要多药、公司或地区精确过滤时才读当前销售目录：

```json
{"view":"catalog","catalogKind":"drugs","q":"<待核药名或商品名>","page":{"limit":20}}
```

```json
{"view":"catalog","catalogKind":"regions","page":{"limit":20}}
```

再用该销售模块本次返回的drugs/companies/regions/targets候选标识；不能用resolve_entities的key替代目录drug/company标识。销售参数支持drug或drugs（最多10个）、company、target、region、fromYear/toYear，具体取当前schema。默认整体region=category:OVERALL；地区过滤用目录code，不把不同公司的地区定义当可加总。

每页最多50个完整历史序列，不按年份拆序列。按返回pagination读完；sourceTruncated=true仍需缩小范围，不能说全量。fromYear/toYear按财年期末年份，仍保留实际起止日期。金额依unit/currency，不把API百万美元与网页亿美元直接拼表。保留原币/汇率换算、品牌合计/财报合列、空值；不能反推单适应症销售、厂商净收入或未披露地区。

海外年度工具不能替代最新季度增长、国内销售或终端处方。销售模块目前没有独立海外销售Excel下载工具；确需自建工作簿由agent依据已取数据生成并标明，不能声称平台导出。

## 7. 公司原文与公告

公司/公告/财报点评优先get_company_research_inputs。支持query公司名直查，歧义候选先确认；query/companyId/旧companyKey三选一，标识必须来自本次合法返回。

```json
{"query":"<研究公司>","sections":["announcement"],"eventSummary":"<用户事件主题或公告标题>","includeAnnouncementText":true}
```

仅要一致预期才只选financial_consensus；预测不是实际销售。核公告主体、标题、日期、body、textStatus及truncated，扫描失败/截断不称完整阅读。近期公告窗口未覆盖时，按[公开路径](competition-peak-evidence.md)找对应历史原文；用户只说“最新公告”但主题不明时先澄清，不能拿默认财报替代。

该入口不提供全部产品销售、长期公告档案或分析结论；正文才是研究资料。需要费用/管线等按实际开放工具补，不调用平台AI，也不在成稿写内部调用流程。

## 8. 说明书、价格和费用（影响市场时才深入）

get_drug_instructions先取候选：

```json
{"query":"<确认的药物名>"}
```

再使用本次确认来源：

```json
{"sourceId":"<本次返回的来源id>"}
```

query/sourceId不可同时传；字段缺失不补。公开药学来源不自动等于官方完整标签，关键注册限定另核NMPA/FDA/EMA。

get_drug_prices：

```json
{"query":"<药物名及已知规格>"}
```

最多20候选，优先浙江医保支付标准、无结果时备用招投标。客户已授权自动选择规格时，核药名、剂型、剂量、包装及价格口径后自动选一种并说明依据，无需逐次确认，不盲取第一项；无此授权时确认规格或给条件情景。保留厂家、剂型、规格、包装价、最小单位价、转换比、地区及priceDate；searchedAt不是价格日期。空价/日期不补零或今日。

get_treatment_cost_framework使用空对象{}，只取得原公式概览、年度化常量和频率；agent依据已核用法及确认价格做独立、可复算测算。它不是患者计算接口，不能传体重/方案让它自动算；复杂分期/日期边界不声称完全等价于平台计算器。缺剂量、频率、疗程、包装或价格先说明缺口；不把药费当自付/净收入。

## 9. 错误与交付

身份歧义先确认，不猜；临床空结果按[峰值手册的检索回退规则](peak-workflow.md)做同义词归一和扩大召回，最多两轮；原问题的患者条件始终保留并逐条核入组，不能将扩大结果冒充精确命中。来源/权限/截断缺口限制结论。关键公开缺口按同一缺口两轮无新增停止规则处理，不无限重试或读后台补。最终给商业判断、实际来源与覆盖说明，不给内部实体标识、工具日志、临时授权下载引用或凭据。
