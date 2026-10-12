"""The app's own SQLite database (~/.digital-fte/fte.db). Agent history is copied
in here in one common shape; the agents' own files are never written to."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .collectors.base import Session

SCHEMA = """
create table if not exists sources (
  key text primary key, agent text not null, version text not null, scanned_at real not null
);
create table if not exists sessions (
  id text primary key, agent text not null, native_id text not null, source text not null,
  parent_id text, title text, cwd text, project text, model text,
  started real, ended real, active_min real,
  n_prompts int, n_replies int, n_actions int, lines_added int, lines_removed int,
  tokens_in int, tokens_out int, cache_read int, words_out int
);
create index if not exists sessions_started on sessions(started);
create index if not exists sessions_source on sessions(source);
create table if not exists events (
  session_id text not null, seq int not null, ts real not null, kind text not null,
  tool text, target text, text text,
  lines_added int, lines_removed int, tokens_in int, tokens_out int, cache_read int,
  primary key (session_id, seq)
);
create index if not exists events_ts on events(ts);
create table if not exists meta (k text primary key, v text);
"""

IDLE_CAP_MIN = 10  # a gap longer than this between events counts as a break


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def active_minutes(times: list[float]) -> float:
    """Time someone (person or agent) was working: event gaps, each capped."""
    times = sorted(times)
    return sum(min(b - a, IDLE_CAP_MIN * 60) for a, b in zip(times, times[1:])) / 60


def replace_source(con: sqlite3.Connection, key: str, agent: str, version: str, sessions: list[Session]) -> None:
    old = [r[0] for r in con.execute("select id from sessions where source=?", (key,))]
    con.executemany("delete from events where session_id=?", [(i,) for i in old])
    con.execute("delete from sessions where source=?", (key,))
    for s in sessions:
        ev = sorted(s.events, key=lambda e: e.ts)
        sums = lambda f: sum(getattr(e, f) for e in ev)
        con.execute(
            "insert or replace into sessions values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.id, s.agent, s.native_id, s.source,
             f"{s.agent}:{s.parent_native_id}" if s.parent_native_id else None,
             s.title, s.cwd, s.project, s.model,
             ev[0].ts, ev[-1].ts, round(active_minutes([e.ts for e in ev]), 1),
             sum(e.kind == "prompt" for e in ev), sum(e.kind == "reply" for e in ev), sum(e.kind == "action" for e in ev),
             s.lines_added if s.lines_added is not None else sums("lines_added"),
             s.lines_removed if s.lines_removed is not None else sums("lines_removed"),
             sums("tokens_in"), sums("tokens_out"), sums("cache_read"),
             sum(len(e.text.split()) for e in ev if e.kind == "reply")),
        )
        con.executemany(
            "insert or replace into events values (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(s.id, i, e.ts, e.kind, e.tool, e.target, e.text, e.lines_added, e.lines_removed,
              e.tokens_in, e.tokens_out, e.cache_read) for i, e in enumerate(ev)],
        )
    con.execute("insert or replace into sources values (?,?,?,?)", (key, agent, version, time.time()))


def forget_missing(con: sqlite3.Connection, agent: str, live_keys: set[str]) -> int:
    """Drop sessions whose source no longer exists (deleted in the agent)."""
    gone = [r[0] for r in con.execute("select key from sources where agent=?", (agent,)) if r[0] not in live_keys]
    for key in gone:
        replace_source(con, key, agent, "", [])
        con.execute("delete from sources where key=?", (key,))
    return len(gone)
