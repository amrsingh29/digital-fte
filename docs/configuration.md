# Configuration, commands and troubleshooting

[← Back to the README](../README.md)

1. [Settings](#settings)
2. [Roles: the virtual team](#roles-the-virtual-team)
3. [Commands](#commands)
4. [Troubleshooting](#troubleshooting)

---

## Settings

All settings live in `~/.digital-fte/config.toml`, created on the first scan.
Each line has a comment explaining it. Open it with:

```bash
open -t ~/.digital-fte/config.toml
```

Changes apply when you reload the dashboard. Sections added by newer versions
are appended to your file automatically, so you can see them.

| Section | Setting | Default | What it does |
|---|---|---|---|
| `[dfte]` | `hours_per_fte_month` | 140 | Productive hours in one person's month |
| | `human_cost_per_month` | 9000 | Monthly cost of a person, for roles without their own cost |
| | `default_keep` | 0.7 | Keep factor when the app can't check |
| | `review_cap_minutes` | 10 | Most review time counted before each follow-up prompt |
| | `include_paths`, `exclude_paths` | [] | Only count, or never count, sessions in these folders |
| `[agents.*]` | `enabled` | true | Count this agent (also a switch in the dashboard's Settings) |
| | `path` | per agent | Where the agent keeps its history |
| | `cost_per_month` | per agent | Its monthly cost |
| `[purposes]` | `learning_weight` | 1.0 | How much general & learning counts (0 to 1) |
| | `admin_weight` | 1.0 | How much setup & admin counts (0 to 1) |
| `[ai]` | `use` | cortex | Active AI service: cortex, claude, openai, gemini, local or off (also in the dashboard's Settings) |
| | `auto` | true | Estimate new tasks automatically after each refresh |
| | `learn_from` | roles, purpose, category | Which of your corrections are shown to the AI as examples. `[]` = none. Hours are never sent |
| `[ai.*]` | `model`, `base_url`, `key_keychain`, `key_env` | per service | How to reach each AI service |
| | `parallel`, `pause_seconds` | 1, 3 for Cortex | Pace of AI requests, to leave room for your own agents |
| `[[roles]]` | `name`, `about`, `fallback`, `monthly_cost` | 10 roles | The virtual team's role catalog ([below](#roles-the-virtual-team)) |
| `[mcp.profiles.me]` | `totals`, `task_details`, `names`, `content` | true, false, false, false | What `fte mcp` may show your own agents ([guide](mcp-server.md)) |
| `[rates]` | 13 settings | | How evidence becomes hours (lines per hour, minutes per read, and so on) |

**Use one currency for every cost** (agents, `human_cost_per_month`, roles).
The dashboard shows "$", but the numbers work in any currency.

---

## Roles: the virtual team

Each `[[roles]]` block is one role the AI can pick:

```toml
[[roles]]
name = "Presales consultant"
about = "prepares customer demos, proposals, decks and customer-facing documents"
fallback = ["documentation", "demo prep"]
monthly_cost = 6750
```

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | What the role is called on the dashboard |
| `about` | no, but recommended | What the role covers. The AI reads it when choosing, so be specific |
| `fallback` | no | Categories (code, documentation, demo prep, research, analysis) or purposes (learning, admin) that get this role before the AI has reviewed a task |
| `monthly_cost` | no | What a person in this role costs per month where you work: salary plus employer costs. Defaults to `human_cost_per_month` |

**Add a role** by appending a block, for example:

```toml
[[roles]]
name = "ITSM platform consultant"
about = "configures and extends ITSM and AIOps platforms for customers"
monthly_cost = 9500
```

The AI only re-checks tasks that changed, so older tasks keep their roles. To
let the AI pick again for every task with the new catalog, run
`fte estimate --recheck-roles` (one AI request per task; your own role
corrections are kept). Removing or renaming a role sends its tasks back to their
first guess until they're re-checked.

---

## Commands

| Command | What it does |
|---|---|
| `fte serve` | Start the dashboard at http://127.0.0.1:8770 (rescans on start) |
| `fte scan` | Read new agent history. `--full` re-reads everything; `--agent cursor` reads one agent |
| `fte estimate` | Recompute tasks and print DFTE per month (rules vs final) |
| `fte estimate --ai` | The same, and send tasks the AI hasn't seen yet. `--limit N` for a few |
| `fte estimate --recheck-roles` | Ask the AI to estimate every task again, including roles, after you change the role catalog. Each task keeps its current estimate until the new one arrives |
| `fte refresh` | Scan and AI estimates together: what the daily job runs |
| `fte schedule install --at HH:MM` | Turn on the daily refresh. Also `remove` and `status` |
| `fte share --name NAME` | Write the summary page to `~/.digital-fte/summary.html` |
| `fte mcp` | Start the read-only MCP server for your own agents ([guide](mcp-server.md)). `--dry-run TOOL` prints what a tool would return instead |

Run them as `.venv/bin/fte …` from the project folder, or as `fte …` if you
added the [alias](setup-mac.md#9-optional-a-shorter-command).

---

## Troubleshooting

**Start with the log:** `~/.digital-fte/app.log` records every scan (per agent),
every AI run (start, end, and each failure with its reason), and any unexpected
error with its details, from both the dashboard and the daily job. It never
contains prompt or reply text, file contents or keys, so it's safe to attach to
a bug report. It rotates at 1 MB and keeps three old files. The daily job also
writes its own short log to `~/.digital-fte/refresh.log`.

```bash
tail -50 ~/.digital-fte/app.log
```

| Problem | What to do |
|---|---|
| Banner says **"… was unreachable; will retry"** | The AI service couldn't be reached (offline, asleep, off VPN). The run stopped and the remaining tasks stay pending; the next run picks them up. Nothing to fix unless it keeps happening while you're online. |
| An agent shows **not found** | Its history is somewhere else. Set its `path` in `config.toml`. |
| **"No key found in …"** in Settings → AI estimates | Store the key with `security add-generic-password` ([setup step 6](setup-mac.md#6-choose-an-ai-service-optional-recommended)), check `key_keychain` matches the name you used, then reload the page after a minute. |
| **"Set a model in config.toml"** | Fill in `model` for that service. |
| **"N failed"** in the banner | Those tasks got a real error (e.g. a rejected request). They're retried on every run; `app.log` shows the reason for each. |
| AI estimates fail with **HTTP 429** | The service is busy. The app waits and retries. For a shared quota, raise `pause_seconds`. |
| AI estimates fail with **HTTP 403** | Check the key and `base_url`. Some company endpoints also need you to be on the company network or VPN. |
| **"already running in another process"** | Another AI run is busy, usually the dashboard's. Nothing was changed; try again when it finishes (Settings → AI estimates shows progress). |
| **Address already in use** when starting | Another `fte serve` is running. Use it, or start on another port: `fte serve --port 8771`. |
| The daily refresh doesn't run | Run `fte schedule status`. If you moved the project folder, run `fte schedule install` again. The log is `~/.digital-fte/refresh.log`. |
| The virtual team says many tasks are a **first guess** | The AI hasn't reviewed them yet. Wait for the next refresh, or click Rescan. |
| A new role isn't used for older tasks | Run `fte estimate --recheck-roles`. |
| Numbers look too high | Turn on AI review, then correct a few tasks so the app learns your view. |
| Start over | Delete `~/.digital-fte/fte.db` and run `fte scan`. Settings are kept, but your corrections are lost. |
