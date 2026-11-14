# DrugAlpha创新药投研专家团 · 安装与使用

[English](README.en.md) · 简体中文

正式版 v0.2.63。用户先读本页，Agent 从 [SKILL.md](SKILL.md) 开始执行。

## 专家团能做什么

DrugAlpha 创新药投研专家团面向创新药投资研究，也支持 BD 尽调、医学事务竞品分析和医保谈判证据准备。专家团优先通过 DrugAlpha MCP 获取临床、管线、竞争格局和催化剂数据，并结合你授权的 Obsidian 等本地资料和公开一手资料，判断药物的差异化、可能改变投资判断的事件，以及当前估值隐含的预期。

你可以单独调用任一位专项研究员，也可以请首席研究员（综合决策）协调五位研究员，完成单公司或多公司投资研判。

| 专家分工 | 负责什么 |
|---|---|
| **临床数据对比研究员** | 对齐适应症、治疗线数和患者亚组，与现行标准治疗比较疗效和安全性。分别说明 PFS 和 OS，指出各药的数据亮点与短板。 |
| **竞争格局分析研究员** | 按靶点和适应症梳理竞品与研发位次，判断谁领先、各自和谁竞争，以及哪些产品争夺同一批患者。 |
| **销售峰值测算研究员** | 分中国和海外测算患者数、渗透率、份额、价格和疗程，给出逐年销售和峰值，并提供可复算 Excel。 |
| **催化剂判断研究员** | 跟踪核心产品的海外授权、申报与获批、临床读出、销售放量和分成兑现，按重要性排序，说明什么结果会改变投资判断。 |
| **财务与估值分析研究员** | 判断现金能否撑到关键节点，评估融资与稀释压力，独立测算估值，并列出 DrugAlpha 平台参考值与当前市值隐含的预期。 |
| **首席研究员（综合决策）** | 拆解问题，协调专项研究，核对关键证据并处理分歧，把临床、竞争和催化剂的发现落实到销售与估值假设。**单公司投资研判**给出核心判断、关键催化剂和最强反证。**多公司投资研判**逐项评分，给出优先深研顺序和理由。 |

完整研究默认交付同版 Markdown 和 Word。销售峰值、正式 DCF 估值和多公司评分附可复算 Excel。报告默认使用提问语言，也可指定中文或英文。

## 1. 安装 Skill

解压单项 ZIP 后找到名为 **drugalpha-research-team** 的文件夹，其根部应直接包含 SKILL.md。保留整个文件夹及内部结构。本包内置六种模式，安装一次即可。

| 你的 Agent 支持什么 | 怎样使用 |
|---|---|
| 有“技能 / Skills”的本地导入功能 | 按该界面要求选择单项 ZIP 或解压后的技能文件夹。导入后在技能列表查看本技能。 |
| 从目录发现技能 | 查阅该 Agent 的设置或文档，找到用户级或项目级技能目录。通用布局是“该 Agent 的技能目录 / drugalpha-research-team / SKILL.md”。用户级供多个项目复用，项目级仅供相应项目使用。 |
| 可以读取文件，但没有技能管理界面 | 把完整文件夹放入允许访问的工作区，在当前任务中指定文件夹并要求 Agent 读取 SKILL.md 及其引用手册。该方式用于当次执行。 |

技能目录因客户端而异。例如 Codex 项目级目录为 `.agents/skills/`，个人目录为 `~/.agents/skills/`。其他 Agent 请使用其实际支持的导入入口或目录。安装后新建会话，输入“请读取DrugAlpha创新药投研专家团的技能入口，说明本次任务采用哪个执行流程”。[Codex 安装参考](https://learn.chatgpt.com/docs/build-skills)

财务研究优先复用客户已连接且有权限的iFinD、东方财富妙想（Choice）或其他金融MCP；没有适用连接时自动查官方财报，不强制购买或安装。金融数据授权由客户自行提供，分发包不包含账号或密钥。DrugAlpha负责药物数据及估值范围，不负责公司财务和行情取数。

### 复制给 AI 安装

把下面的提示词发给你的 AI。也可以[下载 Skill ZIP](https://github.com/max-nobug/drugalpha-skills/releases/download/v0.2.63/drugalpha-research-team-v0.2.63-public.zip)，按客户端说明安装。

~~~text
请从以下 GitHub 目录安装 DrugAlpha 创新药投研专家团 Skill。
https://github.com/max-nobug/drugalpha-skills/tree/main/skills/drugalpha-research-team

请先阅读 Skill 目录里的 README.md，按当前客户端支持的方式安装整个目录。如果需要我手动导入 ZIP，请给出对应客户端的操作步骤。
手动安装用的 Skill ZIP
https://github.com/max-nobug/drugalpha-skills/releases/download/v0.2.63/drugalpha-research-team-v0.2.63-public.zip

安装后确认能调用这个 Skill，并用一次实际只读查询验证 DrugAlpha MCP 连接。需要新接入时，按下面的官方说明协助配置，并告诉我在哪里完成授权。
DrugAlpha MCP 官方连接说明
https://github.com/max-nobug/drugalpha-mcp
~~~

## 2. 连接 DrugAlpha 创新药数据库 MCP

还没有账号？请先到 [DrugAlpha 创新药数据库官网](https://drugalpha.com/) 注册自己的账号。首次调用专家团 Skill 时，AI 会先检查连接。已有连接可直接查询。当前会话未发现连接时，AI 会询问你希望由 AI 协助配置，还是自行配置。

### 让 AI 帮你连接

把这句话发给正在使用的 AI 助手：

> 请帮我连接 DrugAlpha 创新药数据库 MCP。先检查并复用当前连接。需要配置时，先阅读 DrugAlpha 官方 GitHub 仓库 https://github.com/max-nobug/drugalpha-mcp 中的 README.md 和 INSTALL-AGENT.md，再按本客户端支持的方式帮我添加。需要登录授权或填写个人 Key 时，告诉我入口和操作，再等我完成。最后请实际查询恒瑞医药的公司身份，告诉我查询是否成功。

客户端支持时，AI 可以检查现有连接、添加服务、打开授权入口，并在授权后发起实际查询。若客户端只允许用户在设置页操作，AI 会给出对应菜单和填写内容。看到本次数据库实际返回的公司信息，才算验证通过。连接成功后，具体数据权限仍以账号订阅和授权为准。

### 自己配置 DrugAlpha MCP

| 方式 | 适用情况与配置 |
|---|---|
| 官方连接器或插件（推荐） | 客户端能找到 DrugAlpha 官方应用、连接器或插件时，直接启用，再由你在浏览器用自己的账号登录并授权。 |
| 正式 MCP 地址 + OAuth | 在客户端的自定义 MCP 设置中添加服务。名称填 `drugalpha`，地址填 `https://drugalpha.com/mcp`，传输选 Streamable HTTP。保存后完成浏览器 OAuth 授权。 |
| 个人 Key | Alpha 派和 AlphaEngine 可在「DrugAlpha创新药投研」入口直接粘贴个人 Key，输入框只填 Key 本身。其他支持远程 HTTP 和自定义请求头的客户端可用同一 MCP 地址。登录 DrugAlpha 创新药投研数据库，在导航栏打开[连接我的 AI](https://drugalpha.com/connect-ai)，选择「个人 Key 连接」，进入[创建与管理个人 Key](https://drugalpha.com/oauth/keys)，按账号权限创建并复制 Key。使用自定义请求头的客户端在凭据设置中填写 `Authorization: Bearer <个人 Key>`。如客户端提供专用 Bearer Token 输入框，按其说明填写。请直接在客户端填写 Key。 |

WorkBuddy 可从「专家·技能·连接器 → 连接器 → 自定义连接器 → 添加 MCP」进入。Codex 可用插件的认证入口，或用 `codex mcp add drugalpha --url https://drugalpha.com/mcp` 添加后运行 `codex mcp login drugalpha`。授权后可刷新工具列表或新开会话。OAuth 授权可在[我的连接](https://drugalpha.com/oauth/connections)管理。更多入口见[连接我的 AI](https://drugalpha.com/connect-ai)和[DrugAlpha 官方接入指南](https://github.com/max-nobug/drugalpha-mcp)。

## 3. 接入自己的资料，开始研究

- **Obsidian 等本地知识库**。在支持本地文件读取的客户端中，指定 Obsidian 知识库（Vault）的本地路径，并授权 Agent 读取本次相关的子目录或笔记。普通 Markdown 知识库、本地投研资料文件夹也可以用同样的方式。
- **上传文件或使用已连接的知识库**。可上传研报、纪要、公告或估值表，也可指定已授权的知识库连接，并说明要查的目录、主题或日期范围。

可先让 Agent 读出一份资料的标题、日期和一条关键假设，核实它实际读到了正文。在客户端启用允许的联网工具，用论文、公告、注册和监管原文补证。只有文件读取能力时，数据库和联网部分仍需分别配置相应工具。

> 使用 DrugAlpha创新药投研专家团。综合研究首药控股，把临床与竞争判断传导到销售、估值和融资假设，指出最能改变观点的事件。 请只读取我授权的 Obsidian 知识库里「公司研究／首药控股」目录，结合 DrugAlpha 数据和公开一手资料进行核对。

## 六种模式怎样使用

直接用自然语言提问即可。Agent 根据交付目标自动选择模式，也支持点名使用。六种模式涉及 DrugAlpha 覆盖的数据时，先调用本题对应的 DrugAlpha MCP，或复用本次研究已取得的同口径有效返回。公司财务和行情使用已授权的金融 MCP 或官方披露。六种模式以完整研究为主要任务时，默认交付同版 Markdown 与 Word。涉及销售峰值、正式公司 DCF 估值或多公司评分时，附相应可复算 Excel。

| 模式 | 可以这样问 | 模板 |
| --- | --- | --- |
| 临床数据对比 | 这三款RET抑制剂的一线数据谁更好？ | [报告模板](references/clinical-report-template.md) |
| 竞争格局分析 | 同一适应症谁领先，哪些产品真正争夺同一批患者？ | [报告模板](references/competition-report-template.md) |
| 销售峰值测算 | 这款药在中国一线治疗能卖多少钱？ | [报告模板](references/peak-report-template.md) · [Excel 模板](assets/peak-sales-template.xlsx) |
| 催化剂判断 | 未来半年哪些读出可能改变观点？ | [报告模板](references/catalyst-report-template.md) |
| 财务与估值分析 | 现金能撑到下一次读出吗？现在估值隐含多少销售？ | [财务分析模板](references/financial-report-template.md) · [估值分析模板](references/valuation-report-template.md) · [DCF Excel 模板](assets/dcf-valuation-template.xlsx) |
| 综合决策 · 单公司投资研判 | 综合研究首药控股，给出投资判断、关键催化剂和最强反证。 | [报告模板](references/integrated-single-report-template.md) · [DCF Excel 模板](assets/dcf-valuation-template.xlsx) |
| 综合决策 · 多公司投资研判 | 比较映恩生物、贝达药业、劲方医药和众生药业，给出优先研究顺序和打分理由。 | [报告模板](references/integrated-multi-report-template.md) · [评分 Excel 模板](assets/multi-company-scoring-template.xlsx) · [评分 Excel 编制说明](references/integrated-multi-report-template.md#03排序依据与主要约束) |

综合决策包含以上两种用法。单公司研判附可复算 DCF Excel。多公司研判附可复算评分 Excel，并链接实际采用的 DCF 工作簿。

## 文件怎么读

| 文件 | 用途 |
|---|---|
| [README.md](README.md) | 客户安装、授权和首次使用 |
| [SKILL.md](SKILL.md) | Agent 任务入口 |
| [references/integrated-workflow.md](references/integrated-workflow.md) | 综合决策执行手册，含单公司投资研判与多公司投资研判，专项入口见 SKILL.md |
| [references/data-access.md](references/data-access.md) | 本技能的 DrugAlpha 调用方法和数据口径 |
| [references/sources.md](references/sources.md) | 客户知识库读取、公开补证和证据冲突处理 |
| [references/preferences.md](references/preferences.md) | 客户可编辑的研究范围、方法和输出偏好 |

references 内的模块资料采用 `模块-用途.md` 命名。`-workflow` 是执行流程，`-mcp` 是接口指引，`-evidence` 是取证规则，`-methods` 是补充方法（可加主题），`-report-template` 是报告框架。竞争格局与销售峰值的共用资料使用 `competition-peak-` 前缀，全团共用资料沿用各自的功能名。

报告模板、Excel 底稿和已完成样板的统一存放规则见[模板与样板](references/report-delivery.md#模板与样板)。

专业附录按 SKILL.md 和执行手册中的链接按需阅读。由旧版升级时，将旧包客户偏好文件中的个人设置迁移到新版 references/preferences.md，再启用新版本。不同客户端的实际安装和授权结果以客户环境为准。

## 如何让反馈在下次生效

直接指出上一份报告漏了什么，以及这个遗漏为什么影响你的判断。专家团会先修正当次结论，再把可复用的方法写入你的私有研究经验。新开会话后可问：“这次比较会采用我之前反馈过的哪些检查？”一次性要求不会变成长期偏好；你也可以要求查看、修改或删除某条经验。

个人安装默认保存在 references/lessons.md；升级前备份其中“本客户已保存的条目”，再迁入新版同名章节。也可在 references/preferences.md 指定包外私有持久文件，升级时保留该设置。共享或只读安装须使用可写的客户私有位置才可跨会话保存。对外分发请使用干净发行包。
销售峰值按[峰值手册](references/peak-workflow.md)复用康诺亚 DCF 单药页的患者流与终端销售公式，并交付 Markdown、Word 与可复算 Excel。