"""opencode: one SQLite database. Sessions in `session_v2`, messages in
`session_message` (JSON in `data`). Opened read-only."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .base import Collector, Event, Session, Source, clip, count_lines, first_line, infer_project, loads, open_sqlite_ro

_EDIT_TOOLS = {"edit", "write", "patch", "multiedit"}


def _target(inp: dict) -> str | None:
    for key in ("path", "filePath", "file_path", "url", "pattern", "query", "command", "code"):
        if inp.get(key):
            return str(inp[key])[:300]
    return None


def _load_session(db: Path, row: tuple, key: str) -> list[Session]:
    sid, directory, title, parent, model = row
    s = Session(agent="opencode", native_id=sid, source=key, title=title or "", cwd=directory,
                parent_native_id=parent)
    m = loads(model) or {}
    s.model = m.get("id") if isinstance(m, dict) else None
    touched: list[str] = []
    con = open_sqlite_ro(db)
    try:
        rows = con.execute("select type, data from session_message where session_id=? order by seq", (sid,)).fetchall()
    finally:
        con.close()

    for kind, raw in rows:
        d = loads(raw) or {}
        ts = ((d.get("time") or {}).get("created") or 0) / 1000
        if not ts:
            continue
        if kind == "user":
            if d.get("text", "").strip():
                s.events.append(Event(ts=ts, kind="prompt", text=clip(d["text"])))
            continue
        if kind != "assistant":
            continue  # idle / compaction / synthetic / system bookkeeping
        mm = d.get("model") or {}
        if isinstance(mm, dict) and mm.get("id"):
            s.model = mm["id"]
        tok = d.get("tokens") or {}
        tok_in, tok_out = tok.get("input", 0), tok.get("output", 0) + tok.get("reasoning", 0)
        cache = (tok.get("cache") or {}).get("read", 0)
        first = True
        for part in d.get("content") or []:
            ev = None
            if part.get("type") == "text" and part.get("text", "").strip():
                ev = Event(ts=ts, kind="reply", text=clip(part["text"]))
            elif part.get("type") == "tool":
                name = part.get("name", "?")
                inp = (part.get("state") or {}).get("input") or {}
                added = removed = 0
                if name in _EDIT_TOOLS:
                    added = count_lines(inp.get("newString") or inp.get("content"))
                    removed = count_lines(inp.get("oldString"))
                target = _target(inp)
                if name in _EDIT_TOOLS | {"read"} and target:
                    touched.append(target)
                ev = Event(ts=ts, kind="action", tool=name, target=target, lines_added=added, lines_removed=removed)
            if ev:
                if first:
                    ev.tokens_in, ev.tokens_out, ev.cache_read = tok_in, tok_out, cache
                    first = False
                s.events.append(ev)
        if first and (tok_in or tok_out):
            s.events.append(Event(ts=ts, kind="reply", tokens_in=tok_in, tokens_out=tok_out, cache_read=cache))

    if not s.events:
        return []
    s.project = infer_project(s.cwd, touched, [e.target for e in s.events if e.kind == "action"])
    prompts = [e.text for e in s.events if e.kind == "prompt"]
    s.title = s.title or first_line(prompts[0] if prompts else "") or "Untitled session"
    return [s]


class Opencode(Collector):
    id = "opencode"
    name = "opencode"
    default_path = "~/.local/share/opencode/opencode.db"

    def sources(self, path: Path) -> Iterator[Source]:
        con = open_sqlite_ro(path)
        try:
            rows = con.execute(
                "select id, directory, title, parent_id, model, time_updated from session_v2"
            ).fetchall()
        finally:
            con.close()
        for *row, updated in rows:
            key = f"opencode:{row[0]}"
            yield Source(key=key, version=str(updated),
                         load=lambda row=tuple(row), key=key: _load_session(path, row, key))
