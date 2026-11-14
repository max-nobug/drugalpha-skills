# DrugAlpha MCP 连接和药物清单查询

用于本 Skill 的公司药物目录。DrugAlpha 数据库 MCP 提供公司管线和药物标准身份，公告、年报和临床原件用于核对别名、公司角色及权益。接入步骤参考 DrugAlpha 创新药投研专家团安装说明，并与 DrugAlpha 的公开指南核对。[官方接入指南](https://github.com/max-nobug/drugalpha-mcp) · [给 Agent 的安装指引](https://github.com/max-nobug/drugalpha-mcp/blob/main/INSTALL-AGENT.md)

## 连接与验证

已有可用连接时直接复用。优先阅读官方入口说明，客户端版本和工具字段以当前环境为准。首次连接需要客户端支持远程 Streamable HTTP MCP 和浏览器 OAuth。组织管理的客户端使用管理员提供的连接入口。AI 协助选择适配的入口并合并 DrugAlpha 配置，用户在浏览器中登录自己的 DrugAlpha 账号并完成授权。授权凭据留在客户端和授权页面。

- **WorkBuddy**　打开「专家·技能·连接器 → 连接器 → 自定义连接器 → MCP 服务管理 → 添加 MCP」。以下为官方文档所示的 WorkBuddy 配置。已有其他服务时，只把 `drugalpha` 项合并到现有的 `mcpServers`。
  
  ```json
  {
    "mcpServers": {
      "drugalpha": {
        "type": "http",
        "url": "https://drugalpha.com/mcp",
        "timeout": 60000,
        "disabled": false
      }
    }
  }
  ```

- **Codex**　已安装 DrugAlpha 插件时使用插件的认证入口。支持手动 CLI 接入时，可在本机执行以下命令，并按提示完成浏览器 OAuth。
  
  ```bash
  codex mcp add drugalpha --url https://drugalpha.com/mcp
  codex mcp login drugalpha
  ```

- **其他 Agent**　从客户端支持的自定义远程 MCP 入口添加。服务名为 `drugalpha`，传输为 Streamable HTTP，地址为 `https://drugalpha.com/mcp`。实际配置格式以客户端支持为准。

授权后刷新工具列表或新开会话。用用户选定的公司做一次真实的只读查询，先查公司身份，再确认能返回该公司的管线记录。没有指定对象且需要验收时，可以用恒瑞医药。用实际查询结果判断连接状态。权限、订阅、公司歧义和无覆盖分别说明。工具未出现先刷新或新开会话，授权确实失效时再按客户端提示重新连接。[DrugAlpha 我的连接](https://drugalpha.com/oauth/connections)

## 从公司管线建立药物清单

1. 发现当前客户端已经注册的工具，按实时参数调用。用 `resolve_entities` 查询公司身份，唯一匹配后才继续。有歧义时核对公司简称、全称或股票代码。
2. 用本次返回的公司标识调用 `get_pipeline`，按同一条件分页读完所需范围。按已确认的药物身份归并不同适应症、阶段和来源写法。同一药物在清单只占一行。无确认身份的条目标记待核对，分页中断或部分覆盖时注明范围。
3. 记录 DrugAlpha 返回的标准名、来源名称和可用的版本或日期。实体检索可核对本次命中的别名，管线记录也可能提供其他来源写法。以公司原件补充已核实的别名和具体角色，保留地域或权益出处。管线关系本身不足以确认自研、许可条款或当前阶段。
4. 将核实后的结果写入公司 `药物/药物清单.md`，在本 Skill 的药物目录流程中继续补单药笔记和双链。DrugAlpha 查询受限时，报告具体缺口，继续可独立完成的归档工作。用户明确调整数据来源时，另行标注实际来源。

查询范围限于用户选定的公司和药物。面向用户的清单使用可读名称及来源，内部实体标识用于本次工具调用。
