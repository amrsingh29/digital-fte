"""Cursor: chats ("composers") live in globalStorage/state.vscdb, table
cursorDiskKV. `composerData:<id>` holds the chat, `bubbleId:<id>:<bubble>` each
message. Opened read-only. Line counts are only kept per chat, not per edit."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .base import Collector, Event, Session, Source, clip, first_line, infer_project, iso_ts, loads, open_sqlite_ro

_FILE_TOOLS = ("read_file", "edit_file", "delete_file", "write", "search_replace")


def _args(tool: dict) -> dict:
    for key in ("rawArgs", "params"):
        v = tool.get(key)
        v = loads(v) if isinstance(v, str) else v
        if isinstance(v, dict):
            return v
    return {}


def _target(args: dict) -> str | None:
    for key in ("path", "targetFile", "relativeWorkspacePath", "command", "pattern", "globPattern", "query", "url"):
        if args.get(key):
            return str(args[key])[:300]
    return None


def _load(db: Path, cid: str, key: str, parents: dict[str, str]) -> list[Session]:
    con = open_sqlite_ro(db)
    try:
        row = con.execute("select value from cursorDiskKV where key=?", (f"composerData:{cid}",)).fetchone()
        prefix = f"bubbleId:{cid}:"
        bubbles = {k[len(prefix):]: v for k, v in con.execute(
            "select key, value from cursorDiskKV where key >= ? and key < ?", (prefix, prefix[:-1] + ";"))}
    finally:
        con.close()
    c = loads(row[0]) if row else None
    if not c:
        return []

    s = Session(agent="cursor", native_id=cid, source=key, title=c.get("name") or "",
                parent_native_id=parents.get(cid))
    s.model = ((c.get("modelConfig") or {}).get("modelName")) or None
    s.lines_added = c.get("totalLinesAdded") or 0
    s.lines_removed = c.get("totalLinesRemoved") or 0
    repos = c.get("trackedGitRepos") or []
    s.cwd = repos[0].get("repoPath") if repos else None
    touched = [u.replace("file://", "") for u in (c.get("originalFileStates") or {})]
    fallback_ts = (c.get("createdAt") or 0) / 1000

    order = [h.get("bubbleId") for h in c.get("fullConversationHeadersOnly") or []] or list(bubbles)
    for bid in order:
        b = loads(bubbles.get(bid))
        if not b:
            continue
        ts = iso_ts(b.get("createdAt")) or (b.get("startedAtMs") or 0) / 1000 or fallback_ts
        tok = b.get("tokenCount") or {}
        tok_in, tok_out = tok.get("inputTokens", 0), tok.get("outputTokens", 0)
        if b.get("type") == 1:
            if (b.get("text") or "").strip():
                kind = "instruction" if s.parent_native_id else "prompt"   # subagent chats are prompted by the parent
                s.events.append(Event(ts=ts, kind=kind, text=clip(b["text"]), tokens_in=tok_in))
            continue
        tool = b.get("toolFormerData")
        if tool and tool.get("name"):
            args = _args(tool)
            target = _target(args)
            if target and tool["name"].startswith(_FILE_TOOLS):
                touched.append(target)
            s.events.append(Event(ts=ts, kind="action", tool=tool["name"], target=target,
                                  tokens_in=tok_in, tokens_out=tok_out))
        elif (b.get("text") or "").strip():
            s.events.append(Event(ts=ts, kind="reply", text=clip(b["text"]), tokens_in=tok_in, tokens_out=tok_out))

    if not s.events:
        return []
    s.project = infer_project(s.cwd, touched, [e.target for e in s.events if e.kind == "action"])
    prompts = [e.text for e in s.events if e.kind in ("prompt", "instruction")]
    s.title = s.title or first_line(prompts[0] if prompts else "") or "Untitled chat"
    return [s]


class Cursor(Collector):
    id = "cursor"
    name = "Cursor"
    default_path = "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb"

    def sources(self, path: Path) -> Iterator[Source]:
        con = open_sqlite_ro(path)
        try:
            rows = con.execute(
                "select key, json_extract(value,'$.lastUpdatedAt'), json_extract(value,'$.subagentComposerIds') "
                "from cursorDiskKV where key like 'composerData:%'"
            ).fetchall()
        finally:
            con.close()
        parents: dict[str, str] = {}
        for k, _, subs in rows:
            for sub in loads(subs) or []:
                parents[sub] = k.split(":", 1)[1]
        for k, updated, _ in rows:
            cid = k.split(":", 1)[1]
            if cid == "empty-state-draft":
                continue
            key = f"cursor:{cid}"
            yield Source(key=key, version=str(updated), load=lambda cid=cid, key=key: _load(path, cid, key, parents))
