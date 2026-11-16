"""The MCP server's tools, with no MCP library needed (so they can be tested and dry-run on their own).

Three rules hold the privacy line:
  1. The database is opened read-only. Nothing here can write to it.
  2. Every result goes through `shape`, which keeps only the fields the policy allows.
     A field that is not named in a spec is dropped, so a new field is private until someone
     decides which level it belongs to.
  3. Each tool refuses to run unless its level is switched on in config.toml.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config, mcp_policy, share
from .mcp_policy import Policy

REQUIRED_TABLES = ("sessions", "tasks", "ai_estimates", "corrections")
MAX_ROWS = 200
DEFAULT_ROWS = 50
AUDIT_LOG = "mcp-audit.log"

# ---------- what each tool may return, by level ----------
# {field: (level, spec of the records inside it, or None)}. "meta" fields are always allowed.

_EVIDENCE = {k: ("task_details", None) for k in ("code", "doc", "removed", "reads", "commands", "web", "edits", "words",
                                                   "prompts")}

SUMMARY_SPEC = {
    "ok": ("meta", None),
    "current_month": ("totals", None),
    "hours_per_fte_month": ("totals", None),
    "months": ("totals", {
        "month": ("totals", None), "tasks": ("totals", None), "net_hours": ("totals", None),
        "dfte": ("totals", None), "dfte_low": ("totals", None), "dfte_high": ("totals", None),
        "dfte_rules_only": ("totals", None), "leverage": ("totals", None), "keep_rate": ("totals", None),
        "agent_cost": ("totals", None), "cost_per_dfte": ("totals", None),
        "team": ("totals", {"role": ("totals", None), "dfte": ("totals", None)}),
    }),
    # These four are always for current_month, whichever month was asked for (months[].team has each month's team).
    "current_month_by_agent": ("totals", {"agent": ("totals", None), "dfte": ("totals", None)}),
    "current_month_by_kind": ("totals", {"kind": ("totals", None), "dfte": ("totals", None)}),
    "current_month_team": ("totals", {"role": ("totals", None), "dfte": ("totals", None),
                                      "monthly_cost": ("totals", None)}),
    "current_month_team_value": ("totals", None),
    "current_month_cost_per_person": ("totals", None),
}

TASK_SPEC = {
    "id": ("task_details", None), "agent": ("task_details", None), "category": ("task_details", None),
    "purpose": ("task_details", None),
    "roles": ("task_details", {"role": ("task_details", None), "share": ("task_details", None)}),
    "started": ("task_details", None), "ended": ("task_details", None),
    "prompts": ("task_details", None), "actions": ("task_details", None), "events": ("task_details", None),
    "h": ("task_details", None), "h_low": ("task_details", None), "h_high": ("task_details", None),
    "k": ("task_details", None), "k_basis": ("task_details", None), "y": ("task_details", None),
    "net_hours": ("task_details", None), "excluded": ("task_details", None), "corrected": ("task_details", None),
    "ai_reviewed": ("task_details", None), "evidence": ("task_details", _EVIDENCE),
    "why": ("task_details", None),                   # review queue: fixed phrases, never user text
    "title": ("names", None), "project": ("names", None), "reason": ("names", None),
    "summary": ("content", None),
}

TASKS_SPEC = {
    "ok": ("meta", None), "count": ("meta", None), "total": ("meta", None), "note": ("meta", None),
    "tasks": ("task_details", TASK_SPEC),
}

TOOL_LEVEL = {"dfte_summary": "totals", "dfte_tasks": "task_details", "dfte_review_queue": "task_details"}


class NotReady(Exception):
    """The database is missing or not set up yet."""


def shape(value, spec, policy: Policy):
    """Keep only what `spec` names and the policy allows, at every depth."""
    if isinstance(value, list):
        return [shape(v, spec, policy) for v in value]
    if not isinstance(value, dict) or spec is None:
        return value
    out = {}
    for key, (level, sub) in spec.items():
        if key in value and (level == "meta" or policy.allows(level)):
            out[key] = shape(value[key], sub, policy) if sub is not None else value[key]
    return out


# ---------- database ----------

def open_ro(path: Path | None = None) -> sqlite3.Connection:
    """Read-only connection: any attempt to write fails inside SQLite itself."""
    path = path or config.DB_PATH
    if not path.exists():
        raise NotReady("There is no database yet. Run `fte scan` first.")
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)   # 5 s if a scan is writing
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only = on")
    have = {r[0] for r in con.execute("select name from sqlite_master where type = 'table'")}
    if missing := [t for t in REQUIRED_TABLES if t not in have]:
        con.close()
        raise NotReady(f"The database is not set up yet (missing {', '.join(missing)}). "
                       "Run `fte scan`, then `fte estimate --ai`, first.")
    return con


# ---------- the tools ----------

def _month_ok(month: str | None) -> str | None:
    if month is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("month must look like 2026-10")
    return month


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else None


def summary(con: sqlite3.Connection, cfg: dict, month: str | None = None) -> dict:
    s = share.build(cfg, con)
    rows = [{
        "month": r["month"], "tasks": r["tasks"], "net_hours": round(r["net"], 2), "dfte": round(r["dfte"], 3),
        "dfte_low": round(r["lo"], 3), "dfte_high": round(r["hi"], 3), "dfte_rules_only": round(r["rules"], 3),
        "leverage": None if r["leverage"] is None else round(r["leverage"], 1),
        "keep_rate": None if r["keep"] is None else round(r["keep"], 3),
        "agent_cost": round(r["cost"], 2),
        "cost_per_dfte": None if r["cost_per_dfte"] is None else round(r["cost_per_dfte"], 2),
        "team": [{"role": n, "dfte": round(v, 3)} for n, v in r["team"].items()],
    } for r in s["months"]]
    if month is not None:
        rows = [r for r in rows if r["month"] == month]
        if not rows:
            raise ValueError(f"no data for {month}")
    return {
        "ok": True, "current_month": s["current"], "hours_per_fte_month": s["hpf"], "months": rows,
        "current_month_by_agent": [{"agent": n, "dfte": round(v, 3)} for n, v in s["agents"]],
        "current_month_by_kind": [{"kind": html.unescape(n), "dfte": round(v, 3)} for n, v in s["purposes"]],
        "current_month_team": [{"role": n, "dfte": round(v, 3), "monthly_cost": c}
                               for n, v, c in s["team"]["roles"]],
        "current_month_team_value": round(s["team"]["value"], 2),
        "current_month_cost_per_person": round(s["team"]["per_person"], 2),
    }


def _task_row(t: dict) -> dict:
    """Every field a task could show. `shape` decides which of them leave the server."""
    ai = t.get("ai") or {}
    ev = json.loads(t["evidence"]) if t.get("evidence") else {}
    return {
        "id": t["id"], "agent": t["agent"], "category": t["category"], "purpose": t["purpose"],
        "roles": [{"role": r["role"], "share": r["share"]} for r in t.get("roles") or []],
        "started": _iso(t["started"]), "ended": _iso(t["ended"]),
        "prompts": t["n_prompts"], "actions": t["n_actions"], "events": t["n_events"],
        "h": round(t["h"], 2), "h_low": round(t["h_low"], 2), "h_high": round(t["h_high"], 2),
        "k": round(t["k"], 2), "k_basis": t["k_basis"], "y": round(t["y"], 2), "net_hours": round(t["net"], 2),
        "excluded": bool(t["excluded"]), "corrected": t.get("correction") is not None, "ai_reviewed": bool(ai),
        "evidence": ev,
        "title": t["title"], "project": t["project"], "reason": ai.get("reason"),
        "summary": t.get("summary"),
    }


def _visible(con, cfg, month, agent, category, purpose) -> list[dict]:
    tasks, _, _ = share.visible_tasks(cfg, con)
    out = []
    for t in tasks:
        if month and datetime.fromtimestamp(t["started"]).strftime("%Y-%m") != month:
            continue
        if (agent and t["agent"] != agent) or (category and t["category"] != category) or \
                (purpose and t["purpose"] != purpose):
            continue
        out.append(t)
    return out


def _limit(limit: int | None) -> int:
    return max(1, min(int(limit or DEFAULT_ROWS), MAX_ROWS))


def tasks(con, cfg, month=None, agent=None, category=None, purpose=None, limit=None) -> dict:
    found = _visible(con, cfg, month, agent, category, purpose)
    n = _limit(limit)
    return {"ok": True, "count": min(n, len(found)), "total": len(found),
            "note": "Newest first." + (f" Showing the first {n}; narrow with month, agent, category or purpose."
                                       if len(found) > n else ""),
            "tasks": [_task_row(t) for t in found[:n]]}


def _why(t: dict) -> list[str]:
    ai = t.get("ai")
    why = []
    if not ai:
        why.append("no AI estimate yet")
    elif ai.get("clamped"):
        why.append("the AI estimate was held inside the guard rails")
    if t.get("roles_basis") not in ("ai", "you"):
        why.append("roles are a first guess from the category")
    return why


def review_queue(con, cfg, month=None, agent=None, category=None, purpose=None, limit=None) -> dict:
    """Tasks worth a second look: not corrected or excluded, and the AI step is missing, hit the guard
    rails, or left the roles as a first guess. Biggest net hours first."""
    found = [t for t in _visible(con, cfg, month, agent, category, purpose)
             if not t["excluded"] and t.get("correction") is None and _why(t)]
    found.sort(key=lambda t: t["net"], reverse=True)
    n = _limit(limit)
    return {"ok": True, "count": min(n, len(found)), "total": len(found),
            "note": "Biggest net hours first." + (f" Showing the first {n}." if len(found) > n else ""),
            "tasks": [{**_task_row(t), "why": _why(t)} for t in found[:n]]}


TOOLS = {"dfte_summary": (summary, SUMMARY_SPEC), "dfte_tasks": (tasks, TASKS_SPEC),
         "dfte_review_queue": (review_queue, TASKS_SPEC)}


# ---------- one entry point for the server and for --dry-run ----------

def _fields(result: dict) -> list[str]:
    """Names of the fields in a result, never their values (for the audit log)."""
    names: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                names.add(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(result)
    return sorted(names)


def _audit(tool: str, args: dict, policy: Policy, result: dict) -> None:
    line = {"at": datetime.now().isoformat(timespec="seconds"), "tool": tool,
            "args": {k: v for k, v in args.items() if v is not None}, "levels_on": policy.on,
            "ok": bool(result.get("ok")), "rows": len(result.get("tasks", result.get("months", []))),
            "fields": _fields(result)}
    try:
        config.HOME_DIR.mkdir(parents=True, exist_ok=True)
        with (config.HOME_DIR / AUDIT_LOG).open("a") as f:
            f.write(json.dumps(line) + "\n")
    except OSError:
        pass                                    # a log problem must never break the tool


def run(tool: str, args: dict | None = None) -> dict:
    """Run one tool for the owner profile and return what the policy allows. Never raises."""
    args = {k: v for k, v in (args or {}).items() if v not in (None, "")}
    policy = mcp_policy.load()
    result: dict
    try:
        fn, spec = TOOLS[tool]
        level = TOOL_LEVEL[tool]
        if not policy.allows(level):
            result = {"ok": False, "error": f"Sharing '{level}' is switched off. To allow it, set {level} = true "
                                            f"under [mcp.profiles.me] in {config.CONFIG_PATH}."}
        else:
            _month_ok(args.get("month"))
            con = open_ro()
            try:
                result = shape(fn(con, config.load(), **args), spec, policy)
            finally:
                con.close()
    except NotReady as e:
        result = {"ok": False, "error": str(e)}
    except (ValueError, TypeError) as e:
        result = {"ok": False, "error": str(e)}
    except sqlite3.OperationalError as e:
        result = {"ok": False, "error": ("The database is busy (a scan may be running). Try again in a moment."
                                         if "locked" in str(e) else f"Could not read the database: {e}")}
    except Exception as e:                      # never crash the agent's session
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    _audit(tool, args, policy, result)
    return result
