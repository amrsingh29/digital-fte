# The MCP server

[← Back to the README](../README.md)

`fte mcp` lets your own AI agents (Claude Code, opencode, Goose) ask about your
Digital FTE: "what was my DFTE in October?", "which tasks should I review?".
It is read-only. You decide, with four switches, what it may show.

1. [Why the switches matter](#why-the-switches-matter)
2. [Switches](#switches)
3. [Change what is shared](#change-what-is-shared)
4. [Test each level](#test-each-level)
5. [Tools](#tools)
6. [Connect your agent](#connect-your-agent)
7. [Check what an agent would see](#check-what-an-agent-would-see)
8. [Safety](#safety)
9. [If something goes wrong](#if-something-goes-wrong)

---

## Why the switches matter

Your agent is a tool you talk to, and it can answer from any data it is given.
Once it has a piece of data, that data is part of the conversation, and the
agent sends it to its AI service to reply. What you switch on here decides what
can reach the agent at all.

- **Totals** are monthly numbers with no names. They are safe to have on.
- **Task details** show each task's hours and roles. They say how you spend your
  time, but not on what work.
- **Names** add project names, customer names and task titles. These are the
  first thing that identifies a customer, so keep them off unless you need them.
- **Content** adds your prompt and reply excerpts. This is your own work in your
  own words, and it can contain anything you typed.

The defaults are the safest choice: only totals are on. Anything you turn on is
a decision to let that kind of data reach your agent, so turn each one on only
for the question that needs it, then turn it off again.

---

## Switches

They live in `~/.digital-fte/config.toml`. Only totals are on to begin with:

```toml
[mcp.profiles.me]
totals = true
task_details = false      # turn on when you want your agent to see them
names = false
content = false
```

The levels are the same ones described in
[Data and privacy](data-and-privacy.md#the-four-levels-of-data).

| Switch | What your agent can then see |
|---|---|
| `totals` | Monthly DFTE and range, net hours, leverage, keep rate, cost per DFTE, the virtual team, splits by agent and by kind of work |
| `task_details` | One row per task: agent, category, purpose, roles, times, H, K, Y, net hours, evidence counts. Needed for the task tools |
| `names` | Adds each task's title, project name and the AI's reason. Works only with `task_details` on |
| `content` | Adds each task's summary: prompt excerpts, edited file names and a reply excerpt. Works only with `task_details` on |

Good to know:

- **A missing or mistyped switch counts as off.** `names = "true"` (with quotes)
  is off. A typo can only share less, never more.
- **`content` also shows names.** The summary quotes the project name and your
  first prompts, so turning `content` on reveals them too.
- **Changes apply to the next question.** There is nothing to restart.
- **Full file paths, commands and complete prompts or replies are never
  returned**, whatever the switches say.

---

## Change what is shared

The switches live in `~/.digital-fte/config.toml`, under `[mcp.profiles.me]`.

**1. Open the file**

```bash
open -t ~/.digital-fte/config.toml
```

**2. Find the section.** It sits at the bottom of the file:

```toml
[mcp.profiles.me]
totals = true
task_details = false      # turn on when you want your agent to see them
names = false
content = false
```

**3. Change the line you need.** Write `true` or `false` in lowercase, with no
quotes. For example, to let your agent see task details:

```toml
task_details = true
```

**4. Save the file.** The change applies to the next question. Nothing needs to
restart, and an agent session that is already open will pick it up.

**5. Check the result** before you rely on it:

```bash
cd ~/Developer/projects/digital-fte && .venv/bin/fte mcp --dry-run dfte_tasks --limit 1
```

The first line shows what is on, for example `sharing on: totals, task_details`.
The rest is what an agent would receive.

**Turn it back off** when you're done. Set the line to `false` and save.

> **Check the spelling.** A typo such as `ture` or `trues` makes the whole file
> unreadable, and the server then shares **nothing**. The dry-run shows
> `sharing on: nothing` when this happens. Fix the line and run the check again.

---

## Test each level

Run these in order, checking each result before going on. Start from the
defaults, with only totals on.

### Level 1: totals

Set nothing new. In Claude Code (or any agent with the server connected), ask:

> Use dfte to show my DFTE for October.

**Expect:** about 13.8 DFTE, a likely range, and a team breakdown. No task
names, project names or prompts appear.

### Level 2: task details

Set `task_details = true` and save. Then ask:

> Use dfte to list my 3 biggest tasks.

**Expect:** three tasks, each with agent, category, purpose, roles, hours and
evidence counts.

**Check:** no title, project name, reason or summary appears. Those are the
names and content levels.

Check the field list directly, without printing any text:

```bash
.venv/bin/fte mcp --dry-run dfte_tasks --limit 1 | python3 -c "import json,sys; print(sorted(json.load(sys.stdin)['tasks'][0]))"
```

The list should not contain `title`, `project`, `reason` or `summary`.

### Level 3: names

Set `names = true` and save. Ask the same question again:

> Use dfte to list my 3 biggest tasks, with their titles and project names.

**Expect:** each task now has a `title`, a `project` and a `reason`.

**Check:** `summary` still does not appear, because content is off.

### Level 4: content

Set `content = true` and save. Ask:

> Use dfte to show the summary of my most recent task.

**Expect:** a `summary` field with prompt excerpts, edited file names and a
reply excerpt.

**Check:** no full path such as `/Users/...` appears. File names are fine.

> **Careful at this level.** The output contains your real prompts and project
> names. Don't paste it into a shared document, and turn `content` off afterwards.

### Level 5: review queue

Set `task_details = true`, and leave the others as you need them. Ask:

> Use dfte to show tasks to review, biggest first.

**Expect:** tasks, each with a `why` field saying what needs a look, such as
"the AI estimate was held inside the guard rails".

### Check the audit log

The audit log records every question, with the field names but never the data:

```bash
tail -4 ~/.digital-fte/mcp-audit.log
```

Each line shows the tool, the switches that were on, and the field names. If a
title or a prompt appears in this file, stop and tell whoever maintains the code.

### Run the automated check

The tests cover every combination of the four switches:

```bash
cd ~/Developer/projects/digital-fte && .venv/bin/python -m pytest tests/test_mcp.py -q
```

It should report all tests passing.

### Reset

When you finish, set `task_details`, `names` and `content` back to `false`.
Keep `totals = true`.

---

## Tools

| Tool | What it returns | Needs |
|---|---|---|
| `dfte_summary(month?)` | Headline numbers per month, each month's virtual team, and splits by agent, kind of work and role. The splits (fields starting `current_month_`) always cover the current month, whichever month you ask for | `totals` |
| `dfte_tasks(month?, agent?, category?, purpose?, limit?)` | Tasks, newest first. 50 by default, 200 at most | `task_details` |
| `dfte_review_queue(same filters)` | Tasks worth a second look, biggest first, each with the reason | `task_details` |

A task is in the review queue when you have not corrected it and the AI step is
missing, hit its guard rails, or left the roles as a first guess.

If a switch is off, the tool says so and names the setting. There are no tools
that change anything. Corrections stay in the dashboard.

---

## Connect your agent

Use the full path to `fte` inside your project's `.venv`. Below it is shown as
`/path/to/digital-fte`.

**Claude Code**

```bash
claude mcp add --scope user dfte -- /path/to/digital-fte/.venv/bin/fte mcp
```

`--scope user` makes it available in every folder. Start a new `claude` session
after adding it; a session that is already open won't see the new server.

**opencode:** add to `~/.config/opencode/opencode.json`

```json
{
  "mcp": {
    "dfte": {
      "type": "local",
      "command": ["/path/to/digital-fte/.venv/bin/fte", "mcp"],
      "enabled": true
    }
  }
}
```

**Goose:** in Extensions, add a command-line extension named `dfte` with the
command `/path/to/digital-fte/.venv/bin/fte mcp`.

**Hermes:** add this under `mcp_servers:` in `~/.hermes/config.yaml`, next to any
servers already listed (keep the indentation):

```yaml
mcp_servers:
  dfte:
    command: "/path/to/digital-fte/.venv/bin/fte"
    args: ["mcp"]
```

Then ask something like "use dfte to show my DFTE for October".

---

## Check what an agent would see

Before you turn a switch on, look at exactly what comes out:

```bash
.venv/bin/fte mcp --dry-run dfte_tasks --month 2026-10 --limit 3
```

It prints the answer an agent would get right now, and which switches are on.
It works for each tool and takes the same filters. No server is started.

Every question is also written to `~/.digital-fte/mcp-audit.log`: the time, the
tool, the filters, which switches were on and the names of the fields returned.
It never contains the data itself.

---

## Safety

- **Read-only.** The database is opened so SQLite itself refuses any write. The
  server has no tool that changes anything.
- **Nothing goes out from here.** The server makes no network calls. It only
  answers the agent that started it, on your Mac.
- **What the agent receives goes to its AI service.** If you turn on `names` or
  `content`, those words travel with the rest of your conversation with that
  agent. Totals carry no names.
- **Task titles come from your own work,** so they can contain text that looks
  like an instruction. The server tells the agent to treat them as data.
- **Only your own agents.** There is no profile for anyone else yet. To share
  with a manager, use the [summary page](../README.md#using-it-day-to-day).

---

## If something goes wrong

| You see | What to do |
|---|---|
| "Sharing 'task_details' is switched off" | Set that switch to `true` in `config.toml` |
| "There is no database yet" or "not set up yet" | Run `fte scan`, then `fte estimate --ai` |
| "The database is busy" | A scan is writing. Ask again in a moment |
| "month must look like 2026-10" | Use a year and a two-digit month |
| `sharing on: nothing` in the dry-run | The config file has a typo, such as `ture` or `trues`. Fix the line; nothing is shared until the file reads cleanly |
| The agent does not list the tools | Check the path to `fte`, then restart the agent. `fte mcp --dry-run dfte_summary` should print numbers |
