# DrugAlpha Biopharma Obsidian Vault

English · [简体中文](README.md)

Build a local Obsidian vault from company filings, research reports, meeting notes, and valuation models. Edit notes directly in Obsidian, and use an agent to archive new material, maintain company and drug notes, or answer research questions with links to the original files and page numbers.

## Install the skill

### Copy this prompt to install with AI

Copy the prompt below into your AI assistant. You can also [download the Skill ZIP](https://github.com/max-nobug/drugalpha-skills/releases/download/obsidian-v0.1.1/drugalpha-obsidian-vault-v0.1.1.zip) and install it using your client’s instructions.

~~~text
Install the DrugAlpha Biopharma Obsidian Vault Skill from this GitHub directory.
https://github.com/max-nobug/drugalpha-skills/tree/main/skills/drugalpha-obsidian-vault

Read README.en.md in the Skill directory and install the entire directory using the method supported by my client. If I need to import the ZIP manually, give me the steps for my client.
Skill ZIP for manual installation
https://github.com/max-nobug/drugalpha-skills/releases/download/obsidian-v0.1.1/drugalpha-obsidian-vault-v0.1.1.zip

After installation, check that the Skill is available. Once I provide my local Obsidian Vault path, verify read access to the notes I authorize. Check DrugAlpha MCP only if I ask to match drug names or query published pipeline records. Use the official setup guide below if needed.
Official DrugAlpha MCP setup guide
https://github.com/max-nobug/drugalpha-mcp
~~~

Keep the complete `drugalpha-obsidian-vault/` folder, including `SKILL.md`, `references/`, `assets/`, and `agents/`. Use a local client with access to your source files and vault. Building and maintaining the vault requires write access. Research queries need read access.

### Codex

1. Unzip the package and copy the entire skill folder into your user skills directory. On Windows, open `%USERPROFILE%\.agents\skills` in File Explorer. On macOS or Linux, use `~/.agents/skills/`. Create the directory if needed. For project-only use, put the folder in the project's `.agents/skills/` directory.
2. Check that the skill is at `.agents/skills/drugalpha-obsidian-vault/SKILL.md`. If you already have this skill installed, update that copy.
3. Start a new local conversation. Find the skill in the desktop skills list, or enter `$drugalpha-obsidian-vault` in the CLI or IDE extension. Restart Codex if the skill does not appear. [OpenAI skill documentation](https://developers.openai.com/codex/skills)

### WorkBuddy

1. Use the supplied ZIP. If you received an unpacked folder, zip the entire `drugalpha-obsidian-vault/` folder with its supporting files.
2. In the desktop app, open **Experts, Skills and Connectors → Skills → Add Skill → Upload Skill** and select the ZIP. In the Chinese interface, the labels are **专家·技能·连接器 → 技能 → 添加技能 → 上传技能**. Check the installed skills list to confirm it is enabled. [WorkBuddy skill documentation](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)
3. Choose a local working folder with access to your source files and vault. Ask WorkBuddy to use `drugalpha-obsidian-vault`, then paste one of the example prompts below.

### Other clients

Use your client’s Skill import settings or documented Skill directory. For a client with local file access, you can also place the complete directory in the workspace and ask the assistant to read `SKILL.md`. The client needs access to the files and vault involved in your task.

The package includes both installation guides, workflow references and vault templates. Local archiving and read-only queries use your authorized local files. For drug matching or published pipeline queries, connect DrugAlpha MCP with your own account. [Official DrugAlpha MCP setup](https://github.com/max-nobug/drugalpha-mcp)

## Set up your first vault

- Install Obsidian and choose a target folder, or provide the root path of an existing vault. [Obsidian download](https://obsidian.md/download)
- Provide the absolute paths to the source files and identify the companies, years, and document types to include. Mention any material to skip.
- Pick one company, a few representative files, and one question you would ask in practice. If you want a drug directory, include the first drug names or development codes.

The agent inventories the files in read-only mode and shows where the sample files would go. Confirm the vault location, industry grouping, handling of originals, and pilot scope before the pilot import. A new vault copies source files by default. After the pilot, open the vault in Obsidian and follow a note's link back to the original before approving a larger batch.

When grouping by industry, use `00 行业研究/` for industry reports, industry meeting notes, cross-company comparisons, and sector catalyst summaries. Company materials go in the applicable company directory. The agent converts extractable text to Markdown and keeps originals in the relevant `_原件/` directory. Excel valuation models retain their original workbook format. OCR for images and scans is available on request. [Archiving and evidence rules (Chinese)](references/archive-and-evidence.md)

## Archive and maintain the vault

1. If the inbox is enabled, put new files in `00 待入库/` and ask the agent to archive them. It follows your agreed filing and original-file rules, adds source links and verified page numbers, and asks you to approve any new company folders.
2. With incremental maintenance enabled, the agent records material changes in `notes.md` and relevant drug notes, updates upcoming catalysts, and adds links that connect relevant evidence. It uses disclosed timing and flags undated events for follow-up.
3. You can edit notes in Obsidian. The agent reads the latest saved version before making edits and preserves your changes to text, properties, and links. For changes to an existing investment judgment, tracking variable, or validation condition in `key-driver.md`, it shows the original wording, proposed edit, and supporting evidence for your approval before writing. [Drug notes and maintenance (Chinese)](references/drug-notes-and-maintenance.md)

## Ask research questions

Give the vault path, company or drug, research scope, and as-of date. For a local-only answer, ask for a read-only search of archived material. A development code or brand name can be the starting point. Ask the agent to follow links from the drug note, then search the archived text using the canonical name and verified aliases. Request original-file links and physical PDF page numbers, or another stable location, for each material claim. [Search acceptance checks (Chinese)](references/acceptance.md)

## Copy and paste these prompts

Replace bracketed text with your own paths, company, drug, and date.

**Build or reorganize a vault**

~~~text
Use the drugalpha-obsidian-vault skill. My source files are at [absolute source path] and the target vault is [absolute vault path]. Start with a read-only inventory for [company]. Show the company folder structure, where industry material would go, and the proposed locations for five sample files. Wait for my approval before importing the sample. Respond in English.
~~~

**Archive the inbox**

~~~text
Use the drugalpha-obsidian-vault skill to archive the files in 00 待入库 under [absolute vault path]. Follow the existing vault rules. Verify original files, page numbers, and note links. Update the relevant notes.md files, drug notes, and upcoming catalysts. If a company folder is missing, show its proposed path and the files for my approval. Respond in English.
~~~

**Maintain existing notes and links**

~~~text
Use the drugalpha-obsidian-vault skill to update [company] in [absolute vault path]. Read the latest saved notes and preserve my Obsidian edits. Find candidate sources using canonical drug names and verified aliases. Check the context before adding links and citations. For any proposed change to an investment judgment in key-driver.md, show the original wording, proposed change, and supporting evidence for my approval. Respond in English.
~~~

**Run a read-only research query**

~~~text
Use the drugalpha-obsidian-vault skill. Search only archived local material in [absolute vault path], in read-only mode and without external sources. Start from [drug alias] and verify [company]'s development or regulatory progress in [region and indication] as of [date]. Follow links from the drug note, then search the archived text using the canonical name and verified aliases. Distinguish completed milestones, expectations at the time, and items still unverified. Cite original file paths and physical PDF page numbers. Respond in English.
~~~

The [workflow](SKILL.md), [reference manuals](references/templates.md), and vault templates use Chinese conventions for their paths, headings, and fields. Agent replies and research answers default to the language of your request. You can ask for either language explicitly.
