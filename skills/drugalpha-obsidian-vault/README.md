# DrugAlpha创新药Obsidian知识库

[English](README.en.md) · 简体中文

用现有公告、研报、纪要和估值模型搭建一套本地 Obsidian 知识库。你可以直接在 Obsidian 浏览和编辑，AI 可以归档新资料、维护公司和药物笔记，并把投研答案追溯到原件和页码。

## 安装 Skill

### 复制给 AI 安装

把下面的提示词发给你的 AI。也可以[下载 Skill ZIP](https://github.com/max-nobug/drugalpha-skills/releases/download/obsidian-v0.1.3/drugalpha-obsidian-vault-v0.1.3.zip)，按客户端说明安装。

~~~text
请从以下 GitHub 目录安装 DrugAlpha 创新药 Obsidian 知识库 Skill。
https://github.com/max-nobug/drugalpha-skills/tree/main/skills/drugalpha-obsidian-vault

请先阅读 Skill 目录里的 README.md，按当前客户端支持的方式安装整个目录。如果需要我手动导入 ZIP，请给出对应客户端的操作步骤。
手动安装用的 Skill ZIP
https://github.com/max-nobug/drugalpha-skills/releases/download/obsidian-v0.1.3/drugalpha-obsidian-vault-v0.1.3.zip

安装后确认能调用这个 Skill。等我提供本地 Obsidian Vault 路径后，请在我授权的范围内做一次笔记只读验证。只有我需要匹配标准药名或查询已发布管线时，再检查 DrugAlpha MCP。需要接入时，参考下面的官方说明。
DrugAlpha MCP 官方连接说明
https://github.com/max-nobug/drugalpha-mcp
~~~

分享或安装时，保留整个 `drugalpha-obsidian-vault/` 文件夹，其中包含 `SKILL.md`、`references/`、`assets/` 和 `agents/`。请在能访问资料原件和目标 Vault 的本地客户端使用。建库和维护需要目标目录的写入权限，日常查询只需要读取权限。

### Codex

1. 解压技能包，将整个 `drugalpha-obsidian-vault/` 文件夹复制到用户级技能目录。Windows 可在资源管理器地址栏打开 `%USERPROFILE%\.agents\skills`，macOS 或 Linux 使用 `~/.agents/skills/`。目录不存在时可以新建。仅供一个项目使用时，也可以放到该项目的 `.agents/skills/` 下。
2. 确认最终目录是 `.agents/skills/drugalpha-obsidian-vault/SKILL.md`。如果已有同名 Skill，更新已有副本即可。
3. 在本地工作目录打开新对话。桌面端可在技能列表查看，CLI 或 IDE 扩展可输入 `$drugalpha-obsidian-vault` 选中。列表没有刷新时重启 Codex。 [OpenAI 官方 Skill 文档](https://developers.openai.com/codex/skills)

### WorkBuddy

1. 将完整的 `drugalpha-obsidian-vault/` 文件夹压缩成 ZIP，保留其中的 `SKILL.md` 和配套目录。收到完整 ZIP 时可以直接使用。
2. 在 WorkBuddy 桌面端打开「专家·技能·连接器 → 技能 → 添加技能 → 上传技能」，选择本地 ZIP。导入后在「已安装」确认该技能已启用。 [WorkBuddy 官方技能文档](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)
3. 在本地任务中选择能访问原件和 Vault 的工作文件夹，在对话里明确说「使用 DrugAlpha创新药Obsidian知识库 Skill」，然后附上下面的示例指令。

### 其他客户端

按客户端的 Skill 导入入口或技能目录安装。支持本地文件读取的客户端也可以把完整目录放进工作区，并请 AI 读取 `SKILL.md`。客户端需要能访问本次任务涉及的原件和 Vault。

技能包包含中英文安装说明、执行手册和知识库模板。本地归档和只读查询使用你授权的本地文件。需要匹配药物或查询已发布管线时，使用你自己的 DrugAlpha 账号接入 MCP。[DrugAlpha MCP 官方连接说明](https://github.com/max-nobug/drugalpha-mcp)

## 首次建库

准备好以下信息即可开始。资料现有的目录结构也可以一起交给 AI 盘点。

- **Obsidian 和目标目录**　安装 Obsidian，提供新建 Vault 的目标路径，或提供现有 Vault 的根目录。 [Obsidian 官方下载](https://obsidian.md/download)
- **来源和范围**　提供原件所在的本地绝对路径，说明优先处理的公司、年份、资料类型及需要跳过的内容。
- **一小批试导入资料**　选一家公司和几份常用文件，搭配一个你实际会问的投研问题。若要建立药物目录，再指定首批药物名称或代号。

AI 先只读盘点，展示目录和样例映射。你确认 Vault、是否按行业分层、原件处置方式及试导入范围后，AI 建目录并试导入。新建库默认从来源复制原件。试导入完成后，在 Obsidian 打开 Vault，抽查一份笔记能否跳到原件，再决定是否批量导入。

按行业分层时，行业研报、行业纪要、跨公司比较和行业催化剂汇总放在对应行业的 `00 行业研究/`。公司公告、研报、纪要和估值模型归到对应公司目录。可提取的文字默认转成 Markdown，原件留在各归档目录的 `_原件/`。Excel 估值模型保留原工作簿，图片和扫描页按需提出 OCR 请求。 [归档与证据规则](references/archive-and-evidence.md)

## 日常归档与维护

1. 若已启用 `00 待入库/`，把新增公告、纪要、研报和模型放进去，再发指令「归档待入库」。AI 按确认过的目录和原件处置方式归档，补来源链接和可核实的页码。遇到未建库的公司，AI 会列出拟建目录和资料，请你确认。
2. 已启用增量维护时，AI 将有证据支持的实质变化更新到 `notes.md` 和相关单药笔记，补「后续催化」和有实质关联的双链。催化窗口按披露记录，未披露时间的事项留待跟踪。
3. 你可以直接在 Obsidian 编辑笔记。AI 归档、补链或更新前会读最新保存版本，保留你修改的正文、属性和链接。涉及 `key-driver.md` 既有投资判断、核心变量或验证条件的调整，AI 先给出原文、拟改文字和原件出处，你确认后再写回。 [药物笔记与增量维护](references/drug-notes-and-maintenance.md)

## 用 AI 查询

发起查询时给出 Vault 路径、公司或药物、研究范围和截至日期。只想用库内资料时，明确写「仅用已归档资料，全程只读」。药物问题可直接用研发代号或商品名，请 AI 先沿双链找证据，再用标准名和已核实的别名全文补查。客户端通过文件名或索引搜不到时，要求直接搜索已归档 Markdown 的正文和 aliases，并报告实际搜索范围和命中的文件。要求逐项区分已发生进展、当时预期和待核实事项，并附原件物理页码或稳定定位。 [检索验收方法](references/acceptance.md)

## 可直接复制的示例指令

把方括号里的内容换成自己的路径、公司、药物和日期。

**首次建库或改造旧库**

~~~text
使用「DrugAlpha创新药Obsidian知识库」Skill。资料在［原件目录绝对路径］，目标 Vault 是［新建或现有 Vault 的绝对路径］。先只读盘点［公司名］，展示公司目录、行业资料的放置位置和 5 份文件的归档样例，等我确认后再试导入。
~~~

**归档待入库**

~~~text
使用「DrugAlpha创新药Obsidian知识库」Skill，归档［Vault 绝对路径］下的 00 待入库。沿用现有规则，核对原件、页码和双链，并同步维护相关公司的 notes.md、单药笔记和后续催化。遇到未建库公司请列出拟建路径和待归档文件，等我确认。
~~~

**补链并维护现有笔记**

~~~text
使用「DrugAlpha创新药Obsidian知识库」Skill，维护［Vault 绝对路径］里的［公司名］。先读 Obsidian 里最新保存的笔记，保留我的手动修改。用药物标准名和已核实的别名找候选，核对上下文后补双链和出处。涉及 key-driver.md 的投资判断调整，先给我看原文、拟改文和出处，确认后再写。
~~~

**只读投研查询**

~~~text
使用「DrugAlpha创新药Obsidian知识库」Skill，只读［Vault 绝对路径］里已归档的本地资料，不联网或调用外部数据。以［药物别名］查［公司名］在［地区／适应症］的研发或注册进展，截至［日期］。先沿单药笔记双链找证据，再用药物标准名和别名全文补查。逐项区分实际进展、当时预期和待核实事项，附原件路径及 PDF 物理页码。
~~~

完整的 AI 执行流程见 [SKILL.md](SKILL.md)，文档模板见 [模板说明](references/templates.md)。
