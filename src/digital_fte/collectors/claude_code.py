"""Claude Code (CLI and desktop app): one JSONL file per session under
~/.claude/projects/<folder>/, subagent transcripts under <session>/subagents/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from .base import Collector, Event, Session, Source, clip, count_lines, first_line, infer_project, iso_ts

# User-role lines that are not something the person typed.
_NOT_PROMPTS = ("<local-command", "<task-notification", "<system-reminder", "Caveat:", "[Request interrupted")


def _target(name: str, inp: dict) -> str | None:
    for key in ("file_path", "notebook_path", "path", "url", "pattern", "query"):
        if inp.get(key):
            return str(inp[key])[:300]
    if inp.get("command"):
        return str(inp["command"])[:300]
    if inp.get("description"):
        return str(inp["description"])[:300]
    return None


def _lines(name: str, inp: dict) -> tuple[int, int]:
    if name == "Write":
        return count_lines(inp.get("content")), 0
    if name == "Edit":
        return count_lines(inp.get("new_string")), count_lines(inp.get("old_string"))
    if name == "MultiEdit":
        edits = inp.get("edits") or []
        return (sum(count_lines(e.get("new_string")) for e in edits),
                sum(count_lines(e.get("old_string")) for e in edits))
    if name == "NotebookEdit":
        return count_lines(inp.get("new_source")), 0
    return 0, 0


def _prompt_text(content) -> str | None:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(b.get("type") == "tool_result" for b in content if isinstance(b, dict)):
            return None
        text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    else:
        return None
    text = text.strip()
    if not text or text.startswith(_NOT_PROMPTS):
        return None
    if text.startswith("<command-name>"):
        # A slash command the person ran: keep the command itself.
        name = text.split("<command-name>", 1)[1].split("</command-name>", 1)[0]
        args = text.split("<command-args>", 1)[1].split("</command-args>", 1)[0] if "<command-args>" in text else ""
        return f"{name} {args}".strip()
    return text


def parse_file(path: Path, source_key: str, parent: str | None = None) -> Session | None:
    sid = path.stem
    s = Session(agent="claude_code", native_id=sid, source=source_key, parent_native_id=parent)
    titles: dict[str, str] = {}
    cwds: dict[str, int] = {}
    touched: list[str] = []
    seen_usage: set[str] = set()

    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            kind = d.get("type")
            if kind in ("custom-title", "summary"):
                titles[kind] = d.get("customTitle") or d.get("summary") or ""
                continue
            if kind not in ("user", "assistant") or d.get("isMeta"):
                continue
            ts = iso_ts(d.get("timestamp"))
            if ts is None:
                continue
            if d.get("cwd"):
                cwds[d["cwd"]] = cwds.get(d["cwd"], 0) + 1
            msg = d.get("message") or {}

            if kind == "user":
                text = _prompt_text(msg.get("content"))
                if text and not d.get("isSidechain"):
                    # In a subagent transcript the "user" is the parent agent, not the person.
                    s.events.append(Event(ts=ts, kind="instruction" if parent else "prompt", text=clip(text)))
                continue

            if msg.get("model") and msg["model"] != "<synthetic>":
                s.model = msg["model"]
            # One API response is split across several lines that repeat its usage.
            usage = msg.get("usage") or {}
            mid = msg.get("id")
            tok_in = tok_out = cache = 0
            if usage and mid not in seen_usage:
                seen_usage.add(mid)
                tok_in = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
                tok_out = usage.get("output_tokens", 0)
                cache = usage.get("cache_read_input_tokens", 0)
            first = True
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                ev = None
                if block.get("type") == "text" and block.get("text", "").strip():
                    ev = Event(ts=ts, kind="reply", text=clip(block["text"]))
                elif block.get("type") == "tool_use":
                    name, inp = block.get("name", "?"), block.get("input") or {}
                    added, removed = _lines(name, inp)
                    target = _target(name, inp)
                    if name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit") and target:
                        touched.append(target)
                    ev = Event(ts=ts, kind="action", tool=name, target=target, lines_added=added, lines_removed=removed)
                if ev:
                    if first:
                        ev.tokens_in, ev.tokens_out, ev.cache_read = tok_in, tok_out, cache
                        first = False
                    s.events.append(ev)
            if first and (tok_in or tok_out):
                # Thinking-only response: keep its tokens on a silent reply.
                s.events.append(Event(ts=ts, kind="reply", tokens_in=tok_in, tokens_out=tok_out, cache_read=cache))

    if not s.events:
        return None
    s.cwd = max(cwds, key=cwds.get) if cwds else None
    s.project = infer_project(s.cwd, touched, [e.target for e in s.events if e.kind == "action"])
    prompts = [e.text for e in s.events if e.kind in ("prompt", "instruction")]
    s.title = titles.get("custom-title") or titles.get("summary") or first_line(prompts[0] if prompts else "") or "Untitled session"
    if parent:
        s.title = "Subagent · " + (first_line(prompts[0]) if prompts else s.title)
    return s


class ClaudeCode(Collector):
    id = "claude_code"
    name = "Claude Code"
    default_path = "~/.claude/projects"

    def sources(self, path: Path) -> Iterator[Source]:
        for f in sorted(path.glob("*/*.jsonl")) + sorted(path.glob("*/*/subagents/*.jsonl")):
            st = f.stat()
            parent = f.parent.parent.name if f.parent.name == "subagents" else None
            key = f"claude_code:{f}"
            yield Source(key=key, version=f"{st.st_mtime_ns}:{st.st_size}",
                         load=lambda f=f, key=key, parent=parent: [s for s in [parse_file(f, key, parent)] if s])
