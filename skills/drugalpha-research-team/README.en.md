# DrugAlpha Biopharma Research Team

English · [简体中文](README.md)

Release v0.2.70. Use DrugAlpha, your authorized research notes and public primary sources for investment research, business development, medical affairs and reimbursement evidence.

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
| **Technical Analysis Analyst** | Combines price structure and confirmed signals to give action recommendations, supporting evidence, and trigger conditions. |
| **Chief Research Analyst (Integrated Research)** | Defines the research questions, coordinates specialists, resolves differences in evidence, and brings clinical, competitive, and catalyst findings into sales and valuation assumptions. A **single-company review** presents the thesis, key catalysts, and strongest counterevidence. A **multi-company review** explains the scores and ranks the companies for further research. |

Full research includes matching Markdown and Word reports by default. Peak sales, formal DCF valuations, and multi-company scoring include a recalculable Excel workbook. Reports default to the language of your question, with Chinese or English available on request.

Single-company and multi-company reviews use their fixed seven-section templates and table layouts. Technical analysis has its own section; the six fundamental scoring factors and weights remain independent. Both reviews include Markdown, Word and the applicable recalculable Excel workbooks.

## Install

Extract the release ZIP and keep the complete directory with [SKILL.md](SKILL.md) at its root. Import the package through your client's Skills settings or place the directory in its documented Skill location. If your client only supports file access, place the directory in its workspace and ask it to read SKILL.md.

### Copy this prompt to install with AI

Copy the prompt below into your AI assistant. You can also [download the Skill ZIP](https://github.com/max-nobug/drugalpha-skills/releases/download/v0.2.70/drugalpha-research-team-v0.2.70-public.zip) and install it using your client’s instructions.

~~~text
Please install or update the DrugAlpha Biopharma Research Team Skill to v0.2.70 in my current AI client.

Read README.en.md in the directory below, then install the complete Skill directory using the method supported by my client. Keep the directory structure intact.
https://github.com/max-nobug/drugalpha-skills/tree/main/skills/drugalpha-research-team

If I need to import the ZIP manually, tell me where to go in my client and which steps to follow. Use this release ZIP.
https://github.com/max-nobug/drugalpha-skills/releases/download/v0.2.70/drugalpha-research-team-v0.2.70-public.zip

After installation, confirm that my client can invoke the updated Skill. Run a live, read-only DrugAlpha query to verify the identity of Hengrui Pharma and confirm the MCP connection. If setup is needed, follow the official guide and let me complete account authorization.
https://github.com/max-nobug/drugalpha-mcp

For future tasks that generate Excel or Word files, check the relevant Python dependencies first. If any are missing, provide installation commands for my client and operating system.
~~~

### Check Python dependencies by deliverable

Before generating Excel or Word files, the assistant checks the packages and versions in the Python environment that will run the scripts. Read-only research and Markdown can proceed immediately.

| Deliverable | Python dependencies | Example install command when needed |
|---|---|---|
| DCF valuation workbook | Use the [DCF requirements file](scripts/dcf/requirements.txt), which lists `openpyxl`, `requests`, and `akshare`. | `python -m pip install -r scripts/dcf/requirements.txt` |
| Peak sales workbook | `openpyxl>=3.1,<4`. | `python -m pip install "openpyxl>=3.1,<4"` |
| Word report | `python-docx`, imported as `docx`. | `python -m pip install python-docx` |

Run the example commands from the actual Skill directory. Replace `python` with your client's interpreter, such as `python3`, `py -3`, or the Python executable in a virtual environment. If you run the DCF command from another directory, use the quoted absolute path to the requirements file. The assistant provides a command suited to your client and operating system when a dependency is missing. After installation, use the same interpreter to recheck the imports and run the relevant script with `--help`.

## Connect DrugAlpha MCP

[Register for a DrugAlpha account](https://drugalpha.com/) if you need one. Start with an existing connection if available. To set up a new connection, give your assistant this prompt.

> Help me connect DrugAlpha MCP. Check for an existing connection first. For setup, read README.md and INSTALL-AGENT.md at https://github.com/max-nobug/drugalpha-mcp and follow the instructions for my client. Show me where to authorize the connection or enter my personal key, then verify it with a live read-only query.

For manual setup, use the official connector where available or add `https://drugalpha.com/mcp` as a Streamable HTTP server named `drugalpha`. Authorize in your browser. In Alpha Pai or AlphaEngine, open the DrugAlpha research entry and paste the personal key into the key field as issued. For key creation and client-specific steps, open [Connect my AI](https://drugalpha.com/connect-ai) and [Personal Key management](https://drugalpha.com/oauth/keys). In the client's credential settings use `Authorization: Bearer <personal key>` when configuring a custom header. [Official client setup guide](https://github.com/max-nobug/drugalpha-mcp)

## Start a research task

Upload relevant files or authorize access to selected folders or notes in an Obsidian vault, another local knowledge base or a connected knowledge base. The Skill cross-checks relevant notes against DrugAlpha and public primary sources.

| Mode | Example | Templates |
| --- | --- | --- |
| Clinical comparison | Compare selpercatinib and pralsetinib for first-line RET fusion-positive advanced NSCLC. | [Report template](references/clinical-report-template.md) |
| Competitive landscape | Which VAV1 programs lead, and where do they compete? | [Report template](references/competition-report-template.md) |
| Peak sales | Estimate peak sales for this drug in China and overseas. | [Report template](references/peak-report-template.md) · [Excel template](assets/peak-sales-template.xlsx) |
| Catalysts | Which readouts could change the investment thesis over the next six months? | [Report template](references/catalyst-report-template.md) |
| Financial analysis and valuation | Can the company fund its next readout, and what does the current valuation imply? | [Financial analysis template](references/financial-report-template.md) · [Valuation template](references/valuation-report-template.md) · [DCF Excel template](assets/dcf-valuation-template.xlsx) |
| Technical analysis | Should I wait, buy, or reduce, and under what conditions? | [Report template](references/technical-report-template.md) |
| Single-company investment review | Assess one company, its key catalysts, and the strongest counterevidence. | [Report template](references/integrated-single-report-template.md) · [DCF Excel template](assets/dcf-valuation-template.xlsx) |
| Multi-company investment review | Compare several companies, explain the scores, and rank them for further research. | [Report template](references/integrated-multi-report-template.md) · [Scoring Excel template](assets/multi-company-scoring-template.xlsx) · [Scoring workbook guide](references/integrated-multi-report-template.md#03排序依据与主要约束) |

Full research defaults to Markdown and Word. Peak sales, formal DCF and multi-company scoring also include a recalculable Excel workbook where applicable. Reports default to the language of your question. The internal manuals are in Chinese. The bundled Word formatter supports English clinical reports. For other English reports, check the client's document tooling and any workbook labels inherited from Chinese templates.

## Copy-ready research questions

Copy any question below and replace the company, drug, indication, region or time window. You do not need to specify an API or query method: the assistant selects the relevant workflow and checks the connection. These prompts do not presuppose superiority, approval, licensing, valuation upside or a trading conclusion. Full research defaults to Markdown and Word, plus the applicable recalculable Excel workbook; you may explicitly request a chat-only answer.

### Clinical data comparison

> Compare selpercatinib and pralsetinib for first-line RET fusion-positive advanced NSCLC. Explain the trial populations, PFS, OS and safety results, and which comparisons are valid.

### Competitive landscape

> Map the TROP2 ADC competitive landscape, focusing on SKB264. Compare clinical evidence and development progress within the same indications, treatment lines and patient subgroups. Where could it differentiate?

### Peak sales

> Estimate peak sales of SKB264 in China and overseas. Show the evidence and assumptions for patient counts, uptake, market share, price and treatment duration, with annual sales, sensitivity analysis and a recalculable Excel workbook.

### Catalyst assessment

> Assess Innovent Biologics’ key catalysts over the next 12 months. Rank them by importance, explain which outcomes could change the valuation thesis, and identify what to monitor.

### Financial analysis and valuation

> Assess whether Akeso’s cash can fund its key clinical milestones and evaluate financing and dilution risks. Build an independent DCF and explain differences versus the DrugAlpha platform reference value and same-date market capitalization.

### Technical analysis

> Analyze the current technical structure of Hengrui Pharma (600276.SH). Distinguish confirmed and pending signals, and give separate action recommendations for investors with and without a position, including the evidence, confirmation conditions and invalidation triggers.

### Single-company investment review

> Conduct an integrated investment review of Shouyao Holdings (688197.SH). Present the thesis, independent valuation and applicable scenario, key catalysts and strongest counterevidence. Explain how clinical and competitive findings affect sales and valuation assumptions, with a separate technical participation assessment.

### Multi-company investment review

> Compare Duality Biologics, Betta Pharmaceuticals, GenFleet Therapeutics and Zhongsheng Pharmaceutical using the six established fundamental factors. Explain every score, weighted totals and research priorities. Present independent DCF values, DrugAlpha platform reference values and separate technical action recommendations.

### BD due diligence

> Assess IBI363 for BD due diligence: clinical differentiation, competitive position, and sales potential in China and overseas. Distinguish disclosed rights from assumptions, summarize supporting evidence, and identify missing diligence materials.

### Medical affairs competitive analysis

> Compare the evidence for selpercatinib and pralsetinib in RET fusion-positive advanced NSCLC for a medical affairs review. Separate treatment lines and subgroups, efficacy, safety, approved labels and guideline positions, and explain comparability limits.

### Reimbursement evidence preparation

> Prepare a reimbursement evidence framework for SKB264 by proposed indication: standard of care, incremental clinical value, patient population, treatment costs and inputs needed for budget-impact analysis. Separate available evidence from missing materials.

### Research with local notes

> Read only the authorized “Company Research / Shouyao Holdings” folder in my Obsidian vault. Cross-check its clinical, catalyst and valuation assumptions against DrugAlpha and public primary sources, identify conflicts with citations, and produce a single-company investment review.
