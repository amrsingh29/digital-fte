# Setup on a Mac

[← Back to the README](../README.md)

About 10 minutes. You need macOS, Python 3.12 or newer, and at least one of
Claude Code, opencode, Cursor (agent chats) or Cline installed and used.

## 1. Check Python and git

```bash
python3 --version
```

If it says 3.12 or higher, you're set. Otherwise install Python with
[Homebrew](https://brew.sh):

```bash
brew install python
```

If `git` isn't installed, macOS offers to install the command line tools the
first time you run it. You can also start that yourself:

```bash
xcode-select --install
```

## 2. Get the code

```bash
git clone https://github.com/amrsingh29/digital-fte.git ~/Developer/digital-fte
```

The repository is private; ask the owner for access first.

## 3. Install

```bash
cd ~/Developer/digital-fte
python3 -m venv .venv
.venv/bin/pip install -e ".[claude]"
```

`[claude]` adds the Anthropic SDK for the Claude connector. Leave it off
(`pip install -e .`) if you won't use Claude.

## 4. Run the first scan

```bash
.venv/bin/fte scan
```

This creates your settings file at `~/.digital-fte/config.toml` and shows which
agents it found:

```
claude_code  on   78 sessions, 78 re-read
opencode     on   28 sessions, 28 re-read
cursor       on   18 sessions, 18 re-read
cline        on   4 sessions, 4 re-read
```

An agent you don't use shows `not found`. That's fine.

## 5. Check your numbers

Open the settings file:

```bash
open -t ~/.digital-fte/config.toml
```

Before trusting the numbers, set these four:

| Setting | Where | Why |
|---|---|---|
| `cost_per_month` | under each `[agents.…]` | What you pay for each agent. Use 0 if your company pays and you don't know the cost. |
| `human_cost_per_month` | `[dfte]` | Monthly cost of a person in your role, for the cost comparison |
| `hours_per_fte_month` | `[dfte]` | Productive hours in a month where you work (140 by default) |
| `enabled` | under each `[agents.…]` | Turn off agents you don't want counted |

Optional: give each job role its own `monthly_cost` under `[[roles]]` (what a
person in that role costs where you work), so the virtual team's cost is fair.
Use the same currency for every cost in the file. See
[Configuration](configuration.md#roles-the-virtual-team).

## 6. Choose an AI service (optional, recommended)

Without AI the app uses rules only, which overestimate code-heavy work. To turn
on AI review, store your API key in the macOS Keychain (the command asks for it,
so it never appears on screen or in your shell history), then select the service.

**Claude (Anthropic):**

```bash
security add-generic-password -a "$USER" -s anthropic-api-key -w
```

Then, in `config.toml`:

```toml
[ai]
use = "claude"

[ai.claude]
key_keychain = "anthropic-api-key"
```

**OpenAI or Gemini:** the same steps, with service names such as
`openai-api-key` or `gemini-api-key`. Also set `model` under `[ai.openai]` or
`[ai.gemini]` to the model you want to use.

**Your company's own endpoint** (any OpenAI-compatible service, such as Cortex):
set its `base_url`, `model` and `key_keychain` under `[ai.cortex]`, or add a new
`[ai.<name>]` block with `api = "openai"`.

**A local model** with [Ollama](https://ollama.com): set `model` under
`[ai.local]` and `use = "local"`. No key needed, and nothing leaves your Mac.

**No AI:** `use = "off"`.

## 7. Start the dashboard

```bash
.venv/bin/fte serve
```

Open **http://127.0.0.1:8765** in your browser. To see what the app looks like
before your own data is in, see [Try it with sample data](../README.md#try-it-with-sample-data). The first time, the AI step
estimates all your existing tasks in the background; the banner shows its
progress.

## 8. Turn on the daily refresh

```bash
.venv/bin/fte schedule install --at 20:00
```

| At refresh time your Mac is… | What happens |
|---|---|
| on | it runs at that time, at low priority |
| asleep (lid closed) | it runs as soon as the Mac wakes; several missed days count as one run |
| shut down | it runs when you log in |

Each run is skipped if the app already scanned in the last 2 hours (the
dashboard scans when it starts and when you click Rescan), so logging in several
times a day doesn't repeat it.

Missed days lose nothing. Agents keep their history, and the next run catches
up. Some agents delete old history (Claude Code removes transcripts after about
30 days by default), so the app keeps every session it has ever read.

### Run the refresh yourself

You don't have to wait for the scheduled time.

| To | Run |
|---|---|
| Refresh now (scan + AI estimates) | `.venv/bin/fte refresh` |
| Run the scheduled job now, exactly as macOS would | `launchctl kickstart -k gui/$(id -u)/local.digital-fte.refresh` |
| See whether the job is set up, and its last run | `.venv/bin/fte schedule status` |
| Follow the job's log | `tail -f ~/.digital-fte/refresh.log` |
| Change the time, e.g. to 6:30 pm | `.venv/bin/fte schedule install --at 18:30` |
| Stop the daily refresh | `.venv/bin/fte schedule remove` |

`fte refresh` always runs. The scheduled job skips itself if a scan happened in
the last 2 hours; to apply the same rule by hand, use
`fte refresh --if-older-than 2`. Clicking **Rescan** in the dashboard scans
straight away, and new tasks are then estimated in the background.

## 9. Optional: a shorter command

Add this line to `~/.zshrc`, then open a new terminal window. You can then type
`fte serve` from anywhere.

```bash
alias fte="$HOME/Developer/digital-fte/.venv/bin/fte"
```

## Updating

```bash
cd ~/Developer/digital-fte
git pull
.venv/bin/pip install -e ".[claude]"
```

Then restart `fte serve`. Your settings and data in `~/.digital-fte/` are kept.

## Uninstalling

```bash
.venv/bin/fte schedule remove
rm -rf ~/Developer/digital-fte
```

Your data and settings are in `~/.digital-fte/`. Delete that folder too if you
want them gone.

