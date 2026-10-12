"""~/.digital-fte/config.toml: which agents count, where their history lives,
and the numbers the DFTE formula uses. Created with defaults on first run."""

from __future__ import annotations

import fnmatch
import os
import re
import tomllib
from pathlib import Path

HOME_DIR = Path(os.environ.get("DFTE_HOME", "~/.digital-fte")).expanduser()
CONFIG_PATH = HOME_DIR / "config.toml"
DB_PATH = HOME_DIR / "fte.db"

AI_DEFAULT = """\
# AI that refines task estimates. `use` picks the active service; "off" = rules only.
# Only short task summaries are sent, never full transcripts. Keys are never stored
# here: each service reads its key from the macOS Keychain (key_keychain = the
# Keychain service name) or an environment variable (key_env).
[ai]
use = "cortex"
auto = true                    # estimate new tasks automatically after each scan
max_tasks_per_run = 400

[ai.cortex]
api = "openai"                 # OpenAI-compatible chat completions
base_url = "https://cortex.helixops.ai/v1"
model = "cortex-fast"
key_keychain = "cortex-api-key"
parallel = 1                   # Cortex allows one request at a time per person
pause_seconds = 3              # gap between requests, so your own agents aren't queued behind this

[ai.claude]
api = "anthropic"              # needs: pip install -e ".[claude]"
model = "claude-haiku-5-5"
key_env = "ANTHROPIC_API_KEY"

[ai.openai]
api = "openai"
base_url = "https://api.openai.com/v1"
model = ""                     # set the model you want to use
key_env = "OPENAI_API_KEY"

[ai.gemini]
api = "openai"                 # Gemini's OpenAI-compatible endpoint
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
model = ""                     # set the model you want to use
key_env = "GEMINI_API_KEY"

[ai.local]
api = "openai"                 # Ollama / LM Studio on this Mac, no key needed
base_url = "http://localhost:11434/v1"
model = ""
"""

DEFAULT = """\
# digital-fte settings. Edit freely; the dashboard's Agents switches update `enabled`.

[dfte]
hours_per_fte_month = 140      # 20 days x 7 productive hours
default_keep = 0.7             # K when the app can't tell whether output was used
review_cap_minutes = 10        # max review time counted per agent reply
human_cost_per_month = 9000    # loaded cost of the comparable human role (USD)

# Only count sessions whose working folder matches one of these (empty = everything).
include_paths = []
# Never count sessions whose working folder matches one of these.
exclude_paths = []

# How Phase 3 turns evidence into human-hours (H). Tune these to your own pace.
[rates]
code_lines_per_hour = 50       # finished code a competent developer writes per hour
doc_lines_per_hour = 40        # lines of documents/markdown (~500 words) per hour
removed_lines_per_hour = 150   # lines deleted or rewritten during refactors
answer_words_per_hour = 800    # analysis/answers the agent gave you in chat
read_minutes = 3               # per file read or code search
command_minutes = 2            # per command run
web_minutes = 5                # per web search or page fetched
max_lines_per_edit = 300       # one huge generated file counts as at most this many lines
task_gap_minutes = 30          # a break longer than this starts a new task
typing_wpm = 40                # your typing speed, for prompt-writing time
reading_wpm = 250              # your reading speed, for reviewing the final answer
range_low = 0.7                # low estimate = H x this
range_high = 1.4               # high estimate = H x this

[agents.claude_code]
enabled = true
path = "~/.claude/projects"
cost_per_month = 200

[agents.opencode]
enabled = true
path = "~/.local/share/opencode/opencode.db"
cost_per_month = 0

[agents.cursor]
enabled = true
path = "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
cost_per_month = 20

[agents.cline]
enabled = true
path = "~/.cline/data/sessions"
cost_per_month = 0

""" + AI_DEFAULT


def _block(name: str) -> str:
    """The default text of one [section], comments included."""
    m = re.search(rf"^(#[^\n]*\n)*\[{re.escape(name)}\][^\[]*", DEFAULT, re.M)
    if not m:
        return ""
    # [^\[]* also swallows the next section's leading comment; drop it.
    lines = m.group(0).rstrip().split("\n")
    while lines and lines[-1].startswith("#"):
        lines.pop()
    return "\n".join(lines).rstrip() + "\n"


def ensure() -> None:
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(DEFAULT)
        return
    # Sections added in later versions are appended, so older files gain them visibly.
    text = CONFIG_PATH.read_text()
    if not re.search(r"^\[rates\]", text, re.M):
        text = text.rstrip() + "\n\n" + _block("rates")
    ai = re.search(r"^(#[^\n]*\n)*\[ai\]\n(?:(?!^\[(?!ai\.)).*\n?)*", text, re.M)
    if not ai:
        text = text.rstrip() + "\n\n" + AI_DEFAULT
    elif not re.search(r"^use\s*=", ai.group(0), re.M):
        # Phase 3 had one [ai] table; replace it with the per-service layout.
        text = text[: ai.start()] + AI_DEFAULT + text[ai.end():]
    if text != CONFIG_PATH.read_text():
        CONFIG_PATH.write_text(text)


def load() -> dict:
    ensure()
    cfg = tomllib.loads(CONFIG_PATH.read_text())
    defaults = tomllib.loads(DEFAULT)
    cfg.setdefault("dfte", {})
    for k, v in defaults["dfte"].items():
        cfg["dfte"].setdefault(k, v)
    for section in ("rates", "ai"):
        cfg.setdefault(section, {})
        for k, v in defaults[section].items():
            if isinstance(v, dict):
                cfg[section].setdefault(k, dict(v))
            else:
                cfg[section].setdefault(k, v)
    cfg.setdefault("agents", {})
    return cfg


def set_agent_enabled(agent_id: str, enabled: bool) -> None:
    """Flip `enabled` inside [agents.<id>] only, keeping the rest of the file
    (comments included) exactly as the person wrote it."""
    ensure()
    text = CONFIG_PATH.read_text()
    section = re.search(rf"^\[agents\.{re.escape(agent_id)}\][^\[]*", text, re.M)
    value = "true" if enabled else "false"
    if not section:
        text = text.rstrip() + f"\n\n[agents.{agent_id}]\nenabled = {value}\n"
    else:
        body = section.group(0)
        if re.search(r"^enabled\s*=.*$", body, re.M):
            body = re.sub(r"^enabled\s*=.*$", f"enabled = {value}", body, count=1, flags=re.M)
        else:
            body = body.replace("]\n", f"]\nenabled = {value}\n", 1)
        text = text[: section.start()] + body + text[section.end():]
    CONFIG_PATH.write_text(text)


def path_allowed(cwd: str | None, cfg: dict) -> bool:
    def match(patterns):
        c = os.path.expanduser(cwd or "")
        return any(fnmatch.fnmatch(c, os.path.expanduser(p)) or c.startswith(os.path.expanduser(p).rstrip("*"))
                   for p in patterns)
    inc, exc = cfg["dfte"].get("include_paths") or [], cfg["dfte"].get("exclude_paths") or []
    return (not inc or match(inc)) and not (exc and match(exc))


def set_ai_use(name: str) -> None:
    """Switch the active AI service (`use = ...` in [ai]), keeping the rest of the file."""
    ensure()
    text = CONFIG_PATH.read_text()
    text = re.sub(r'^(use\s*=\s*)"[^"]*"', lambda m: f'{m.group(1)}"{name}"', text, count=1, flags=re.M)
    CONFIG_PATH.write_text(text)
