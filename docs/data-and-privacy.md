# Data and privacy

[← Back to the README](../README.md)

What Digital FTE reads, what it keeps, what it sends out, and what each output
shows. Everything stays on your Mac unless this page says otherwise.

1. [What it reads](#what-it-reads)
2. [What it keeps](#what-it-keeps)
3. [The four levels of data](#the-four-levels-of-data)
4. [What leaves your Mac](#what-leaves-your-mac)
5. [Your controls](#your-controls)
6. [Keeping this page true](#keeping-this-page-true)

---

## What it reads

The history files your coding agents already keep. They are opened read-only.
The app never changes, moves or deletes them.

| Agent | Where the history is |
|---|---|
| Claude Code | `~/.claude/projects` |
| opencode | `~/.local/share/opencode/opencode.db` |
| Cursor | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` |
| Cline | `~/.cline/data/sessions` |

You can switch any agent off, or limit the folders it counts, in
[`config.toml`](configuration.md).

---

## What it keeps

One SQLite database on your Mac: `~/.digital-fte/fte.db`.

| Table | What is in it |
|---|---|
| `sources` | Which history files were read, and when |
| `sessions` | One row per agent session: agent, model, project name, folder, title, times, counts of prompts, actions and lines, token counts |
| `events` | Every prompt, reply and action in a session: the text (first 4,000 characters of each message), the tool used, and the file or command it touched |
| `tasks` | Work split from sessions: title, category, roles, hours (H, K, Y, net), an evidence count, and the summary sent to the AI step |
| `ai_estimates` | What the AI service said about each task: hours, category, purpose, roles and a one-sentence reason |
| `corrections` | Your changes: hours, keep factor, category, purpose, roles, excluded |

API keys are not in the database. They are read from the macOS Keychain or an
environment variable when needed.

---

## The four levels of data

Every field falls in one of these levels. Level 1 is the safest to share and
level 4 should never leave your Mac.

| Level | What it is | Fields |
|---|---|---|
| **1. Totals** | Numbers added up over a month, with no tasks or names | Monthly DFTE and likely range, net hours, task count, leverage, keep rate, agent cost, cost per DFTE, DFTE by agent, DFTE by purpose, virtual team (DFTE per role, team value, cost per person) |
| **2. Task details** | One row per task, with no names | Task id, agent, category, purpose, roles and shares, start and end time, counts of prompts, actions and events, H, H low and high, K and how it was set, Y, net hours, excluded, evidence counts (lines, files read, commands, web lookups, words) |
| **3. Names** | Fields that can reveal a customer, a project or the work | Project name, folder path, session title, task title, the AI's reason for an estimate |
| **4. Content** | What you and the agent actually wrote | Prompts, replies, tool names, file paths, commands, and the task summary (prompt excerpts, edited file names, a reply excerpt) |

Notes:

- The **evidence** field holds counts only, so it is level 2.
- The **AI reason** is one sentence about the work. It can mention the kind of
  deal or the tools involved, so it is level 3.
- The **task summary** holds prompt excerpts, file names and a reply excerpt,
  so it is level 4.
- Level 4 also shows level 3: the summary quotes the project name and the first
  prompts. Switching content on reveals names too.

---

## What leaves your Mac

| Output | What it shows or sends | Levels |
|---|---|---|
| **Dashboard** | Everything, on `127.0.0.1`. Only your Mac can open it | 1 to 4 |
| **Share summary page** (`fte share`) | Totals and role names. No task, project or customer names | 1 |
| **AI service you choose** | A short summary per task: up to three prompt excerpts (300 characters each), edited file names, counts, an excerpt of the final reply (400 characters), and the titles and labels of up to 10 tasks you corrected, as examples. Never the full transcript | 4 |
| **MCP server** (`fte mcp`) | Read-only, for your own agents. Follows four switches in `config.toml`; only totals are on to begin with. Every question is logged by field name | 1 to 4, set by you ([details](mcp-server.md)) |
| **Anything else** | Nothing | none |

The AI step is the one place level 4 data leaves your Mac. Use a local model,
or turn the AI step off, to send nothing. See
[Configuration](configuration.md#settings).

---

## Your controls

- **Agents:** switch each agent on or off.
- **Folders:** `include_paths` and `exclude_paths` limit which sessions count.
  Excluded sessions are also left out of the share page.
- **AI service:** pick which service reviews tasks, use a local model, or turn
  the step off.
- **Learning:** `learn_from` sets which of your corrections are shown to the AI
  as examples. An empty list shows none.
- **Corrections:** exclude any task from the totals.
- **MCP server:** four switches under `[mcp.profiles.me]` choose what your own
  agents may see. Only totals are on to begin with. See [the MCP server](mcp-server.md).
- **Share name:** `fte share --name` adds your name to the summary. Without it,
  the page carries no name.

---

## Keeping this page true

When you add a field, a table, an output or an MCP tool, update the tables
above in the same change. The MCP server's sharing levels use this page as the
list of what each field is.
