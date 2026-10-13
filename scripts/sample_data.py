"""Fictional sample data, for screenshots and demos. Never touches your real data.

    python scripts/sample_data.py /tmp/dfte-sample            # build the data folder
    python scripts/sample_data.py /tmp/dfte-sample --serve    # build it, then serve on port 8766

The folder gets its own config.toml and fte.db: three months of made-up tasks for
fictional customers (Acme, Northwind, Globex), with AI estimates, roles and a few
corrections already in place. Serving it skips scanning agent history, and the AI
step never runs (`auto = false`), so nothing is sent anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# title, project, agent, category, purpose, H (hours), roles [(role, share)], code lines, doc lines
TEMPLATES = [
    ("Customer portal login and sessions", "acme-portal", "claude_code", "code", "project", 14,
     [("Full-stack developer", .7), ("Front-end developer", .3)], 900, 0),
    ("Invoice export to CSV and PDF", "acme-portal", "cursor", "code", "project", 6, [("Full-stack developer", 1)], 420, 0),
    ("Admin onboarding guide", "acme-portal", "cursor", "documentation", "project", 6,
     [("Technical writer", .6), ("Presales consultant", .4)], 0, 380),
    ("Usage trends dashboard", "lumen-analytics", "claude_code", "code", "project", 10,
     [("Front-end developer", .6), ("Data / AI engineer", .4)], 700, 0),
    ("Monthly metrics pipeline", "lumen-analytics", "opencode", "code", "project", 12, [("Data / AI engineer", 1)], 650, 0),
    ("Support ticket themes analysis", "lumen-analytics", "opencode", "analysis", "project", 5, [("Business analyst", 1)], 120, 60),
    ("Workshop demo app", "northwind-demo", "claude_code", "demo prep", "project", 16,
     [("Presales consultant", .7), ("Full-stack developer", .3)], 800, 120),
    ("Renewal proposal deck", "northwind-demo", "opencode", "demo prep", "project", 5, [("Presales consultant", 1)], 0, 260),
    ("Integration solution design", "globex-integration", "opencode", "documentation", "project", 8,
     [("Solution architect", 1)], 0, 420),
    ("Rollout sizing sheet", "globex-integration", "cline", "analysis", "project", 4,
     [("Business analyst", .7), ("Presales consultant", .3)], 60, 80),
    ("Landing page redesign", "orbit-site", "cursor", "code", "project", 8,
     [("UX designer", .5), ("Front-end developer", .5)], 520, 0),
    ("How vector databases work", "(no project)", "opencode", "research", "learning", 1.5, [("Researcher / advisor", 1)], 0, 0),
    ("OAuth device flow explained", "(no project)", "claude_code", "research", "learning", 1, [("Researcher / advisor", 1)], 0, 0),
    ("Agent MCP server setup", "agents config", "claude_code", "code", "admin", 2, [("DevOps / IT admin", 1)], 90, 0),
    ("Fix shell PATH for Python", "(no project)", "cline", "code", "admin", 0.5, [("DevOps / IT admin", 1)], 10, 0),
]
PER_MONTH = {0: 8, 1: 20, 2: 30}       # tasks two months ago, last month, this month


def build(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    for f in ("fte.db", "fte.db-wal", "fte.db-shm", "config.toml"):
        (home / f).unlink(missing_ok=True)
    os.environ["DFTE_HOME"] = str(home)
    sys.path.insert(0, str(ROOT / "src"))
    from digital_fte import ai, config, db, estimate, refine
    from digital_fte.collectors.base import Event, Session

    config.ensure()
    text = config.CONFIG_PATH.read_text()
    text = text.replace("auto = true", "auto = false")                     # never call an AI service
    for agent in ("claude_code", "opencode", "cursor", "cline"):
        text = text.replace(f'[agents.{agent}]\nenabled = true\npath = "', f'[agents.{agent}]\nenabled = true\npath = "/nonexistent/')
    config.CONFIG_PATH.write_text(text)
    cfg = config.load()

    rnd = random.Random(7)
    con = db.connect(config.DB_PATH)
    now = datetime.now()
    first = now.replace(day=1, hour=10, minute=0, second=0, microsecond=0)
    plan = []                                                               # (start time, template, scale)
    prev = (first - timedelta(days=1)).replace(day=1)
    months = {0: (prev - timedelta(days=1)).replace(day=1), 1: prev, 2: first}
    for back, n in PER_MONTH.items():
        month = months[back]
        last_day = max(1, now.day - 1) if back == 2 else 27
        for i in range(n):
            tpl = TEMPLATES[(i * 7 + back * 3) % len(TEMPLATES)]
            day = rnd.randint(1, last_day)
            start = month.replace(day=day, hour=rnd.choice([9, 10, 11, 14, 15, 16]), minute=rnd.randint(0, 50))
            plan.append((start, tpl, rnd.uniform(0.7, 1.3)))

    work = home / "work"            # the files the made-up tasks "edited", so they count as kept
    for k, (start, tpl, scale) in enumerate(plan):
        title, project, agent, category, purpose, h, roles, code, docs = tpl
        for rel in [f"src/module{j}.py" for j in range(3)] + ["docs/notes.md"]:
            (work / project / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / project / rel).touch()
        t0 = start.timestamp()
        ev = [Event(ts=t0, kind="prompt", text=f"{title}: please help with this")]
        ts = t0
        for j in range(rnd.randint(2, 6)):
            ts += rnd.randint(60, 400)
            ev.append(Event(ts=ts, kind="action", tool="read", target=f"{work}/{project}/src/file{j}.ts"))
        if code:
            for j in range(3):
                ts += 300
                ev.append(Event(ts=ts, kind="action", tool="edit", target=f"{work}/{project}/src/module{j}.py",
                                lines_added=int(code * scale / 3), lines_removed=int(code * scale / 20)))
        if docs:
            ts += 300
            ev.append(Event(ts=ts, kind="action", tool="write", target=f"{work}/{project}/docs/notes.md", lines_added=int(docs * scale)))
        for j in range(rnd.randint(1, 4)):
            ts += 240
            ev.append(Event(ts=ts, kind="action", tool="bash", target="npm test"))
        ts += 120
        ev.append(Event(ts=ts, kind="reply", text="Done. " + "Here is a summary of what changed. " * rnd.randint(5, 40)))
        for j in range(rnd.randint(2, 5)):        # follow-ups: your reading, review and typing time
            ts += rnd.randint(300, 600)
            ev.append(Event(ts=ts, kind="prompt", text="Thanks. Next, please adjust this part " * rnd.randint(6, 14)))
            ts += rnd.randint(120, 400)
            ev.append(Event(ts=ts, kind="reply", text="Updated as requested. " * rnd.randint(20, 60)))
        s = Session(agent=agent, native_id=f"sample{k}", source=f"{agent}:sample{k}", title=title,
                    cwd=f"{work}/{project}", project=project, model="sample-model", events=ev)
        db.replace_source(con, s.source, agent, "v1", [s])
    con.execute("insert or replace into meta values ('last_scan', ?)", (str(time.time() - 1800),))
    estimate.rebuild(con, cfg)

    # AI estimates for every task (as if the AI step had run), from the template.
    ai.ensure_tables(con)
    by_title = {t[0]: t for t in TEMPLATES}
    tasks = con.execute("select t.id, t.n_events, t.started, s.title from tasks t join sessions s on s.id = t.session_id").fetchall()
    for tid, n_events, started, title in tasks:
        _, _, _, category, purpose, h, roles, _, _ = by_title[title]
        h = round(h * rnd.uniform(0.8, 1.2), 1)
        con.execute("insert into ai_estimates values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tid, "cortex", "cortex-fast", n_events, title, category, h * 0.7, h, h * 1.4, 0.9,
                     "Sample estimate: judged from the size and difficulty of the result.", None, time.time(), purpose,
                     json.dumps([{"role": r, "share": s} for r, s in roles])))
    con.commit()

    # A few corrections, as a person reviewing their tasks would make.
    latest = sorted(tasks, key=lambda t: -t[2])
    demo = next(t for t in latest if t[3] == "Workshop demo app")
    refine.save_correction(con, demo[0], h=20)
    portal = next(t for t in latest if t[3] == "Customer portal login and sessions")
    refine.save_correction(con, portal[0], roles=[{"role": "Full-stack developer", "share": 0.5},
                                                  {"role": "UX designer", "share": 0.5}])
    vec = next(t for t in latest if t[3] == "How vector databases work")
    refine.save_correction(con, vec[0], purpose="learning", category="research")
    con.close()
    print(f"sample data: {len(tasks)} tasks in {home}")


def serve(home: Path, port: int) -> None:
    """Serve the sample folder: no history scan, every agent shown as found."""
    import uvicorn

    from digital_fte import app as app_mod
    from digital_fte.collectors import COLLECTORS
    app_mod.scan = lambda *a, **k: []
    for col in COLLECTORS.values():
        type(col).found = lambda self, path: True
    uvicorn.run(app_mod.app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("home", type=Path)
    p.add_argument("--serve", action="store_true")
    p.add_argument("--port", type=int, default=8766)
    args = p.parse_args()
    build(args.home.expanduser().resolve())
    if args.serve:
        serve(args.home.expanduser().resolve(), args.port)
