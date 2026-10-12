"""Final per-task numbers: rules (Phase 3) → AI estimate (Phase 4) → your corrections.

    1. Start from the rules-based H, K, Y.
    2. If the active AI service estimated the task, use its H, kept within
       [0.25x, 2x] of the rules H so one odd answer can't swing the total.
       Its K replaces the rules K only when no edited files could be checked.
    3. Learn from your corrections: per category, the typical ratio between the H you
       set and the H we had; applied to uncorrected tasks once there are 3+ corrections.
    4. Your correction of a task always wins. "Not real work" sets its net hours to 0.
    5. Purpose (project work / general & learning / setup & admin): yours, else the
       AI's, else the rules'. Net hours are multiplied by that purpose's weight
       ([purposes] in config; 1.0 = count fully).
"""

from __future__ import annotations

import sqlite3
import statistics
import time

from . import ai
from .collectors.base import NO_PROJECT
from .estimate import PURPOSES

GUARD_LOW, GUARD_HIGH = 0.25, 2.0
LEARN_MIN = 3

CORRECTIONS = """
create table if not exists corrections (
  task_id text primary key, h real, k real, category text, excluded int default 0, updated_at real, purpose text
);
"""


def ensure_tables(con: sqlite3.Connection) -> None:
    ai.ensure_tables(con)
    con.executescript(CORRECTIONS)
    if "purpose" not in {r[1] for r in con.execute("pragma table_info(corrections)")}:
        con.execute("alter table corrections add column purpose text")


def save_correction(con: sqlite3.Connection, task_id: str, h=None, k=None, category=None, excluded=False,
                    purpose=None) -> None:
    ensure_tables(con)
    con.execute("insert or replace into corrections (task_id, h, k, category, excluded, updated_at, purpose) "
                "values (?,?,?,?,?,?,?)", (task_id, h, k, category, int(bool(excluded)), time.time(), purpose))
    con.commit()


def clear_correction(con: sqlite3.Connection, task_id: str) -> None:
    ensure_tables(con)
    con.execute("delete from corrections where task_id = ?", (task_id,))
    con.commit()


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def finalize(con: sqlite3.Connection, cfg: dict) -> tuple[list[dict], dict]:
    """Every task with its rules, AI and final numbers, plus a summary of the AI step."""
    con.row_factory = sqlite3.Row
    ensure_tables(con)
    weights = {"project": 1.0, "learning": cfg.get("purposes", {}).get("learning_weight", 1.0),
               "admin": cfg.get("purposes", {}).get("admin_weight", 1.0)}
    act = ai.active(cfg)
    name, model = (act[0], act[1].get("model")) if act else (None, None)
    est = {r["task_id"]: r for r in con.execute(
        "select * from ai_estimates where service = ? and model = ? and error is null", (name, model))} if act else {}
    corr = {r["task_id"]: r for r in con.execute("select * from corrections")}
    tasks = [dict(r) for r in con.execute(
        "select t.*, s.cwd, s.model from tasks t join sessions s on s.id = t.session_id order by t.started desc")]

    # Pass 1: rules → AI, before learning.
    for t in tasks:
        e = est.get(t["id"])
        t["purpose"] = t.get("purpose") or "project"
        t["rules"] = {"h": t["h"], "h_low": t["h_low"], "h_high": t["h_high"], "k": t["k"], "net": t["net"],
                      "category": t["category"], "title": t["title"], "purpose": t["purpose"]}
        t["ai"] = None
        base_h, lo, hi = t["h"], t["h_low"], t["h_high"]
        if e:
            g_lo, g_hi = max(0.1, t["h"] * GUARD_LOW), max(0.2, t["h"] * GUARD_HIGH)
            base_h = _clamp(e["h"], g_lo, g_hi)
            lo, hi = _clamp(e["h_low"], g_lo, base_h), _clamp(e["h_high"], base_h, g_hi)
            t["ai"] = {"title": e["title"], "category": e["category"], "h": e["h"], "k": e["k"],
                       "reason": e["reason"], "clamped": abs(base_h - e["h"]) > 0.01}
            if e["title"]:
                t["title"] = e["title"]
            if e["category"]:
                t["category"] = e["category"]
            if e["purpose"]:
                t["purpose"] = e["purpose"]
            if t["k_basis"].startswith("default") and e["k"] is not None:
                t["k"] = round(_clamp(e["k"], 0.2, 1.0), 2)
                t["k_basis"] = f"{name}: judged from the conversation"
        t["base_h"], t["h_low"], t["h_high"] = base_h, lo, hi

    # Pass 2: learn a per-category factor from corrected H values.
    ratios: dict[str, list[float]] = {}
    for t in tasks:
        c = corr.get(t["id"])
        if c and c["h"] is not None and t["base_h"] > 0:
            ratios.setdefault(c["category"] or t["category"], []).append(c["h"] / t["base_h"])
    learned = {cat: round(_clamp(statistics.median(r), 0.33, 3.0), 2) for cat, r in ratios.items() if len(r) >= LEARN_MIN}

    # Pass 3: corrections and final net hours.
    for t in tasks:
        c = corr.get(t["id"])
        f = learned.get(t["category"], 1.0)
        h = t["base_h"] * f
        t["h_low"], t["h_high"] = t["h_low"] * f, t["h_high"] * f
        t["learned"] = f if f != 1.0 else None
        t["correction"] = None
        t["excluded"] = False
        if c:
            t["correction"] = {"h": c["h"], "k": c["k"], "category": c["category"], "excluded": bool(c["excluded"])}
            if c["category"]:
                t["category"] = c["category"]
            if c["purpose"]:
                t["correction"]["purpose"] = c["purpose"]
                t["purpose"] = c["purpose"]
            if c["h"] is not None:
                scale = c["h"] / h if h else 1
                h, t["h_low"], t["h_high"] = c["h"], t["h_low"] * scale, t["h_high"] * scale
            if c["k"] is not None:
                t["k"], t["k_basis"] = c["k"], "set by you"
            t["excluded"] = bool(c["excluded"])
        t["h"] = round(h, 2)
        t["h_low"], t["h_high"] = round(t["h_low"], 2), round(t["h_high"], 2)
        t["weight"] = weights.get(t["purpose"], 1.0)
        t["net"] = 0.0 if t["excluded"] else round(max(0.0, t["h"] * t["k"] - t["y"]) * t["weight"], 2)
        t["rules"]["net"] = round(t["rules"]["net"] * weights.get(t["rules"]["purpose"], 1.0), 2)
        # What the "By project" view groups by: the project for project work, the purpose otherwise.
        t["group"] = (t["project"] if t["project"] != NO_PROJECT else "Unassigned") if t["purpose"] == "project" \
            else PURPOSES[t["purpose"]]

    summary = {"service": name, "model": model, "estimated": sum(1 for t in tasks if t["ai"]),
               "corrected": len(corr), "learned": learned,
               "failed": con.execute("select count(*) from ai_estimates where service = ? and model = ? and error is not null",
                                     (name, model)).fetchone()[0] if act else 0}
    return tasks, summary
