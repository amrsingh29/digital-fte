"""~/.digital-fte/app.log: one troubleshooting log for the dashboard and the daily job.

What goes in: scans (per agent), AI runs (start, end, each failure and why), and
unexpected errors with their traceback. What never goes in: prompt or reply text,
file contents or keys, so the file is safe to attach to a bug report.
Rotates at 1 MB, keeping 3 old files (app.log.1 … app.log.3).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from . import config

_logger: logging.Logger | None = None


def get() -> logging.Logger:
    global _logger
    path = config.HOME_DIR / "app.log"
    if _logger is None or getattr(_logger, "_path", None) != path:   # HOME_DIR can change in tests
        log = logging.getLogger("digital_fte")
        for h in list(log.handlers):
            log.removeHandler(h)
            h.close()
        config.HOME_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(process)d] %(message)s",
                                               "%Y-%m-%d %H:%M:%S"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
        log._path = path
        _logger = log
    return _logger
