# digital-fte

A local dashboard that turns your coding-agent history (Claude Code, opencode,
Cursor, Cline) into a **Digital FTE** number: how many full-time people's worth
of work your agents delivered this month.

Runs only on this Mac, bound to `127.0.0.1`. No transcript leaves the machine.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Look and feel: ASCII agent orbit, dashboard, sample data | done |
| 2 | Readers for all four agents, local database, `fte scan`, `config.toml` | done |
| 3 | Task splitting, rules-based H, K, Y, real monthly DFTE | done |
| 4 | AI estimates (Cortex, Claude, OpenAI, Gemini, local), editable tasks, learning from corrections | done |
| 5 | Daily refresh, history kept after agents prune it, cost per DFTE over time, shareable summary | **done** |

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"          # add ,claude for the Claude connector
.venv/bin/fte scan           # read agent history into ~/.digital-fte/fte.db, rebuild tasks
.venv/bin/fte estimate       # recompute tasks and print rules vs final DFTE per month
.venv/bin/fte estimate --ai  # also send tasks the AI hasn't seen yet (--limit N)
.venv/bin/fte serve          # → http://127.0.0.1:8765 (also rescans on start)
.venv/bin/fte refresh        # scan + AI estimates: what the daily job runs
.venv/bin/fte schedule install --at 20:00   # daily refresh via launchd (also: remove, status)
.venv/bin/fte share --name Amrendra         # write the totals-only summary page
.venv/bin/python -m pytest   # reader tests
```

`fte scan` only re-reads sessions that changed since the last scan; `--full`
re-reads everything, `--agent cursor` scans one agent.

The dashboard has two views, switched at the top right:

- **DFTE**: your sessions split into tasks, each with H, K, Y and net hours,
  adding up to Digital FTE per month (month to date for the current one), with
  leverage, cost per DFTE and keep rate. Each task shows the evidence its H came
  from; hover K to see why it was kept or not.
- **Activity**: the raw sessions: prompts, agent actions, lines written and
  active time.

## Where history comes from

| Agent | Default path | Format |
|---|---|---|
| Claude Code (CLI + desktop) | `~/.claude/projects` | JSONL per session; subagents under `<session>/subagents/` |
| opencode | `~/.local/share/opencode/opencode.db` | SQLite: `session_v2`, `session_message` |
| Cursor | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` | SQLite key-value: `composerData:*`, `bubbleId:*` |
| Cline (VS Code + CLI, v4+) | `~/.cline/data/sessions` | `<id>.json` + `<id>.messages.json` per session |

Agent files are opened **read-only**. Each reader turns its agent's format into
the same shape (session → prompts, replies, actions) in
`src/digital_fte/collectors/`. Adding an agent = one new module + one line in
`collectors/__init__.py`.

What each agent records differs:

- Cursor keeps line counts per chat, not per edit, and doesn't store token counts.
- opencode and Cline sessions on Cortex models report tokens; cost is 0 in the
  default config, so set `cost_per_month` if you pay for them.

## Settings: `~/.digital-fte/config.toml`

Created with defaults on first run. The dashboard's **Agents** switches edit
`enabled` in place (comments are kept). Other settings:

- `path` per agent, if its history lives somewhere else
- `cost_per_month` per agent
- `include_paths` / `exclude_paths`: only count, or never count, sessions whose
  working folder matches
- `hours_per_fte_month`, `default_keep`, `review_cap_minutes`,
  `human_cost_per_month` for the DFTE formula
- `[rates]`: every number the rules use to turn evidence into hours
- `[ai]`: which AI service refines the estimates (see below)

## The local database: `~/.digital-fte/fte.db`

- `sessions`: one row per session with agent, project, model, start/end,
  active minutes (gaps over 10 min left out), prompts, replies, actions, lines,
  tokens.
- `events`: every prompt, reply and action, with the first 4,000 characters of
  prompt and reply text. Phase 3/4 use this text to split and estimate tasks.
  It stays in this file on this Mac.
- `sources`: what was read and its version, so rescans are incremental.
- `tasks`: rebuilt from `events` after every scan, with H, K, Y, net hours, the
  evidence behind them, and the short summary the AI step is shown. A task's id
  is `<session>@<start second>`, so it stays the same across rebuilds.
- `ai_estimates`: one row per task per AI service and model.
- `corrections`: your edits to H, K, category, or "not real work".

Delete the file any time; the next scan rebuilds it.

**Project** is the session's working folder, or, when that's the home folder
or a scratch dir, the folder most of its edited files are in. OneDrive
`…/Project/<Country>/<Customer>` paths become `Customer (Country)`.

## The calculation

```
Net hours (task) = max(0, H × K − Y)
DFTE (month)     = Σ Net hours ÷ 140 productive hours per FTE
```

**Tasks.** Each session is split where you prompt again after a break of more
than 30 minutes. Tiny exchanges ("thanks!") fold into the neighbouring task.
Subagent sessions join the parent task that launched them, and their
instructions don't count as your prompts.

**H: hours a competent person would need.** The person depends on the task:
a developer for code, a presales consultant for documents and demo prep, an
analyst for research and analysis.

```
H = code lines ÷ 50/h + doc lines ÷ 40/h + removed lines ÷ 150/h
  + answer words ÷ 800/h + reads × 3m + commands × 2m + web × 5m
```

One edit counts at most 300 lines, so a huge generated file can't dominate.
The low and high estimates are H × 0.7 and H × 1.4. Driving a browser for
testing counts as commands, not web research.

**K: keep factor**, averaged over the files a task edited: 1.0 if in git, 0.9
if still on disk, 0.3 if gone. 0.7 when there's nothing to check (chat answers,
analysis).

**Y: your time.** Typing each prompt (40 wpm, max 5 min, since long prompts are
usually pasted), plus the time between the agent finishing and your next
prompt (max 10 min), plus reading the final answer (250 wpm).

All of these numbers live in `[rates]` and `[dfte]` in the config.

**Known limits of the rules** (what Phase 4's Cortex step is for):

- Lines of code drive most of H. AI-written code is often longer than a person
  would write, so code-heavy tasks lean high; a hard fix with few lines leans
  low.
- Y only counts time the transcript shows. Thinking time away from the
  keyboard isn't visible, so leverage leans high.
- Category comes from file types and a few keywords.

## AI estimates (Phase 4)

Rules measure volume; the AI step judges difficulty. For each task it reads a
short summary (up to three prompt excerpts of 300 characters, edited file names,
evidence counts, a 400-character excerpt of the final reply, the rules estimate),
never the transcript, and returns a title, category, H (low, likely, high), K
and a one-sentence reason.

How the final numbers are built (`refine.py`):

1. The AI's H replaces the rules H, kept within **0.25×–2×** of it.
2. The AI's K is used only when no edited files could be checked.
3. **Your corrections win.** Click a task to set H, K, category, or mark it
   "not real work" (net hours 0). Reset puts the estimate back.
4. **Learning:** after 3+ corrected H values in a category, the typical ratio
   (median, clamped 0.33–3×) is applied to that category's other tasks.

The dashboard shows the final DFTE, with **rules only** next to it and as a
dotted line on the trend.

### Choosing the AI service

```toml
[ai]
use = "cortex"        # cortex | claude | openai | gemini | local | off
auto = true           # estimate new tasks after each scan

[ai.cortex]
api = "openai"                            # OpenAI-compatible chat completions
base_url = "https://cortex.helixops.ai/v1"
model = "cortex-fast"
key_keychain = "cortex-api-key"           # macOS Keychain service name
parallel = 1                              # requests at a time
pause_seconds = 3                         # gap between requests
```

- `api = "openai"` covers Cortex, OpenAI, Gemini (its OpenAI-compatible
  endpoint), Ollama and LM Studio. `api = "anthropic"` uses the official
  `anthropic` SDK (`pip install -e ".[claude]"`).
- Keys come from `key_keychain` or `key_env`, never from the file.
- Set `model` for OpenAI and Gemini before using them.
- The **AI estimates** panel shows each service's status and switches between
  them. Estimates are stored per service and model, so switching keeps both
  sets and lets you compare them.
- Cortex allows one request at a time per person and answers `429` when busy;
  the app waits as asked and pauses between requests so your own Cortex agents
  aren't queued behind it.

## Daily refresh

`fte schedule install --at 20:00` adds a macOS launch agent
(`~/Library/LaunchAgents/local.digital-fte.refresh.plist`) that runs
`fte refresh --if-older-than 12`:

- every day at the set time, at low priority;
- **if the Mac is asleep then**, as soon as it wakes (launchd runs a missed
  calendar job once on wake, however many days were missed);
- **if the Mac was shut down**, at login. The 12-hour check stops it re-running
  every time you log in.

Missing days loses nothing: agents keep their history and the next run catches
up. The one thing that can't be caught up is history an agent has deleted.
Claude Code removes transcripts after about 30 days by default, so the app
**keeps every session it has read**, even after the agent deletes the file.

The job and the dashboard server share file locks (`~/.digital-fte/.scan.lock`,
`.ai.lock`), so they never scan or call the AI service at the same time. Output
goes to `~/.digital-fte/refresh.log`; the Settings section shows the schedule
and the last run. If you move the project folder, run `fte schedule install`
again, since the job points at this folder's Python.

## Sharing

`fte share --name <you>` writes `~/.digital-fte/summary.html`, and the
dashboard serves the same page at `/share` (link under Settings → Share). It
holds totals only: monthly DFTE with range and rules-only, net hours, tasks,
leverage, cost per DFTE and keep rate, plus splits by agent and kind of work.
No task names, prompts, file names, project or customer names. Cost per DFTE
counts the agents' monthly subscriptions, and for the current month only the
days elapsed so far.

## The orbit

`static/js/orbit.js` uses the same method as personal-clone-site: work out an
ink density for every character cell, then map it through the ramp
` .:-=+*#%@`.

- The core is a slowly turning globe sized by the month's total (actions now,
  DFTE from Phase 3).
- Each enabled agent is a shaded sphere on its own tilted orbit, sized by its
  share. Agents active in the last hour move faster, leave a short trail and
  get a pulsing dot on their label.
- Hover or click an agent (sphere or label) to filter the whole dashboard.
- Mono / Color toggle; light and dark themes; respects reduced motion; pauses
  when the tab is hidden or the hero is off-screen; throttled to ~30fps.
