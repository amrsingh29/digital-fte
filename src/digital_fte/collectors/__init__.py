"""One reader per coding agent. Adding an agent = one new module + one line here."""

from .base import Collector
from .claude_code import ClaudeCode
from .cline import Cline
from .cursor import Cursor
from .opencode import Opencode

COLLECTORS: dict[str, Collector] = {c.id: c for c in (ClaudeCode(), Opencode(), Cursor(), Cline())}
