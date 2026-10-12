"""Phase 5: automatic refresh.

`fte refresh` = scan + AI estimates, skipped if the last scan is recent.
`fte schedule install` registers it with macOS launchd so it runs:
  - every day at a set time;
  - on wake, if the Mac was asleep at that time (launchd runs a missed
    calendar job once when the Mac wakes, however many days were missed);
  - at login, if the Mac was shut down; --if-older-than stops this from
    re-running when you log in again the same day.
Missing a day loses nothing: the next run reads everything since the last one.
"""

from __future__ import annotations

import fcntl
import os
import plistlib
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import config

LABEL = "local.digital-fte.refresh"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG = config.HOME_DIR / "refresh.log"


@contextmanager
def file_lock(name: str, wait: bool = True):
    """Cross-process lock, so the nightly job and the dashboard server never scan
    or call the AI service at the same time. Yields False if `wait` is off and the
    lock is held elsewhere."""
    config.HOME_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(config.HOME_DIR / f".{name}.lock", "w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        fh.close()


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}", flush=True)


def last_scan() -> float | None:
    from . import db
    con = db.connect(config.DB_PATH)
    try:
        row = con.execute("select v from meta where k='last_scan'").fetchone()
        return float(row[0]) if row else None
    finally:
        con.close()


def refresh(if_older_than_hours: float | None = None) -> None:
    from . import ai
    from .scan import scan

    if if_older_than_hours is not None:
        last = last_scan()
        if last and time.time() - last < if_older_than_hours * 3600:
            log(f"skipped: last scan {(time.time() - last) / 3600:.1f}h ago")
            return
    log("scan started")
    results = scan()
    log("scan done: " + ", ".join(f"{r.agent} {r.updated} updated" for r in results if r.found))
    cfg = config.load()
    if cfg["ai"].get("use", "off") != "off":
        out = ai.run(config.DB_PATH, cfg)
        log(f"ai: {out}")


# ---------- launchd ----------

def install(at: str = "20:00") -> Path:
    hour, minute = (int(x) for x in at.split(":"))
    config.HOME_DIR.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "digital_fte", "refresh", "--if-older-than", "12"],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": True,                 # catch up at login after the Mac was shut down
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
        # launchd starts with a bare environment; `security` (Keychain) and `git` live here.
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
                                 "DFTE_HOME": str(config.HOME_DIR)},
        "ProcessType": "Background",       # low priority: never competes with what you're doing
        "LowPriorityIO": True,
        "Nice": 10,
    }
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    if PLIST.exists():
        remove()
    with PLIST.open("wb") as fh:
        plistlib.dump(plist, fh)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)], check=True)
    return PLIST


def remove() -> bool:
    if not PLIST.exists():
        return False
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST)], capture_output=True)
    PLIST.unlink()
    return True


def status() -> dict:
    """Is the job installed, when does it run, and how did the last run go."""
    out = {"installed": PLIST.exists(), "at": None, "loaded": False, "last_log": None}
    if out["installed"]:
        with PLIST.open("rb") as fh:
            sci = plistlib.load(fh).get("StartCalendarInterval", {})
        out["at"] = f"{sci.get('Hour', 0):02d}:{sci.get('Minute', 0):02d}"
        r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True)
        out["loaded"] = r.returncode == 0
    if LOG.exists():
        lines = [ln for ln in LOG.read_text(errors="replace").splitlines() if ln.strip()]
        out["last_log"] = lines[-1] if lines else None
    return out
