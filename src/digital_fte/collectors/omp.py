"""OMP (oh-my-pi coding agent): one JSONL file per session under
~/.omp/agent/sessions/<folder>/, one JSON object per line. Messages carry their
token usage; tool calls are `toolCall` blocks. Files starting with a dot are locks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from .base import Collector, Event, Session, Source, clip, count_lines, first_line, infer_project, iso_ts

_PATH_KEYS = ("path", "file_path", "url", "query", "pattern", "command")
_EDIT_FILE = re.compile(r"^\[([^\]#]+)")   # edit input starts with "[path#anchor]"
_VIRTUAL = "://"                           # xd://, mcp:// and similar are not files


def _target(inp: dict) -> str | None:
    for key in _PATH_KEYS:
        value = inp.get(key)
        if value and _VIRTUAL not in str(value):
            return str(value)[:300]
    return None


def _edit_lines(patch: str) -> tuple[int, int]:
    """OMP edits are patch text: `+` lines are added, `-` lines removed."""
    added = removed = 0
    for line in patch.splitlines():
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def parse_file(path: Path, source_key: str) -> Session | None:
    s = Session(agent="omp", native_id=path.stem, source=source_key)
    title = ""
    cwd = None
    touched: list[str] = []
    seen_responses: set[str] = set()

    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            kind = d.get("type")
            if kind == "session":
                cwd = d.get("cwd") or cwd
                continue
            if kind == "model_change":
                s.model = d.get("model") or s.model
                continue
            if kind == "title_change" or kind == "title":
                title = d.get("title") or title
                continue
            if kind != "message":
                continue

            msg = d.get("message") or {}
            role = msg.get("role")
            if role == "user":
                ts = iso_ts(d.get("timestamp"))
                text = _text_of(msg.get("content")).strip()
                if ts and text:
                    s.events.append(Event(ts=ts, kind="prompt", text=clip(text)))
                continue
            if role != "assistant":
                continue

            ts = (msg.get("timestamp") or 0) / 1000
            if not ts:
                continue
            if msg.get("model"):
                s.model = msg["model"]
            # Usage is reported once per response; drop repeats of the same response.
            usage = msg.get("usage") or {}
            rid = msg.get("responseId")
            tok_in = tok_out = cache = 0
            if usage and (rid is None or rid not in seen_responses):
                seen_responses.add(rid)
                tok_in, tok_out = usage.get("input", 0), usage.get("output", 0)
                cache = usage.get("cacheRead", 0)

            first = True
            blocks = msg.get("content") if isinstance(msg.get("content"), list) else []
            for block in blocks:
                ev = None
                if block.get("type") == "text" and block.get("text", "").strip():
                    ev = Event(ts=ts, kind="reply", text=clip(block["text"]))
                elif block.get("type") == "toolCall":
                    name = (block.get("name") or "?").lower()
                    inp = block.get("arguments") or {}
                    added = removed = 0
                    target = _target(inp)
                    if name == "edit":
                        patch = str(inp.get("input") or "")
                        added, removed = _edit_lines(patch)
                        m = _EDIT_FILE.match(patch)
                        target = m.group(1) if m else target
                    elif name == "write":
                        added = count_lines(inp.get("content"))
                    if name in ("read", "write", "edit") and target:
                        touched.append(target)
                    ev = Event(ts=ts, kind="action", tool=name, target=target,
                               lines_added=added, lines_removed=removed)
                if ev:
                    if first:
                        ev.tokens_in, ev.tokens_out, ev.cache_read = tok_in, tok_out, cache
                        first = False
                    s.events.append(ev)
            if first and (tok_in or tok_out):
                # Silent response (thinking only): keep its tokens.
                s.events.append(Event(ts=ts, kind="reply", tokens_in=tok_in, tokens_out=tok_out, cache_read=cache))

    if not s.events:
        return None
    s.cwd = cwd
    s.project = infer_project(cwd, touched, [e.target for e in s.events if e.kind == "action"])
    prompts = [e.text for e in s.events if e.kind == "prompt"]
    s.title = title or first_line(prompts[0] if prompts else "") or "Untitled session"
    return s


class Omp(Collector):
    id = "omp"
    name = "OMP"
    default_path = "~/.omp/agent/sessions"

    def sources(self, path: Path) -> Iterator[Source]:
        for f in sorted(path.glob("*/*.jsonl")):
            if f.name.startswith("."):
                continue  # lock and temp files
            st = f.stat()
            key = f"omp:{f}"
            yield Source(key=key, version=f"{st.st_mtime_ns}:{st.st_size}",
                         load=lambda f=f, key=key: [s for s in [parse_file(f, key)] if s])
