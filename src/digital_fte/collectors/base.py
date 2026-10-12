"""Common shape every agent reader produces, plus small shared helpers."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

HOME = str(Path.home())
TEXT_LIMIT = 4000  # prompt/reply text kept per event (local only; used by later phases)


@dataclass
class Event:
    ts: float                    # epoch seconds
    kind: str                    # "prompt" (the person) | "instruction" (a parent agent) | "reply" | "action"
    text: str = ""
    tool: str | None = None
    target: str | None = None    # file path, command or query the action touched
    lines_added: int = 0
    lines_removed: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0


@dataclass
class Session:
    agent: str
    native_id: str
    source: str                  # key of the Source it came from
    title: str = ""
    cwd: str | None = None
    project: str = ""
    model: str | None = None
    parent_native_id: str | None = None
    events: list[Event] = field(default_factory=list)
    # Some agents only record line counts per session (Cursor); None = sum the events.
    lines_added: int | None = None
    lines_removed: int | None = None

    @property
    def id(self) -> str:
        return f"{self.agent}:{self.native_id}"


@dataclass
class Source:
    """One unit of history that is re-read only when its version changes."""
    key: str
    version: str
    load: Callable[[], list[Session]]


class Collector:
    id = ""
    name = ""
    default_path = ""

    def found(self, path: Path) -> bool:
        return path.exists()

    def sources(self, path: Path) -> Iterator[Source]:
        raise NotImplementedError


# ---------- helpers ----------

def iso_ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def clip(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT] + "…"


def count_lines(text: str | None) -> int:
    return len(text.splitlines()) if text else 0


def first_line(text: str, limit: int = 90) -> str:
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    line = line.lstrip("#").strip()
    return line if len(line) <= limit else line[: limit - 1] + "…"


def open_sqlite_ro(path: Path) -> sqlite3.Connection:
    """Read-only, never writes to the agent's database. Falls back to an immutable
    snapshot read if the WAL can't be opened read-only."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.execute("select 1").fetchone()
        return con
    except sqlite3.Error:
        return sqlite3.connect(f"file:{path}?immutable=1", uri=True)


def loads(s: str | bytes | None):
    try:
        return json.loads(s) if s else None
    except (ValueError, TypeError):
        return None


# Folder names that group projects rather than being one.
_CONTAINERS = {
    "developer", "my-projects", "projects", "my-local-workspace", "library", "cloudstorage",
    "myfolder", "workspace", "documents", "desktop", "downloads", "code", "src", "repos",
    "private", "tmp", "var", "folders", "users", "helix", "application", "application support",
}
_DOT_PARENTS = {".config", ".local", ".cache", "share"}


def project_of(path: str | None) -> str | None:
    """Best-guess project name for a file or folder path."""
    if not path:
        return None
    p = os.path.expanduser(path)
    if p.startswith("file://"):
        p = p[7:]
    if p.startswith(("/private/", "/var/", "/tmp/", "/opt/", "/usr/")):
        return None  # system and temp folders are never a project
    if p.startswith(HOME):
        p = p[len(HOME):]
    parts = [x.rstrip("\\") for x in p.split("/") if x.rstrip("\\")]
    if any(x.startswith("claude-") or x == "scratchpad" for x in parts[:4]):
        return None  # agent scratch space, not a project
    for i, part in enumerate(parts):
        low = part.lower()
        if low in _CONTAINERS or low.startswith("onedrive"):
            continue
        if low == "project":
            # OneDrive …/Project/<Country>/<Customer>/…; a file right under <Country> means no customer.
            if i + 3 < len(parts) or (i + 2 < len(parts) and "." not in parts[i + 2]):
                return f"{parts[i + 2]} ({parts[i + 1]})"
            if i + 1 < len(parts) and "." not in parts[i + 1]:
                return parts[i + 1]
            continue
        if part.startswith(".") or low in _DOT_PARENTS:
            if low in _DOT_PARENTS and i + 1 < len(parts):
                continue  # ~/.config/<tool>, ~/.local/share/<tool>: name the tool
            return part.lstrip(".") + " config"
        return part if i + 1 < len(parts) or "." not in part else None
    return None


NO_PROJECT = "(no project)"
_PATH_IN_TEXT = re.compile(r"(?:~|/Users/[^/\s]+)/[^\s'\"`;|&)]+")


def infer_project(cwd: str | None, touched: list[str], mentioned: list[str] = ()) -> str:
    """Use the working folder unless it's the home folder or a scratch dir; then
    the project most touched files belong to; then paths mentioned in commands."""
    own = project_of(cwd) if cwd and os.path.expanduser(cwd) != HOME else None
    if own:
        return own
    for paths in (touched, [m for t in mentioned for m in _PATH_IN_TEXT.findall(t or "")]):
        votes = Counter(p for p in map(project_of, paths) if p)
        if votes:
            return votes.most_common(1)[0][0]
    return NO_PROJECT
