# DrugAlpha Biopharma Research Team

English · [简体中文](README.md)

Release v0.2.59. Use DrugAlpha, your authorized research notes and public primary sources for investment research, business development, medical affairs and reimbursement evidence.

## What the team does

The DrugAlpha Biopharma Research Team supports investment research, business development due diligence, medical affairs competitive analysis, and evidence preparation for reimbursement discussions. It queries DrugAlpha MCP first for available clinical, pipeline, competitive landscape, and catalyst data. It also uses authorized local materials, including Obsidian notes, and public primary sources to assess drug differentiation, events that could change the investment thesis, and expectations implied by the current valuation.

You can use any specialist independently or ask the Chief Research Analyst (Integrated Research) to coordinate a single-company or multi-company investment review.

| Research role | Responsibilities |
|---|---|
| **Clinical Data Comparison Analyst** | Aligns indications, treatment lines, and subgroups. Compares efficacy and safety with the current standard of care, evaluates PFS and OS separately, and identifies each drug's strengths and limitations. |
| **Competitive Landscape Analyst** | Maps competitors by target and indication, compares development progress, and identifies the leading programs and which ones compete for the same patients. |
| **Peak Sales Analyst** | Estimates patient counts, uptake, share, price, and treatment duration for China and overseas markets. Delivers annual sales and peak sales with a recalculable Excel model. |
| **Catalyst Analyst** | Tracks out-licensing, regulatory filings and approvals, clinical readouts, sales uptake, and royalty receipts for core assets. Prioritizes events and specifies what outcomes would change the investment thesis. |
| **Financial and Valuation Analyst** | Assesses cash runway, financing needs, and dilution risk. Builds an independent valuation, presents the DrugAlpha platform reference value, and explains what the market price implies. |
| **Chief Research Analyst (Integrated Research)** | Defines the research questions, coordinates specialists, resolves differences in evidence, and brings clinical, competitive, and catalyst findings into sales and valuation assumptions. A **single-company review** presents the thesis, key catalysts, and strongest counterevidence. A **multi-company review** explains the scores and ranks the companies for further research. |

Full research includes matching Markdown and Word reports by default. Peak sales, formal DCF valuations, and multi-company scoring include a recalculable Excel workbook. Reports default to the language of your question, with Chinese or English available on request.

## Install

Extract the release ZIP and keep the complete directory with [SKILL.md](SKILL.md) at its root. Import the package through your client's Skills settings or place the directory in its documented Skill location. If your client only supports file access, place the directory in its workspace and ask it to read SKILL.md.

### Copy this prompt to install with AI

Copy the prompt below into your AI assistant. You can also [download the Skill ZIP](https://github.com/max-nobug/drugalpha-skills/releases/download/v0.2.59/drugalpha-research-team-v0.2.59-public.zip) and install it using your client’s instructions.

~~~text
Install the DrugAlpha Biopharma Research Team Skill from this GitHub directory.
https://github.com/max-nobug/drugalpha-skills/tree/main/skills/drugalpha-research-team

Read README.en.md in the Skill directory and install the entire directory using the method supported by my client. If I need to import the ZIP manually, give me the steps for my client.
Skill ZIP for manual installation
https://github.com/max-nobug/drugalpha-skills/releases/download/v0.2.59/drugalpha-research-team-v0.2.59-public.zip

After installation, check that the Skill is available and run a read-only DrugAlpha query to verify the MCP connection. If setup is needed, follow the official guide below and tell me where to complete authorization.
Official DrugAlpha MCP setup guide
https://github.com/max-nobug/drugalpha-mcp
~~~

## Connect DrugAlpha MCP

[Register for a DrugAlpha account](https://drugalpha.com/) if you need one. Start with an existing connection if available. To set up a new connection, give your assistant this prompt.

> Help me connect DrugAlpha MCP. Check for an existing connection first. For setup, read README.md and INSTALL-AGENT.md at https://github.com/max-nobug/drugalpha-mcp and follow the instructions for my client. Show me where to authorize the connection or enter my personal key, then verify it with a live read-only query.

For manual setup, use the official connector where available or add `https://drugalpha.com/mcp` as a Streamable HTTP server named `drugalpha`. Authorize in your browser. In Alpha Pai or AlphaEngine, open the DrugAlpha research entry and paste the personal key into the key field as issued. For key creation and client-specific steps, open [Connect my AI](https://drugalpha.com/connect-ai) and [Personal Key management](https://drugalpha.com/oauth/keys). In the client's credential settings use `Authorization: Bearer <personal key>` when configuring a custom header. [Official client setup guide](https://github.com/max-nobug/drugalpha-mcp)

## Start a research task

Upload relevant files or authorize access to selected folders or notes in an Obsidian vault, another local knowledge base or a connected knowledge base. The Skill cross-checks relevant notes against DrugAlpha and public primary sources.

| Mode | Example |
|---|---|
| Clinical comparison | Compare selpercatinib and pralsetinib for first-line RET fusion-positive advanced NSCLC. |
| Competitive landscape | Which VAV1 programs lead, and where do they compete? |
| Peak sales | Estimate peak sales for this drug in China and overseas. |
| Catalysts | Which readouts could change the investment thesis over the next six months? |
| Financial analysis and valuation | Can the company fund its next readout, and what does the current valuation imply? |
| Integrated research | Assess one company, or compare several companies and rank them for further research. |

Full research defaults to Markdown and Word. Peak sales, formal DCF and multi-company scoring also include a recalculable Excel workbook where applicable. Reports default to the language of your question. The internal manuals are in Chinese. The bundled Word formatter supports English clinical reports. For other English reports, check the client's document tooling and any workbook labels inherited from Chinese templates.
