"""Cline (VS Code extension and CLI, v4+): ~/.cline/data/sessions/<id>/ holds
<id>.json (session info) and <id>.messages.json (the conversation)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from .base import Collector, Event, Session, Source, clip, count_lines, first_line, infer_project

_USER_INPUT = re.compile(r"<user_input[^>]*>(.*?)</user_input>", re.S)


def _prompt(blocks: list) -> str | None:
    if any(b.get("type") == "tool_result" for b in blocks):
        return None
    text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    m = _USER_INPUT.search(text)
    return (m.group(1).strip() if m else text) or None


def _target(inp: dict) -> str | None:
    for key in ("path", "file_path", "url"):
        if inp.get(key):
            return str(inp[key])[:300]
    for key in ("commands", "files", "queries"):
        v = inp.get(key)
        if v:
            first = v[0]
            return str(first.get("path") if isinstance(first, dict) else first)[:300]
    return None


def _load(folder: Path, key: str) -> list[Session]:
    sid = folder.name
    meta = json.loads((folder / f"{sid}.json").read_text())
    msgs = json.loads((folder / f"{sid}.messages.json").read_text()).get("messages", [])
    s = Session(agent="cline", native_id=sid, source=key, cwd=meta.get("cwd") or meta.get("workspace_root"),
                model=meta.get("model"), parent_native_id=meta.get("parent_session_id"))
    touched: list[str] = []
    for m in msgs:
        ts = (m.get("ts") or 0) / 1000
        blocks = m.get("content") if isinstance(m.get("content"), list) else []
        if m.get("role") == "user":
            text = _prompt(blocks)
            if text:
                s.events.append(Event(ts=ts, kind="prompt", text=clip(text)))
            continue
        met = m.get("metrics") or {}
        tok_in, tok_out, cache = met.get("inputTokens", 0), met.get("outputTokens", 0), met.get("cacheReadTokens", 0)
        first = True
        for b in blocks:
            ev = None
            if b.get("type") == "text" and b.get("text", "").strip():
                ev = Event(ts=ts, kind="reply", text=clip(b["text"]))
            elif b.get("type") == "tool_use":
                name, inp = b.get("name", "?"), b.get("input") or {}
                added = count_lines(inp.get("new_text") or inp.get("content")) if name == "editor" else 0
                removed = count_lines(inp.get("old_text")) if name == "editor" else 0
                target = _target(inp)
                if name in ("editor", "read_files") and target:
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
    s.title = first_line(prompts[0] if prompts else meta.get("prompt") or "") or "Untitled task"
    return [s]


class Cline(Collector):
    id = "cline"
    name = "Cline"
    default_path = "~/.cline/data/sessions"

    def sources(self, path: Path) -> Iterator[Source]:
        for folder in sorted(p for p in path.iterdir() if p.is_dir()):
            msgs = folder / f"{folder.name}.messages.json"
            if not msgs.exists():
                continue
            st = msgs.stat()
            key = f"cline:{folder.name}"
            yield Source(key=key, version=f"{st.st_mtime_ns}:{st.st_size}",
                         load=lambda folder=folder, key=key: _load(folder, key))
