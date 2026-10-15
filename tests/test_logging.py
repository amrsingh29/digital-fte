"""When the AI service is unreachable, a run stops and leaves tasks pending; app.log
records scans and AI runs without any prompt or reply text."""

from digital_fte import ai, applog, config, db, estimate, jobs
from digital_fte.collectors.base import Event, Session


def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "fte.db")
    con = db.connect(config.DB_PATH)
    for i in range(4):
        s = Session(agent="cline", native_id=f"s{i}", source=f"cline:s{i}", project="p", events=[
            Event(ts=1_790_000_000 + i * 10_000, kind="prompt", text=f"SECRET-PROMPT-{i}"),
            Event(ts=1_790_000_060 + i * 10_000, kind="reply", text="SECRET-REPLY")])
        db.replace_source(con, s.source, s.agent, "v", [s])
    cfg = config.load()
    cfg["ai"]["use"] = "local"
    cfg["ai"]["local"] = {"api": "openai", "base_url": "http://localhost:1/v1", "model": "m", "parallel": 1}
    estimate.rebuild(con, cfg)
    con.close()
    return cfg


def test_unreachable_stops_the_run_and_leaves_tasks_pending(tmp_path, monkeypatch):
    cfg = _home(tmp_path, monkeypatch)
    calls = []

    def down(svc, summary, roles, examples=()):
        calls.append(1)
        raise ai.Unreachable("connection failed: nodename nor servname provided")
    monkeypatch.setattr(ai, "estimate_one", down)
    out = ai.run(config.DB_PATH, cfg)
    assert out["unreachable"] and out["errors"] == 0 and len(calls) == 1
    con = db.connect(config.DB_PATH)
    assert con.execute("select count(*) from ai_estimates").fetchone()[0] == 0          # nothing marked failed
    assert len(ai.pending(con, "local", "m", 10)) == 4                                    # all retried next run
    assert "unreachable" in ai.progress["note"]
    log = (tmp_path / "app.log").read_text()
    assert "unreachable" in log and "SECRET" not in log


def test_real_errors_are_still_recorded(tmp_path, monkeypatch):
    cfg = _home(tmp_path, monkeypatch)

    def bad(svc, summary, roles, examples=()):
        raise ai.AIError("HTTP 401: bad key")
    monkeypatch.setattr(ai, "estimate_one", bad)
    out = ai.run(config.DB_PATH, cfg)
    assert out["errors"] == 4 and not out.get("unreachable")
    assert "HTTP 401" in (tmp_path / "app.log").read_text()


def test_log_rotates_and_job_lines_reach_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME_DIR", tmp_path)
    jobs.log("scan started")
    handler = applog.get().handlers[0]
    assert handler.maxBytes == 1_000_000 and handler.backupCount == 3
    assert "daily refresh: scan started" in (tmp_path / "app.log").read_text()
