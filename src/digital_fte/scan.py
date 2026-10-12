"""`fte scan`: read every enabled agent's history into the local database.
Only sources whose version changed since the last scan are re-read."""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import config, db, estimate
from .collectors import COLLECTORS
from .collectors.base import NO_PROJECT


@dataclass
class AgentResult:
    agent: str
    path: str
    found: bool = False
    sources: int = 0
    updated: int = 0
    gone: int = 0       # sources the agent deleted; their sessions are kept
    errors: list[str] = field(default_factory=list)


def scan(full: bool = False, only: str | None = None) -> list[AgentResult]:
    from .jobs import file_lock

    with file_lock("scan"):
        return _scan(full, only)


def _scan(full: bool, only: str | None) -> list[AgentResult]:
    cfg = config.load()
    con = db.connect(config.DB_PATH)
    results = []
    for aid, col in COLLECTORS.items():
        acfg = cfg["agents"].get(aid, {})
        if only and aid != only:
            continue
        path = Path(acfg.get("path") or col.default_path).expanduser()
        res = AgentResult(aid, str(path), found=col.found(path))
        results.append(res)
        # Disabled agents are still scanned: the switch decides what's counted,
        # so turning one on later shows its history straight away.
        if not res.found:
            continue
        known = {} if full else {r["key"]: r["version"] for r in con.execute("select key, version from sources where agent=?", (aid,))}
        live = set()
        try:
            for src in col.sources(path):
                live.add(src.key)
                res.sources += 1
                if known.get(src.key) == src.version:
                    continue
                try:
                    db.replace_source(con, src.key, aid, src.version, src.load())
                    res.updated += 1
                except Exception as e:  # one bad file shouldn't stop the scan
                    res.errors.append(f"{src.key}: {e.__class__.__name__}: {e}")
            res.gone = db.forget_missing(con, aid, live)
        except Exception as e:
            res.errors.append(f"{e.__class__.__name__}: {e}\n{traceback.format_exc(limit=3)}")
        con.commit()
    # Subagents without their own project belong to their parent's.
    con.execute(
        "update sessions set project = (select p.project from sessions p where p.id = sessions.parent_id) "
        "where project = ? and parent_id in (select id from sessions where project != ?)",
        (NO_PROJECT, NO_PROJECT),
    )
    estimate.rebuild(con, cfg)
    con.execute("insert or replace into meta values ('last_scan', ?)", (str(time.time()),))
    con.commit()
    con.close()
    return results
