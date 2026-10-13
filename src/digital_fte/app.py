import json
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import ai, config, db, jobs, refine, share
from .collectors import COLLECTORS
from .estimate import CATEGORIES_ROLE, PURPOSES
from .scan import scan

HERE = Path(__file__).parent
_scan_lock = threading.Lock()


def _ai_in_background() -> None:
    threading.Thread(target=lambda: ai.run(config.DB_PATH, config.load()), daemon=True).start()


def _scan_quietly() -> None:
    with _scan_lock:
        scan()
    if config.load()["ai"].get("auto"):
        _ai_in_background()   # new tasks get their AI estimate without anyone asking


@asynccontextmanager
async def lifespan(_app):
    # Fresh numbers on every start without blocking the first page load.
    threading.Thread(target=_scan_quietly, daemon=True).start()
    yield


app = FastAPI(title="Digital FTE", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


@app.get("/")
def index(request: Request):
    # Version stamp on static URLs so the browser never shows a stale script or stylesheet.
    v = int(max(f.stat().st_mtime for f in (HERE / "static").rglob("*") if f.is_file()))
    return templates.TemplateResponse(request, "index.html", {"v": v})


def _real() -> dict:
    cfg = config.load()
    con = db.connect(config.DB_PATH)
    try:
        rows = con.execute("select * from sessions order by started desc").fetchall()
        has_tasks = con.execute("select 1 from sqlite_master where name='tasks'").fetchone()
        task_rows, ai_summary = refine.finalize(con, cfg) if has_tasks else ([], {})
        last = con.execute("select v from meta where k='last_scan'").fetchone()
    finally:
        con.close()

    sessions = []
    for r in rows:
        if not config.path_allowed(r["cwd"], cfg):
            continue
        sessions.append({
            "id": r["id"], "agent": r["agent"], "parent": r["parent_id"], "title": r["title"],
            "project": r["project"], "model": r["model"],
            "date": datetime.fromtimestamp(r["started"]).date().isoformat(),
            "started": r["started"], "ended": r["ended"], "active_min": r["active_min"],
            "prompts": r["n_prompts"], "actions": r["n_actions"],
            "lines_added": r["lines_added"], "lines_removed": r["lines_removed"], "tokens_out": r["tokens_out"],
        })

    tasks = [{
        "id": t["id"], "session": t["session_id"], "agent": t["agent"], "project": t["project"],
        "title": t["title"], "category": t["category"], "role": t["role"], "roles": t["roles"], "roles_basis": t["roles_basis"], "model": t["model"],
        "date": datetime.fromtimestamp(t["started"]).date().isoformat(), "started": t["started"], "ended": t["ended"],
        "prompts": t["n_prompts"], "actions": t["n_actions"],
        "h": t["h"], "h_low": t["h_low"], "h_high": t["h_high"], "k": t["k"], "k_basis": t["k_basis"],
        "y": t["y"], "net": t["net"], "evidence": json.loads(t["evidence"] or "{}"),
        "rules": t["rules"], "ai": t["ai"], "learned": t["learned"], "correction": t["correction"],
        "excluded": t["excluded"], "purpose": t["purpose"], "group": t["group"], "weight": t["weight"],
    } for t in task_rows if config.path_allowed(t["cwd"], cfg)]

    services = []
    for name, svc in ai.services(cfg).items():
        ok, why = ai.status(name, svc)
        services.append({"name": name, "api": svc.get("api"), "model": svc.get("model") or "",
                         "ready": ok, "status": why, "active": name == cfg["ai"].get("use")})

    agents = []
    for aid, col in COLLECTORS.items():
        acfg = cfg["agents"].get(aid, {})
        path = acfg.get("path") or col.default_path
        mine = [s for s in sessions if s["agent"] == aid]
        found = col.found(Path(path).expanduser())
        if not found:
            status = "Not found at this path"
        elif not mine:
            status = "Found · no sessions yet"
        else:
            latest = datetime.fromtimestamp(max(s["ended"] for s in mine)).strftime("%-d %b")
            status = f"Found · {len(mine)} sessions · last active {latest}"
        agents.append({"id": aid, "name": col.name, "enabled": bool(acfg.get("enabled", False)) and found,
                       "found": found, "cost": acfg.get("cost_per_month", 0), "path": path, "status": status})

    months = sorted({s["date"][:7] for s in sessions})
    if months:  # continuous range up to this month, so quiet months show as zero
        y, m = map(int, months[0].split("-"))
        now = datetime.now()
        months = []
        while (y, m) <= (now.year, now.month):
            months.append(f"{y}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    return {
        "sample": False,
        "now": time.time(),
        "last_scan": float(last[0]) if last else None,
        "scanning": _scan_lock.locked(),
        "settings": {k: cfg["dfte"][k] for k in ("hours_per_fte_month", "human_cost_per_month", "default_keep", "review_cap_minutes")},
        "rates": cfg["rates"],
        "ai": {"use": cfg["ai"].get("use", "off"), "auto": cfg["ai"].get("auto", False), "services": services,
               **ai_summary, "progress": dict(ai.progress)},
        "categories": list(CATEGORIES_ROLE),
        "purposes": PURPOSES,
        "roles": cfg["roles"],
        "schedule": jobs.status(),
        "agents": agents,
        "months": months,
        "sessions": sessions,
        "tasks": tasks,
    }


@app.get("/api/data")
def data():
    return _real()


class AgentToggle(BaseModel):
    enabled: bool


@app.post("/api/agents/{agent_id}")
def toggle_agent(agent_id: str, body: AgentToggle):
    if agent_id not in COLLECTORS:
        raise HTTPException(404, "unknown agent")
    config.set_agent_enabled(agent_id, body.enabled)
    return {"ok": True}


@app.post("/api/scan")
def rescan():
    with _scan_lock:
        results = scan()
    return {"agents": [{"agent": r.agent, "sessions": r.sources, "updated": r.updated, "errors": r.errors[:3]}
                       for r in results]}


@app.post("/api/ai/run")
def ai_run():
    if ai.progress["running"]:
        return {"ok": True, "already": True}
    _ai_in_background()
    return {"ok": True}


class AIUse(BaseModel):
    name: str


@app.post("/api/ai/use")
def ai_use(body: AIUse):
    cfg = config.load()
    if body.name != "off" and body.name not in ai.services(cfg):
        raise HTTPException(404, "unknown AI service")
    config.set_ai_use(body.name)
    if body.name != "off" and cfg["ai"].get("auto"):
        _ai_in_background()
    return {"ok": True}


class Correction(BaseModel):
    h: float | None = None
    k: float | None = None
    category: str | None = None
    purpose: str | None = None
    excluded: bool = False


@app.post("/api/tasks/{task_id:path}/correction")
def correct(task_id: str, body: Correction):
    if body.k is not None and not 0 <= body.k <= 1:
        raise HTTPException(400, "K must be between 0 and 1")
    if body.h is not None and body.h < 0:
        raise HTTPException(400, "H can't be negative")
    if body.category and body.category not in CATEGORIES_ROLE:
        raise HTTPException(400, "unknown category")
    if body.purpose and body.purpose not in PURPOSES:
        raise HTTPException(400, "unknown purpose")
    con = db.connect(config.DB_PATH)
    try:
        refine.save_correction(con, task_id, body.h, body.k, body.category, body.excluded, body.purpose)
    finally:
        con.close()
    return {"ok": True}


@app.delete("/api/tasks/{task_id:path}/correction")
def uncorrect(task_id: str):
    con = db.connect(config.DB_PATH)
    try:
        refine.clear_correction(con, task_id)
    finally:
        con.close()
    return {"ok": True}


@app.get("/share", response_class=HTMLResponse)
def share_page(name: str | None = None):
    """The totals-only summary, as it would be shared."""
    return share.page(share.build(), name)
