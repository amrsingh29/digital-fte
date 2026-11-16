"""Which kinds of data the MCP server may return, read from `[mcp.profiles.me]` in config.toml.

Four switches, matching the levels in docs/data-and-privacy.md. Only `totals` is on by
default; a missing, mistyped or non-boolean setting counts as off, so a typo can only
share less, never more.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

from . import config

LEVELS = ("totals", "task_details", "names", "content")
DEFAULTS = {"totals": True, "task_details": False, "names": False, "content": False}


@dataclass(frozen=True)
class Policy:
    totals: bool = True
    task_details: bool = False
    names: bool = False
    content: bool = False

    def allows(self, level: str) -> bool:
        return level in LEVELS and getattr(self, level) is True

    @property
    def on(self) -> list[str]:
        return [lv for lv in LEVELS if self.allows(lv)]


NOTHING = Policy(totals=False)


def load() -> Policy:
    """Read the policy fresh from config.toml (so editing the file takes effect on the next call)."""
    try:
        raw = tomllib.loads(config.CONFIG_PATH.read_text())
    except FileNotFoundError:
        return Policy()
    except (OSError, tomllib.TOMLDecodeError):
        return NOTHING          # a file we cannot read shares nothing
    mcp = raw.get("mcp")
    profiles = mcp.get("profiles") if isinstance(mcp, dict) else None
    section = profiles.get("me") if isinstance(profiles, dict) else None
    if not isinstance(section, dict):
        return Policy()
    return Policy(**{lv: section.get(lv, DEFAULTS[lv]) is True for lv in LEVELS})
