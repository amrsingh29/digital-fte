"""Phase 5: the shared summary never carries task-level detail; the refresh job's timing."""

import time
from datetime import datetime

import pytest

from digital_fte import config, db, estimate, jobs, share
from digital_fte.collectors.base import Event, Session


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "fte.db")
    config.ensure()
    config.set_ai_use("off")
    return tmp_path


def add_task(con, title, project):
    now = time.time()
    s = Session(agent="cline", native_id=title, source=f"cline:{title}", project=project, events=[
        Event(ts=now - 600, kind="prompt", text=title),
        Event(ts=now - 500, kind="action", tool="editor", target=f"/secret/{project}/plan.md", lines_added=200),
        Event(ts=now - 400, kind="reply", text="Done")])
    db.replace_source(con, s.source, s.agent, "v", [s])


def test_summary_has_totals_but_no_task_details(home):
    con = db.connect(config.DB_PATH)
    add_task(con, "Prepare the Acme renewal deck", "Acme (Singapore)")
    add_task(con, "Draft Globex pricing memo", "Globex")
    estimate.rebuild(con, config.load())
    con.close()

    s = share.build()
    html = share.render(s, "Sam")
    assert s["months"] and s["months"][-1]["tasks"] == 2
    for secret in ("Acme", "Globex", "renewal", "pricing", "plan.md", "/secret"):
        assert secret not in html
    assert "<title>Sam's Digital FTE</title>" in html and "Cline" in html
    assert share.page(s).startswith("<!doctype html>")


def test_current_month_cost_counts_days_so_far():
    now = datetime(2026, 10, 12)
    assert share._cost_share("2026-10", now) == pytest.approx(12 / 31)
    assert share._cost_share("2026-09", now) == 1.0


def test_refresh_skips_when_recent(home, capsys):
    con = db.connect(config.DB_PATH)
    con.execute("insert or replace into meta values ('last_scan', ?)", (str(time.time() - 3600),))
    con.commit()
    con.close()
    jobs.refresh(if_older_than_hours=12)
    assert "skipped: last scan 1.0h ago" in capsys.readouterr().out


def test_file_lock_is_exclusive(home):
    with jobs.file_lock("x") as first:
        assert first
        with jobs.file_lock("x", wait=False) as second:
            assert second is False
