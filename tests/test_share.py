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


def test_virtual_team_and_role_costs():
    cfg = {"dfte": {"hours_per_fte_month": 100, "human_cost_per_month": 9000},
           "roles": [{"name": "Dev", "monthly_cost": 8000}, {"name": "Presales", "monthly_cost": 12000}]}
    tasks = [{"net": 50, "roles": [{"role": "Dev", "share": 1.0}]},
             {"net": 50, "roles": [{"role": "Presales", "share": 0.6}, {"role": "Dev", "share": 0.4}]},
             {"net": 10, "roles": [{"role": "Retired", "share": 1.0}]}]       # not in the catalog: default cost
    team = share.team_of(tasks, cfg)
    assert [(n, round(v, 2), c) for n, v, c in team["roles"]] == [("Dev", 0.7, 8000), ("Presales", 0.3, 12000),
                                                                  ("Retired", 0.1, 9000)]
    assert team["value"] == pytest.approx(0.7 * 8000 + 0.3 * 12000 + 0.1 * 9000)
    assert team["per_person"] == pytest.approx(team["value"] / 1.1)
    assert share.team_of([], cfg)["per_person"] == 9000


def test_summary_shows_virtual_team(home):
    con = db.connect(config.DB_PATH)
    add_task(con, "Prepare the Acme renewal deck", "Acme (Singapore)")
    estimate.rebuild(con, config.load())
    con.close()
    html = share.render(share.build(), "Sam")
    assert "Virtual team" in html and "Presales consultant" in html and "would cost about" in html
    assert "By kind of work" not in html and "Acme" not in html


def test_team_series_keeps_top_roles_and_sums_the_rest(monkeypatch):
    monkeypatch.setattr(share, "TEAM_COLORS", 2)
    rows = [{"month": "2026-09", "team": {"A": 1.0, "B": 0.5, "C": 0.2}},
            {"month": "2026-10", "team": {"A": 2.0, "D": 0.4}}]
    assert share.team_series(rows) == [("A", [1.0, 2.0]), ("B", [0.5, 0.0]), ("Other", [0.2, 0.4])]
    svg = share._team_chart(rows)
    assert svg.count("<rect") == 5 and "Other" in svg and 'aria-label="Virtual team per month"' in svg
    assert share._team_chart([]) == ""
