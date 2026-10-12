"""Phase 3: turn sessions into tasks and estimate H, K and Y for each, with rules only.

    Net hours (task) = max(0, H x K - Y)
    DFTE (month)     = sum of net hours / hours_per_fte_month

Every number keeps its evidence, so the dashboard can show why a task got its H and K.
Phase 4 refines these with AI; the rules here stay as the baseline and the guard rails.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .collectors.base import first_line

# ---------- what kind of work an action is ----------

_EDIT = re.compile(r"^(edit|write|multiedit|notebookedit|editor|apply_patch|patch|search_replace|delete_file|"
                   r"write_to_file|replace_in_file|edit_file.*|create_file)$", re.I)
_READ = re.compile(r"^(read.*|glob.*|grep|ls|list.*|search.*|ripgrep.*|codebase_search|find.*|view)$", re.I)
_CMD = re.compile(r"^(bash|shell|run_terminal.*|execute_command|run_commands|execute|terminal)$", re.I)
_WEB = re.compile(r"(websearch|webfetch|web_search|web_fetch|fetch|browser|navigate|get_page_text)", re.I)
_SPAWN = re.compile(r"^(task|agent|task_v2|spawn.*)$", re.I)

DOC_EXT = {".md", ".mdx", ".txt", ".rst", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".csv", ".pdf", ".rtf"}

CATEGORIES_ROLE = ROLE = {"code": "developer", "documentation": "presales consultant", "demo prep": "presales consultant",
        "research": "analyst", "analysis": "analyst"}
_DEMO_WORDS = re.compile(r"\b(demo|deck|slides?|presentation|pptx|pitch|storyline|walkthrough|talk track|poc)\b", re.I)


def action_class(tool: str | None) -> str:
    t = tool or ""
    if t.startswith("mcp__"):
        # Driving a browser (screenshots, clicks) is testing, timed like a command;
        # only page reading and fetching count as web research.
        if re.search(r"browser|chrome|preview", t, re.I):
            return "web" if re.search(r"get_page_text|read_page|fetch", t, re.I) else "command"
        return "web" if _WEB.search(t) else "other"
    if _EDIT.match(t):
        return "edit"
    if _CMD.match(t):
        return "command"
    if _WEB.search(t):
        return "web"
    if _READ.match(t):
        return "read"
    if _SPAWN.match(t):
        return "spawn"
    return "other"


# ---------- tasks ----------

@dataclass
class Task:
    session_id: str
    agent: str
    project: str
    cwd: str | None
    events: list = field(default_factory=list)   # sqlite rows, sorted by ts

    @property
    def start(self) -> float:
        return self.events[0]["ts"]

    @property
    def human_prompts(self) -> list:
        return [e for e in self.events if e["kind"] == "prompt"]

    def substantive(self) -> bool:
        actions = sum(e["kind"] == "action" for e in self.events)
        words = sum(len((e["text"] or "").split()) for e in self.events if e["kind"] == "reply")
        return actions >= 2 or words >= 80


def split_tasks(session: sqlite3.Row, events: list, gap_min: float) -> list[Task]:
    """A new task starts when the person prompts again after a break longer than
    gap_min. Tiny tasks are folded into a neighbour so a quick follow-up question
    doesn't count as a separate piece of work."""
    new = lambda: Task(session["id"], session["agent"], session["project"], session["cwd"])
    tasks, cur, prev_ts = [], new(), None
    for e in events:
        if e["kind"] == "prompt" and cur.events and prev_ts is not None and e["ts"] - prev_ts > gap_min * 60:
            tasks.append(cur)
            cur = new()
        cur.events.append(e)
        prev_ts = e["ts"]
    if cur.events:
        tasks.append(cur)

    merged: list[Task] = []
    for t in tasks:
        if merged and not t.substantive():
            merged[-1].events += t.events
        elif merged and not merged[-1].substantive():
            t.events = merged[-1].events + t.events
            merged[-1] = t
        else:
            merged.append(t)
    return merged


# ---------- K: was the output kept? ----------

@lru_cache(maxsize=None)
def _git_root(folder: str) -> str | None:
    p = Path(folder)
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return str(d)
    return None


@lru_cache(maxsize=None)
def _tracked(root: str) -> frozenset[str]:
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, timeout=10, check=True).stdout
        return frozenset(os.path.join(root, f) for f in out.decode(errors="replace").split("\0") if f)
    except (subprocess.SubprocessError, OSError):
        return frozenset()


def keep_factor(files: list[str], default: float) -> tuple[float, str]:
    """1.0 if an edited file is in git, 0.9 if it still exists, 0.3 if it's gone.
    Tasks with no checkable files get the configured default."""
    files = sorted({f for f in files if f.startswith("/")})
    if not files:
        return default, "default: no edited files to check"
    scores, in_git, exist = [], 0, 0
    for f in files:
        if os.path.exists(f):
            root = _git_root(os.path.dirname(f))
            if root and f in _tracked(root):
                in_git += 1
                scores.append(1.0)
            else:
                exist += 1
                scores.append(0.9)
        else:
            scores.append(0.3)
    gone = len(files) - in_git - exist
    parts = [f"{in_git} in git" if in_git else "", f"{exist} still on disk" if exist else "", f"{gone} gone" if gone else ""]
    return round(sum(scores) / len(scores), 2), f"{len(files)} edited file{'s' * (len(files) != 1)}: " + ", ".join(p for p in parts if p)


# ---------- H and Y ----------

def _abs(path: str | None, cwd: str | None) -> str | None:
    if not path:
        return None
    p = os.path.expanduser(path)
    if not p.startswith("/") and cwd:
        p = os.path.join(os.path.expanduser(cwd), p)
    return p if p.startswith("/") else None


def estimate(task: Task, extra_lines: tuple[int, int], cfg: dict) -> dict:
    """Rules-based H, K, Y for one task. extra_lines are line counts the agent only
    records per session (Cursor), already apportioned to this task."""
    r, d = cfg["rates"], cfg["dfte"]
    cap = r["max_lines_per_edit"]
    ev = dict(code=0, doc=0, removed=0, reads=0, commands=0, web=0, edits=0, words=0, prompts=0)
    files, doc_edits = [], 0
    for e in task.events:
        if e["kind"] == "reply":
            ev["words"] += len((e["text"] or "").split())
        elif e["kind"] == "prompt":
            ev["prompts"] += 1
        elif e["kind"] == "action":
            cls = action_class(e["tool"])
            if cls == "edit":
                ev["edits"] += 1
                path = _abs(e["target"], task.cwd)
                if path:
                    files.append(path)
                is_doc = Path(e["target"] or "").suffix.lower() in DOC_EXT
                doc_edits += is_doc
                ev["doc" if is_doc else "code"] += min(e["lines_added"] or 0, cap)
                ev["removed"] += min(e["lines_removed"] or 0, cap)
            elif cls in ("read", "command", "web"):
                ev[{"read": "reads", "command": "commands", "web": "web"}[cls]] += 1
    added, removed = extra_lines
    if added or removed:
        share_doc = doc_edits / ev["edits"] if ev["edits"] else 0
        added = min(added, cap * max(1, ev["edits"]))
        ev["doc"] += round(added * share_doc)
        ev["code"] += added - round(added * share_doc)
        ev["removed"] += min(removed, cap * max(1, ev["edits"]))

    h = (ev["code"] / r["code_lines_per_hour"] + ev["doc"] / r["doc_lines_per_hour"]
         + ev["removed"] / r["removed_lines_per_hour"] + ev["words"] / r["answer_words_per_hour"]
         + (ev["reads"] * r["read_minutes"] + ev["commands"] * r["command_minutes"] + ev["web"] * r["web_minutes"]) / 60)
    h = max(h, 0.1)

    # Y: typing each prompt (capped, since long ones are usually pasted), reading time
    # between the agent finishing and the next prompt (capped), and reading the final answer.
    y_min = 0.0
    cap_min = d["review_cap_minutes"]
    last_agent_ts = None
    for e in task.events:
        if e["kind"] == "prompt":
            y_min += min(len((e["text"] or "").split()) / r["typing_wpm"], 5)
            if last_agent_ts is not None:
                y_min += min(max(0, e["ts"] - last_agent_ts) / 60, cap_min)
        elif e["kind"] in ("reply", "action"):
            last_agent_ts = e["ts"]
    replies = [e for e in task.events if e["kind"] == "reply" and e["text"]]
    if replies:
        y_min += min(len(replies[-1]["text"].split()) / r["reading_wpm"], cap_min)
    y = y_min / 60

    k, k_basis = keep_factor(files, d["default_keep"])

    prompts_text = " ".join(e["text"] or "" for e in task.events if e["kind"] in ("prompt", "instruction"))
    if ev["code"] + ev["doc"] > 0:
        category = "code" if ev["code"] >= ev["doc"] else "documentation"
    elif ev["web"] >= 3 or (ev["reads"] >= 5 and not ev["edits"]):
        category = "research"
    else:
        category = "analysis"
    if category != "code" and _DEMO_WORDS.search(prompts_text):
        category = "demo prep"

    first = next((e for e in task.events if e["kind"] == "prompt"), None) or \
        next((e for e in task.events if e["kind"] == "instruction"), None)
    return {
        "title": first_line(first["text"] if first else "") or "(no prompt)",
        "category": category, "role": ROLE[category],
        "h": round(h, 2), "h_low": round(h * r["range_low"], 2), "h_high": round(h * r["range_high"], 2),
        "k": k, "k_basis": k_basis, "y": round(y, 2),
        "net": round(max(0.0, h * k - y), 2),
        "evidence": ev,
    }


# ---------- what an AI reviewer is shown ----------

def summary(task: Task, est: dict, session: sqlite3.Row) -> str:
    """A short, plain description of one task for the AI step. Kept small on
    purpose: a few prompt excerpts, file names, counts. Never the transcript."""
    clipped = lambda t, n: (t or "").strip().replace("\n", " ")[:n] + ("…" if len(t or "") > n else "")
    prompts = [e["text"] for e in task.events if e["kind"] == "prompt"] or \
              [e["text"] for e in task.events if e["kind"] == "instruction"]
    files: dict[str, int] = {}
    for e in task.events:
        if e["kind"] == "action" and action_class(e["tool"]) == "edit" and e["target"]:
            name = os.path.basename(e["target"].rstrip("/"))
            files[name] = files.get(name, 0) + (e["lines_added"] or 0)
    replies = [e["text"] for e in task.events if e["kind"] == "reply" and e["text"]]
    ev = est["evidence"]
    active = sum(min(b["ts"] - a["ts"], 600) for a, b in zip(task.events, task.events[1:])) / 60
    lines = [
        f"Agent: {session['agent']} ({session['model'] or 'unknown model'}) · project: {task.project}",
        f"Prompts from the person: {len(prompts)} · agent active time: {active:.0f} min",
        "First prompts:",
        *[f"  {i + 1}. {clipped(p, 300)}" for i, p in enumerate(prompts[:3])],
        "Files edited (lines added): " + (", ".join(f"{n} (+{a})" for n, a in list(files.items())[:15]) or "none"),
        f"Evidence: {ev['code']} code lines, {ev['doc']} document lines, {ev['removed']} lines removed, "
        f"{ev['reads']} files read/searches, {ev['commands']} commands, {ev['web']} web lookups, "
        f"{ev['words']} words of answers in chat",
        f"Final agent reply (excerpt): {clipped(replies[-1], 400) if replies else 'none'}",
        f"Rules-based estimate: {est['h']:.1f} h ({est['category']})",
    ]
    return "\n".join(lines)


# ---------- rebuild the tasks table ----------

# Tasks are derived data: dropped and rebuilt on every scan. Their id is
# "<session>@<start second>", stable across rebuilds, so AI estimates and your
# corrections (kept in their own tables) stay attached to the same task.
SCHEMA = """
drop table if exists tasks;
create table tasks (
  id text primary key, session_id text not null, agent text not null, project text,
  title text, category text, role text, started real, ended real,
  n_prompts int, n_actions int, n_events int, h real, h_low real, h_high real, k real, k_basis text,
  y real, net real, evidence text, summary text
);
create index if not exists tasks_started on tasks(started);
"""


def rebuild(con: sqlite3.Connection, cfg: dict) -> int:
    """Recompute every task from the events. Cheap (seconds) and keeps tasks in
    step with any change to the rules or the config."""
    _git_root.cache_clear()
    _tracked.cache_clear()
    con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    sessions = {r["id"]: r for r in con.execute("select * from sessions")}
    events = defaultdict(list)
    for e in con.execute("select * from events order by ts"):
        events[e["session_id"]].append(e)

    # Subagent work belongs to the task in the parent session that launched it.
    root = lambda sid: sid if not sessions[sid]["parent_id"] or sessions[sid]["parent_id"] not in sessions else root(sessions[sid]["parent_id"])
    streams = defaultdict(list)
    for sid in sessions:
        streams[root(sid)] += events[sid]

    rows = []
    for sid, evs in streams.items():
        evs.sort(key=lambda e: e["ts"])
        s = sessions[sid]
        tasks = split_tasks(s, evs, cfg["rates"]["task_gap_minutes"])
        # Line counts recorded only per session (Cursor) are shared out by edit actions.
        listed = sum((e["lines_added"] or 0) for e in evs)
        extra_add = max(0, sum(sessions[x]["lines_added"] or 0 for x in sessions if root(x) == sid) - listed)
        extra_rem = max(0, sum(sessions[x]["lines_removed"] or 0 for x in sessions if root(x) == sid)
                        - sum((e["lines_removed"] or 0) for e in evs))
        edits = [sum(e["kind"] == "action" and action_class(e["tool"]) == "edit" for e in t.events) for t in tasks]
        total_edits = sum(edits) or 1
        for i, t in enumerate(tasks):
            share = edits[i] / total_edits if sum(edits) else (1 if i == len(tasks) - 1 else 0)
            est = estimate(t, (round(extra_add * share), round(extra_rem * share)), cfg)
            rows.append((f"{sid}@{int(t.start)}", sid, s["agent"], s["project"], est["title"], est["category"], est["role"],
                         t.start, t.events[-1]["ts"], len(t.human_prompts),
                         sum(e["kind"] == "action" for e in t.events), len(t.events),
                         est["h"], est["h_low"], est["h_high"], est["k"], est["k_basis"], est["y"], est["net"],
                         json.dumps(est["evidence"]), summary(t, est, s)))
    con.executemany(f"insert or replace into tasks values ({','.join('?' * 21)})", rows)
    con.commit()
    return len(rows)
