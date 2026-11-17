"""Hermes agent: one SQLite database (~/.hermes/state.db). `sessions` holds one row
per conversation with token totals; `messages` holds the turns. Tool calls are JSON
in `messages.tool_calls`. Opened read-only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from .base import Collector, Event, Session, Source, clip, count_lines, first_line, infer_project, loads, open_sqlite_ro

# Tool name -> the argument that says what it touched.
_TARGET_KEYS = {
    "read_file": ("path",), "write_file": ("path",), "patch": ("path",),
    "search_files": ("pattern", "path"), "terminal": ("command",),
    "web_search": ("query",), "web_extract": ("url", "urls"), "skill_view": ("name",),
}
_FILE_TOOLS = {"read_file", "write_file", "patch"}


def _calls(tool_calls) -> list[tuple[str, dict]]:
    """Unpack one assistant message's tool calls. `tool_call` is a wrapper that
    carries a list of real calls in its arguments."""
    out: list[tuple[str, dict]] = []
    for call in loads(tool_calls) or []:
        fn = call.get("function") or {}
        name = (fn.get("name") or "?").lower()
        args = loads(fn.get("arguments")) or {}
        if name == "tool_call":
            for inner in args.get("calls") or []:
                out.append(((inner.get("name") or "?").lower(), inner.get("arguments") or {}))
        else:
            out.append((name, args))
    return out


def _target(name: str, args: dict) -> str | None:
    for key in _TARGET_KEYS.get(name, ("path", "file_path", "command", "query", "url")):
        value = args.get(key)
        if value:
            return str(value)[:300]
    return None


def _lines(name: str, args: dict) -> tuple[int, int]:
    if name == "write_file":
        return count_lines(args.get("content")), 0
    if name == "patch":
        return count_lines(args.get("new_string")), count_lines(args.get("old_string"))
    return 0, 0


def _split(total: int, parts: int) -> list[int]:
    """Share a session total across its assistant turns; the remainder goes first."""
    if parts <= 0:
        return []
    base, extra = divmod(total or 0, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def _load_session(db: Path, row: tuple, key: str) -> list[Session]:
    sid, parent, cwd, title, model, tin, tout, cache = row
    s = Session(agent="hermes", native_id=sid, source=key, cwd=cwd, model=model,
                parent_native_id=parent)
    touched: list[str] = []

    con = open_sqlite_ro(db)
    try:
        rows = con.execute(
            "select role, content, tool_calls, timestamp from messages "
            "where session_id=? and active=1 order by id", (sid,)).fetchall()
    finally:
        con.close()

    turns = sum(1 for role, *_ in rows if role == "assistant")
    shares_in, shares_out = _split(tin, turns), _split(tout, turns)
    shares_cache = _split(cache, turns)
    n = 0

    for role, content, tool_calls, ts in rows:
        if not ts:
            continue
        if role == "user":
            if (content or "").strip():
                s.events.append(Event(ts=ts, kind="prompt", text=clip(content)))
            continue
        if role != "assistant":
            continue  # tool results are not the person's work

        tok_in, tok_out, cache_read = shares_in[n], shares_out[n], shares_cache[n]
        n += 1
        first = True
        events: list[Event] = []
        if (content or "").strip():
            events.append(Event(ts=ts, kind="reply", text=clip(content)))
        for name, args in _calls(tool_calls):
            added, removed = _lines(name, args)
            target = _target(name, args)
            if name in _FILE_TOOLS and target:
                touched.append(target)
            events.append(Event(ts=ts, kind="action", tool=name, target=target,
                                lines_added=added, lines_removed=removed))
        if not events:
            events.append(Event(ts=ts, kind="reply"))  # silent turn: keep its tokens
        for ev in events:
            if first:
                ev.tokens_in, ev.tokens_out, ev.cache_read = tok_in, tok_out, cache_read
                first = False
            s.events.append(ev)

    if not s.events:
        return []
    s.project = infer_project(cwd, touched, [e.target for e in s.events if e.kind == "action"])
    prompts = [e.text for e in s.events if e.kind == "prompt"]
    s.title = title or first_line(prompts[0] if prompts else "") or "Untitled session"
    if parent:
        s.title = "Subagent · " + s.title
    return [s]


class Hermes(Collector):
    id = "hermes"
    name = "Hermes"
    default_path = "~/.hermes/state.db"

    def found(self, path: Path) -> bool:
        return path.is_file()

    def sources(self, path: Path) -> Iterator[Source]:
        con = open_sqlite_ro(path)
        try:
            rows = con.execute(
                "select id, parent_session_id, cwd, title, model, input_tokens, output_tokens, "
                "cache_read_tokens, transcript_revision, message_count, last_activity_at from sessions").fetchall()
        finally:
            con.close()
        for row in rows:
            sid, rev, count, last = row[0], row[8], row[9], row[10]
            key = f"hermes:{sid}"
            # Re-read a session only when its transcript or totals have changed.
            yield Source(key=key, version=f"{rev}:{count}:{last}",
                         load=lambda row=row[:8], key=key, db=path: _load_session(db, row, key))
